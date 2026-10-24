from __future__ import annotations

import ast
import os
import subprocess
from collections import defaultdict
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatch
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .advanced_rules import AdvancedRuleEvaluator
from .core_rule_contract import CoreQualityRule
from .config import GuardConfig, is_test_path, is_test_source_path
from .facts import FactsCollector, ModuleFacts
from .model import Definition, Finding, ScanReport, Usage
from .python_import_resolution import resolve_local_import_symbol
from .rules import RuleEvaluator
from .topology_facts import (
    RepositoryTopology,
    UsageEdge,
    python_topology,
    python_usage_edge,
)


def snapshot_only_rule_findings(
    analysis_snapshot: RepositoryAnalysisSnapshot,
) -> tuple[tuple[Finding, ...], ...]:
    """Evaluate topology-independent rules over one immutable source snapshot.

    Args:
        analysis_snapshot: Parsed source units and path order; callers own
            repository-wide topology and selected-file policy separately.

    Returns:
        Four separately grouped Finding tuples for partial commits,
        constructor read-order, exception handlers and source-bound calls.
    """
    from .partial_commit_flow import partial_commit_findings
    from .constructor_read_order import constructor_read_order_findings
    from .exception_handler_rule_objects import exception_handler_rule_findings
    from .source_call_contracts import source_bound_call_findings

    return (
        tuple(partial_commit_findings(analysis_snapshot)),
        tuple(constructor_read_order_findings(analysis_snapshot)),
        tuple(
            item
            for group in exception_handler_rule_findings(analysis_snapshot)
            for item in group
        ),
        tuple(source_bound_call_findings(analysis_snapshot)),
    )


class RepositoryInstructionUniquenessRule(CoreQualityRule[Path]):
    """Detect noncanonical instructions anywhere below a repository root.

    QG187 checks physical paths, including Git-ignored or untracked instruction files.
    """

    code = "QG187"

    def evaluate(self, root: Path) -> list[Finding]:
        """Find secondary AGENTS.md instruction files on disk, including ignored ones.

        Args:
            root: Filesystem root of the inspected repository; .git trees and
                symlinked directory traversal are excluded.

        Returns:
            QG187 Critical Findings for every instruction file outside the one
            permitted root AGENTS.md, ordered by repository-relative path.
        """
        candidates: list[Path] = []
        for current_root, directories, filenames in os.walk(root, followlinks=False):
            directories[:] = [name for name in directories if name != ".git"]
            current = Path(current_root)
            for filename in filenames:
                if filename.lower() != "agents.md":
                    continue
                relative = (current / filename).relative_to(root)
                if relative != Path("AGENTS.md"):
                    candidates.append(relative)
        return [
            Finding(
                code=self.code,
                severity="critical",
                confidence="high",
                path=relative.as_posix(),
                line=1,
                column=1,
                message=(
                    "仓库存在根目录唯一 AGENTS.md 之外的指令文件；"
                    "无论是否为历史存量、未跟踪或被忽略文件都绝对阻断。"
                ),
                suggestion=(
                    "删除该文件；把仍然有效且不冲突的项目约束集中到根 AGENTS.md，"
                    "仓库只允许一个根目录指令文件。"
                ),
                source="quality-guard",
            )
            for relative in sorted(set(candidates))
        ]


class RepositoryScanner:
    """
    协调文件发现、AST 解析、引用解析和规则评估。

    扫描器负责流程编排，具体事实收集和规则判断由独立组件承担。
    """

    def __init__(
        self,
        root: Path,
        config: GuardConfig,
        analysis_snapshot: RepositoryAnalysisSnapshot | None = None,
        topology: RepositoryTopology | None = None,
        shared_snapshot_findings: tuple[tuple[Finding, ...], ...] | None = None,
    ) -> None:
        """Prepare one repository scanner with optional shared parsing and topology facts.

        Args:
            root: Root path of the Git repository being scanned.
            config: Rules, scope and Profile policy already selected by the CLI.
            analysis_snapshot: Optional immutable source/AST snapshot to avoid reparsing.
            topology: Optional normalized cross-module usage and inheritance relations.
            shared_snapshot_findings: Optional already computed groups of source-only
                findings, shared between worktree and baseline scans.

        Returns:
            None; stores its inputs for subsequent source collection and rule evaluation.
        """
        self.root = root.resolve()
        self.config = config
        self.analysis_snapshot = analysis_snapshot
        self.topology = topology
        self.shared_snapshot_findings = shared_snapshot_findings

    def scan(self, selected_files: set[Path] | None = None) -> ScanReport:
        """
        扫描仓库并生成包含覆盖率的完整报告。

        Args:
            selected_files: 显式限定的待扫描文件集合；为空时扫描全部候选文件。

        Returns:
            包含静态发现和 docstring 覆盖率的扫描报告。
        """
        files = list(self.discover_python_files())
        if selected_files is not None:
            normalized = {path.resolve() for path in selected_files}
            files = [path for path in files if path.resolve() in normalized]
        facts = self._parse_files(files)
        for item in facts:
            item.repository_root = self.root
        definitions = [definition for item in facts for definition in item.definitions]
        usages = [usage for item in facts for usage in item.usages]
        resolved_edges = self._resolve_usage(definitions, usages, facts)
        if self.topology is None:
            self.topology = python_topology(definitions, resolved_edges)
        contract_facts = {contract for item in facts for contract in item.contracts}
        if contract_facts:
            self.topology = replace(
                self.topology,
                contracts=tuple(
                    sorted(
                        {*self.topology.contracts, *contract_facts},
                        key=lambda item: (
                            item.path.as_posix(),
                            item.line,
                            item.receiver,
                            item.operation,
                            item.selector,
                        ),
                    )
                ),
            )
        findings = [finding for item in facts for finding in item.findings]
        from .case_contract_rules import case_contract_findings

        # Case entrypoint authentication needs the same cross-module graph as
        # all other R1/R2/R3 rules. Run after topology construction, reusing
        # the already parsed AST and never constructing a second graph.
        for item in facts:
            if item.tree is None:
                continue
            if is_test_source_path(item.path, self.config.test_roots):
                findings.extend(
                    case_contract_findings(
                        item.path, item.tree, self.root, topology=self.topology,
                        test_roots=self.config.test_roots,
                    )
                )
        if self.analysis_snapshot is not None and self.topology is not None:
            from .typed_producer_contract import typed_producer_contract_findings

            audited_paths = {path.relative_to(self.root).as_posix() for path in files}
            findings.extend(
                item
                for item in typed_producer_contract_findings(
                    self.analysis_snapshot, self.topology
                )
                if item.path in audited_paths
            )
        if self.analysis_snapshot is not None:
            source_findings = (
                self.shared_snapshot_findings
                if self.shared_snapshot_findings is not None
                else snapshot_only_rule_findings(self.analysis_snapshot)
            )
            audited_paths = {path.relative_to(self.root).as_posix() for path in files}
            for group in source_findings:
                findings.extend(item for item in group if item.path in audited_paths)
        # Conditional CFG witnesses remain OPEN_WORLD rather than confirmed exceptions.
        if self.topology is not None:
            from .handler_read_rule import (
                ExceptionHandlerReadRule,
                ExceptionReadRuleContext,
            )

            audited_paths = frozenset(
                path.relative_to(self.root).as_posix() for path in files
            )
            findings.extend(
                ExceptionHandlerReadRule().evaluate(
                    ExceptionReadRuleContext(self.topology, audited_paths)
                )
            )
        definition_evaluator = RuleEvaluator(self.config, self.topology)
        findings.extend(definition_evaluator.evaluate(definitions, facts))
        advanced = AdvancedRuleEvaluator(self.config)
        findings.extend(advanced.evaluate(definitions, facts))
        if self.analysis_snapshot is not None and self.topology is not None:
            from .tool_return_graph_rules import (
                ToolConsumerContractRule,
                ToolGraphContext,
                tool_result_bindings,
            )
            from .visitor_rule_objects import MissingMappingKeyRule

            bindings = tool_result_bindings(
                ToolGraphContext(
                    self.analysis_snapshot,
                    self.topology,
                    self.config,
                    frozenset(advanced.signals.registered_tools),
                    frozenset(path.relative_to(self.root).as_posix() for path in files),
                )
            )
            from .typed_consumers import typed_required_get_findings

            for candidate in typed_required_get_findings(self.topology, audited_paths):
                if not any(
                    previous.code == candidate.code
                    and previous.path == candidate.path
                    and previous.line == candidate.line
                    for previous in findings
                ):
                    findings.append(candidate)
            findings.extend(ToolConsumerContractRule().evaluate(bindings))
            for item in MissingMappingKeyRule().evaluate_tool_bindings(bindings):
                if not any(
                    old.code == item.code
                    and old.path == item.path
                    and old.line == item.line
                    for old in findings
                ):
                    findings.append(item)
        if not self.config.include_tests:
            findings.extend(RepositoryInstructionUniquenessRule().evaluate(self.root))
        inherited_docstrings = definition_evaluator.documented_overrides
        docstring_definitions = [
            definition
            for definition in definitions
            if definition.symbol_id not in inherited_docstrings
            and not (
                self.config.skip_private_docstrings
                and definition.name.startswith("_")
                and not (
                    definition.name.startswith("__") and definition.name.endswith("__")
                )
            )
        ]
        documented_definitions = sum(
            definition.has_docstring for definition in docstring_definitions
        )
        complete_docstrings = sum(
            self._has_complete_docstring(definition)
            for definition in docstring_definitions
        )
        findings.sort(key=lambda item: (item.path, item.line, item.column, item.code))
        return ScanReport(
            root=self.root,
            files_scanned=len(files),
            findings=findings,
            definitions=len(definitions),
            docstring_definitions=len(docstring_definitions),
            documented_definitions=documented_definitions,
            complete_docstrings=complete_docstrings,
            project_name=self.config.project_name,
            profile_source=self.config.profile_source,
            profile_capabilities=tuple(self.config.profile_capabilities),
            commit_policy=dict(self.config.profile_commit_policy),
        )

    def _has_complete_docstring(self, definition: Definition) -> bool:
        """
        判断定义是否满足公开/非公开差异化 docstring 完整性。

        Args:
            definition: 待评估的定义。

        Returns:
            非公开结构有 docstring 即视为完整，公开接口必须满足多行和章节要求。
        """
        private_name = str(definition.path).startswith("tests/") or (
            definition.name.startswith("_")
            and not (
                definition.name.startswith("__") and definition.name.endswith("__")
            )
        )
        property_interface = any(
            decorator.rsplit(".", 1)[-1]
            in {"property", "cached_property", "setter", "deleter"}
            for decorator in definition.decorators
        )
        if private_name or property_interface:
            return definition.has_docstring
        return definition.has_complete_docstring(
            self.config.docstring_min_lines,
            self.config.require_docstring_sections,
        )

    def _parse_files(self, files: list[Path]) -> list[ModuleFacts]:
        """
        并发解析 Python 文件并保持报告顺序稳定。

        Args:
            files: 已过滤且排序后的 Python 文件列表。

        Returns:
            与输入文件顺序一致的模块事实列表。
        """
        file_count = len(files)
        if file_count <= 1:
            return [self._parse_file(path) for path in files]
        configured_jobs = self.config.jobs
        cpu_default = min(32, (os.cpu_count() or 1) + 4)
        jobs = configured_jobs or cpu_default
        bounded_jobs = max(1, min(jobs, file_count))
        if bounded_jobs == 1:
            return [self._parse_file(path) for path in files]
        with ThreadPoolExecutor(max_workers=bounded_jobs) as executor:
            parsed = executor.map(self._parse_file, files)
            return list(parsed)

    def changed_files(self, revision: str) -> set[Path]:
        """
        读取指定 Git 修订范围内变更的 Python 文件。

        Args:
            revision: 作为 Git diff 基准的修订名称。

        Returns:
            变更范围内 Python 文件的绝对路径集合。
        """
        command = ["git", "diff", "--name-only", f"{revision}...HEAD", "--", "*.py"]
        result = subprocess.run(
            command,
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "无法读取 Git 变更文件")
        return {self.root / line for line in result.stdout.splitlines() if line.strip()}

    def discover_python_files(self) -> tuple[Path, ...]:
        """发现当前配置允许扫描的 Python 文件。

        Returns:
            已应用 tests 与 exclude 配置、去重并排序后的稳定文件元组。
        """
        if self.analysis_snapshot is not None:
            result: list[Path] = []
            for relative in self.analysis_snapshot.paths:
                if not self.config.include_tests and is_test_path(
                    relative, self.config.project_name, self.config.test_roots
                ):
                    continue
                if any(fnmatch(relative, pattern) for pattern in self.config.exclude):
                    continue
                result.append(self.root / relative)
            return tuple(result)
        git_command = [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "*.py",
        ]
        git_result = subprocess.run(
            git_command,
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
        if git_result.returncode == 0:
            candidates = [
                self.root / line
                for line in git_result.stdout.splitlines()
                if line.strip()
            ]
        else:
            candidates = list(self.root.rglob("*.py"))
        result: list[Path] = []
        for path in candidates:
            if not path.is_file():
                continue
            relative = path.relative_to(self.root).as_posix()
            if not self.config.include_tests and is_test_path(
                relative, self.config.project_name, self.config.test_roots
            ):
                continue
            if any(fnmatch(relative, pattern) for pattern in self.config.exclude):
                continue
            result.append(path)
        return tuple(sorted(set(result)))

    def _parse_file(self, path: Path) -> ModuleFacts:
        """
        解析单个 Python 文件并收集模块事实。

        Args:
            path: 目标路径。

        Returns:
            解析成功或包含语法错误发现的模块事实。
        """
        relative = path.relative_to(self.root)
        module_parts = list(relative.with_suffix("").parts)
        if module_parts and module_parts[0] == "src":
            module_parts = module_parts[1:]
        if module_parts and module_parts[-1] == "__init__":
            module_parts = module_parts[:-1]
        module = ".".join(module_parts)
        snapshot_unit = (
            self.analysis_snapshot.unit(relative.as_posix())
            if self.analysis_snapshot is not None
            else None
        )
        source = (
            snapshot_unit.source
            if snapshot_unit is not None
            else path.read_text(encoding="utf-8")
        )
        facts = ModuleFacts(path=relative, module=module, source=source)
        if snapshot_unit is not None and snapshot_unit.syntax_error is not None:
            line, column, message = snapshot_unit.syntax_error
            facts.findings.append(
                Finding(
                    code="QG000",
                    severity="error",
                    confidence="high",
                    path=str(relative),
                    line=line,
                    column=column,
                    message=f"Python 语法解析失败: {message}",
                )
            )
            return facts
        if snapshot_unit is not None and snapshot_unit.tree is not None:
            tree = snapshot_unit.tree
        else:
            try:
                tree = ast.parse(source, filename=str(relative), type_comments=True)
            except SyntaxError as error:
                facts.findings.append(
                    Finding(
                        code="QG000",
                        severity="error",
                        confidence="high",
                        path=str(relative),
                        line=error.lineno or 1,
                        column=error.offset or 1,
                        message=f"Python 语法解析失败: {error.msg}",
                    )
                )
                return facts
        facts.tree = tree
        collector = FactsCollector(facts, self.config, self.topology)
        collector.visit(tree)
        return facts

    def _resolve_usage(
        self,
        definitions: list[Definition],
        usages: list[Usage],
        facts: list[ModuleFacts],
    ) -> list[UsageEdge]:
        """
        把静态引用解析到仓库内定义、累计次数并返回归一化使用边。

        Args:
            definitions: 仓库内已收集的定义列表。
            usages: 仓库内收集的引用列表。
            facts: 当前模块事实或模块事实列表。

        Returns:
            已解析且类型化的仓库内使用边。
        """
        resolved_edges: list[UsageEdge] = []
        by_id = {definition.symbol_id: definition for definition in definitions}
        by_module_name: dict[tuple[str, str], list[Definition]] = defaultdict(list)
        by_simple_name: dict[str, list[Definition]] = defaultdict(list)
        imports_by_module = {item.module: item.imports for item in facts}
        for definition in definitions:
            by_module_name[(definition.module, definition.name)].append(definition)
            by_simple_name[definition.name].append(definition)
        for usage in usages:
            candidates = self._usage_candidates(
                usage,
                definitions,
                by_id,
                by_module_name,
                by_simple_name,
                imports_by_module,
            )
            for definition in candidates:
                if usage.is_call:
                    definition.calls += 1
                else:
                    definition.references += 1
                if self.topology is None:
                    resolved_edges.append(python_usage_edge(usage, definition))
        return resolved_edges

    def _usage_candidates(
        self,
        usage: Usage,
        definitions: list[Definition],
        by_id: dict[str, Definition],
        by_module_name: dict[tuple[str, str], list[Definition]],
        by_simple_name: dict[str, list[Definition]],
        imports_by_module: dict[str, dict[str, str]],
    ) -> list[Definition]:
        """
        根据引用形态选择可能匹配的定义。

        Args:
            usage: 待解析的静态引用。
            definitions: 仓库内已收集的定义列表。
            by_id: 按完整符号标识建立的定义索引。
            by_module_name: 按模块和简单名称建立的定义索引。
            by_simple_name: 按简单名称建立的定义索引。
            imports_by_module: 各模块的本地导入名称映射。

        Returns:
            与引用最可能对应的定义列表。
        """
        if usage.base in {"self", "cls"}:
            return self._self_usage_candidates(usage, definitions, by_id)
        if usage.base:
            return self._attribute_usage_candidates(
                usage, by_id, by_simple_name, imports_by_module
            )
        return self._name_usage_candidates(
            usage, by_id, by_module_name, by_simple_name, imports_by_module
        )

    def _self_usage_candidates(
        self,
        usage: Usage,
        definitions: list[Definition],
        by_id: dict[str, Definition],
    ) -> list[Definition]:
        """
        解析 self 或 cls 方法引用。

        Args:
            usage: 待解析的静态引用。
            definitions: 仓库内已收集的定义列表。
            by_id: 按完整符号标识建立的定义索引。

        Returns:
            匹配到的方法定义列表。
        """
        exact_id = (
            f"{usage.module}.{usage.owner_class}.{usage.target}"
            if usage.owner_class
            else ""
        )
        if exact_id and exact_id in by_id:
            return [by_id[exact_id]]
        return [
            definition
            for definition in definitions
            if definition.module == usage.module
            and definition.kind == "method"
            and definition.name == usage.target
        ]

    def _attribute_usage_candidates(
        self,
        usage: Usage,
        by_id: dict[str, Definition],
        by_simple_name: dict[str, list[Definition]],
        imports_by_module: dict[str, dict[str, str]],
    ) -> list[Definition]:
        """
        解析模块、类或导入对象的属性引用。

        Args:
            usage: 待解析的静态引用。
            by_id: 按完整符号标识建立的定义索引。
            by_simple_name: 按简单名称建立的定义索引。
            imports_by_module: 各模块的本地导入名称映射。

        Returns:
            匹配到的属性目标定义列表。
        """
        imports = imports_by_module[usage.module]
        base_root = usage.base.split(".", 1)[0]
        if base_root in imports:
            imported_base = imports[base_root]
            suffix = usage.base[len(base_root) :].lstrip(".")
            qualified_base = imported_base + (f".{suffix}" if suffix else "")
            candidate_id = f"{qualified_base}.{usage.target}"
            resolved = resolve_local_import_symbol(
                candidate_id, imports_by_module, by_id
            )
            if resolved is not None:
                return [by_id[resolved]]
        class_candidate = f"{usage.module}.{usage.base}.{usage.target}"
        if class_candidate in by_id:
            return [by_id[class_candidate]]
        return (
            by_simple_name[usage.target]
            if len(by_simple_name[usage.target]) == 1
            else []
        )

    def _name_usage_candidates(
        self,
        usage: Usage,
        by_id: dict[str, Definition],
        by_module_name: dict[tuple[str, str], list[Definition]],
        by_simple_name: dict[str, list[Definition]],
        imports_by_module: dict[str, dict[str, str]],
    ) -> list[Definition]:
        """
        解析无接收者的名称引用。

        Args:
            usage: 待解析的静态引用。
            by_id: 按完整符号标识建立的定义索引。
            by_module_name: 按模块和简单名称建立的定义索引。
            by_simple_name: 按简单名称建立的定义索引。
            imports_by_module: 各模块的本地导入名称映射。

        Returns:
            匹配到的名称目标定义列表。
        """
        imports = imports_by_module[usage.module]
        if usage.target in imports:
            resolved = resolve_local_import_symbol(
                imports[usage.target], imports_by_module, by_id
            )
            if resolved is not None:
                return [by_id[resolved]]

        scope = usage.owner_qualname
        while scope and scope != usage.owner_class:
            lexical_id = f"{usage.module}.{scope}.{usage.target}"
            if lexical_id in by_id:
                return [by_id[lexical_id]]
            scope = scope.rpartition(".")[0]

        module_id = f"{usage.module}.{usage.target}"
        if module_id in by_id:
            return [by_id[module_id]]
        return []
