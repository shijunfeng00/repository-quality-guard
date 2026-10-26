"""Source-bound checks for observable TestCase registrations and oracles.

Only explicit imports of the published framework authorize test-specific
findings. Identical user-defined class names do not authorize a finding.
"""

from __future__ import annotations

import ast
import os
from importlib.util import resolve_name
from pathlib import Path
from dataclasses import dataclass, replace

from .core_rule_contract import CoreQualityRule
from .facts import ModuleFacts
from .case_contract_source_identity import (
    bound_names,
    entrypoint_call_witness,
    entrypoint_return_bindings,
    stable_reference,
    straight_line_local_imports,
)
from .config import is_test_source_path

from .model import Finding
from .topology_facts import RepositoryTopology, UsageKind, Visibility

_FRAMEWORK = "rqgtest"


def is_case_test_path(path: Path) -> bool:
    """Check the configured test-directory contract through the core classifier.

    Args:
        path: Repository-relative Python source file to classify.

    Returns:
        Whether the file resides inside an authenticated unit-test root.
    """
    return is_test_source_path(path)


_ROOT_METHODS = {
    "TestCase": frozenset(
        {"get_enterpoint", "get_input_data", "get_output_data", "equals"}
    ),
    "StatefulTestCase": frozenset(
        {"get_enterpoint", "get_input_data", "get_output_data", "equals"}
    ),
}


class _CachedParents(dict[ast.AST, ast.AST]):
    """Cache scope bindings only for one immutable test AST analysis.

    This definition supplies reusable typed behavior to its callers."""

    def __init__(self, relations: dict[ast.AST, ast.AST]) -> None:
        """Construct parent links and an initially empty lexical binding cache.

        Args:
            relations: Parent AST node indexed by each child from the same
                parsed module; no child node is reparsed or reparented.

        Returns:
            None; bound_names() populates the cache only when queried.
        """
        super().__init__(relations)
        self._bindings: dict[ast.AST, set[str]] = {}

    def bound_names(
        self, scope: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> set[str]:
        """Return lexical bindings, reusing the initial walk for each AST scope.

        Ignore nested definitions' internal names so unrelated inner bindings
        cannot be mistaken for local shadows of an authenticated import.

        Args:
            scope: Function, async function or class whose immediate body
                contributes names to the shadowing and import-binding rules.

        Returns:
            Names bound directly in the requested lexical scope.
        """
        if scope not in self._bindings:
            self._bindings[scope] = bound_names(scope.body, skip_nested=True)
        return self._bindings[scope]


def case_contract_findings(
    path: Path,
    tree: ast.Module,
    repository_root: Path | None = None,
    topology: RepositoryTopology | None = None,
    test_roots: tuple[str, ...] = (),
) -> list[Finding]:
    """Check registered test class contracts only with authenticated imports.

    Tests that merely reuse framework-like class or decorator names are not
    enough evidence. Shadowed imports and opaque callbacks remain unresolved.

    Args:
        path: Repository path for the file under analysis and Finding location.
        tree: Already parsed module whose imports and test classes are checked.
        repository_root: Repository source boundary for authenticated entrypoint origins.
        topology: Shared, already built repository relation graph, when available.
        test_roots: Additional explicitly configured repository-relative test roots.

    Returns:
        Proven TestUnit contract findings for directly bound framework APIs;
        ambiguous or unrelated local class definitions produce no finding.
    """
    aliases: dict[str, str] = {}
    module_aliases: dict[str, str] = {}
    certified_imports: set[ast.stmt] = set()
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module in {_FRAMEWORK, "rqgtest.core.unit"}:
            for alias in statement.names:
                if (alias.name in {*_ROOT_METHODS, "TestUnit", "ITestCase"}
                        and (statement.module == _FRAMEWORK or alias.name == "TestUnit")):
                    aliases[alias.asname or alias.name] = alias.name
                    certified_imports.add(statement)
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name in {_FRAMEWORK, "rqgtest.core.unit"}:
                    root = alias.asname or alias.name.split(".")[0]
                    module_aliases[root] = alias.name if alias.asname else _FRAMEWORK
                    certified_imports.add(statement)
    # Abstract decorators must also have authenticated provenance: an unrelated
    # decorator named "abstractmethod" does not make a Case abstract.
    abstract_names: set[str] = set()
    abc_modules: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module == "abc":
            abstract_names.update(
                alias.asname or alias.name
                for alias in statement.names
                if alias.name == "abstractmethod"
            )
        elif isinstance(statement, ast.Import):
            abc_modules.update(
                alias.asname or "abc"
                for alias in statement.names
                if alias.name == "abc"
            )
    abc_shadowed: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module == "abc":
            abc_shadowed.update(
                alias.asname or alias.name
                for alias in statement.names
                if alias.name != "abstractmethod"
            )
        elif isinstance(statement, ast.Import):
            abc_shadowed.update(
                alias.asname or alias.name.split(".")[0]
                for alias in statement.names
                if alias.name != "abc"
            )
        else:
            abc_shadowed.update(bound_names([statement], skip_nested=True))
    # Two distinct canonical exports assigned the same local identifier are
    # ambiguous as well, so neither is certified as an abstract decorator.
    abc_shadowed.update(abstract_names & abc_modules)
    abstract_names.difference_update(abc_shadowed)
    abc_modules.difference_update(abc_shadowed)
    builtin_shadowed = bound_names(tree.body, skip_nested=True)
    property_names: set[str] = set()
    builtins_modules: set[str] = set()
    property_shadowed: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module == "builtins":
            for alias in statement.names:
                local = alias.asname or alias.name
                if alias.name == "property":
                    property_names.add(local)
                else:
                    property_shadowed.add(local)
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                local = alias.asname or alias.name.split(".")[0]
                if alias.name == "builtins":
                    builtins_modules.add(local)
                else:
                    property_shadowed.add(local)
        else:
            property_shadowed.update(bound_names([statement], skip_nested=True))
    property_shadowed.update(property_names & builtins_modules)
    property_names.difference_update(property_shadowed)
    builtins_modules.difference_update(property_shadowed)

    # Authentication is source-specific. Other imports or assignments that bind
    # the same symbol conservatively invalidate that framework provenance.
    shadowed = bound_names(
        [statement for statement in tree.body if statement not in certified_imports],
        skip_nested=True,
    )
    aliases = {
        name: meaning for name, meaning in aliases.items() if name not in shadowed
    }
    module_aliases = {
        name: meaning
        for name, meaning in module_aliases.items()
        if name not in shadowed
    }
    # An in-repository lookalike shadows the published rqgtest distribution.
    # A spelling match is not enough to authenticate framework identity.
    if repository_root is not None:
        search_dirs = (
            repository_root,
            path.parent if path.is_absolute() else repository_root / path.parent,
        )
        for directory in search_dirs:
            if (directory / "rqgtest.py").is_file() or (
                directory / "rqgtest" / "__init__.py"
            ).is_file():
                aliases.clear()
                module_aliases.clear()
                break
    parents = _CachedParents(
        {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    )
    context = TestContractContext(
        path=path,
        tree=tree,
        aliases=aliases,
        module_aliases=module_aliases,
        parents=parents,
        test_roots=test_roots,
        abstract_names=abstract_names,
        abc_modules=abc_modules,
        builtin_shadowed=builtin_shadowed,
        property_names=property_names,
        builtins_modules=builtins_modules,
        topology=topology,
    )
    findings = TestLayoutRule().evaluate(context)
    for statement in ast.walk(tree):
        node_context = replace(context, statement=statement)
        findings.extend(SelfRunTestUnitRule().evaluate(node_context))
        if not isinstance(statement, ast.ClassDef):
            continue
        bases = [
            _certified_symbol(base, aliases, module_aliases, parents)
            for base in statement.bases
        ]
        if len(bases) != 1 or bases[0] not in _ROOT_METHODS:
            continue
        declared = {
            method.name: method
            for method in statement.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        class_context = replace(node_context, declared=declared, base=bases[0])
        findings.extend(CaseInterfaceRule().evaluate(class_context))
        findings.extend(EmptyTestUnitRule().evaluate(class_context))
        findings.extend(_closed_case_body_findings(path, statement, bases[0], tree))
        findings.extend(
            _case_entrypoint_origin_findings(
                path, statement, declared, tree, repository_root, topology, test_roots
            )
        )
        findings.extend(AlwaysTrueOracleRule().evaluate(class_context))
        findings.extend(OracleIndependenceRule().evaluate(class_context))
        findings.extend(ProductionEntrypointIsolationRule().evaluate(class_context))
    return findings


@dataclass(frozen=True)
class TestContractContext:
    """Authenticated imports and AST are shared across all unit-test QG owners.

    Fields: path, tree, aliases, module_aliases, parents, abstract_names, abc_modules, builtin_shadowed."""

    path: Path
    tree: ast.Module
    aliases: dict[str, str]
    module_aliases: dict[str, str]
    parents: dict[ast.AST, ast.AST]
    test_roots: tuple[str, ...]
    abstract_names: set[str]
    abc_modules: set[str]
    builtin_shadowed: set[str]
    property_names: set[str]
    builtins_modules: set[str]
    statement: ast.AST | None = None
    declared: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] | None = None
    base: str = ""
    topology: RepositoryTopology | None = None


class TestLayoutRule(CoreQualityRule[TestContractContext]):
    """QG240 owns the closed Case module layout and forbidden assertions.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG240"

    def evaluate(self, context: TestContractContext) -> list[Finding]:
        """Evaluate QG240 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        path, tree = context.path, context.tree
        aliases, module_aliases, parents = (
            context.aliases,
            context.module_aliases,
            context.parents,
        )
        findings: list[Finding] = []
        is_unit = is_test_source_path(path, test_roots=context.test_roots)
        if is_unit and len(path.parts) > 1 and path.parts[-2] == "dataloaders":
            # Authenticated shared data readers have a separate, non-Case
            # structural contract. Never reclassify arbitrary helper packages.
            imported: set[str] = set()
            for statement in tree.body:
                if isinstance(statement, ast.ImportFrom) and statement.module in {
                    "rqgtest", "rqgtest.data.loader"
                }:
                    imported.update(
                        alias.asname or alias.name for alias in statement.names
                        if alias.name in {
                            "DataLoader", "JSONDataLoader", "JSONLDataLoader",
                            "CSVDataLoader", "PickleDataLoader"
                        }
                    )
            classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
            allowed = (
                len(classes) == 1
                and len(classes[0].bases) == 1
                and isinstance(classes[0].bases[0], ast.Name)
                and classes[0].bases[0].id in imported
                and all(
                    isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef))
                    or isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    for node in tree.body
                )
            )
            if allowed:
                return []
        if is_unit:
            valid_classes: set[ast.ClassDef] = set()
            for index, statement in enumerate(tree.body):
                if isinstance(statement, (ast.Import, ast.ImportFrom)):
                    continue
                if (
                    index == 0
                    and isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    continue  # module docstring, not test execution
                if isinstance(statement, ast.ClassDef):
                    # Public framework roots are final-with-direct-leaves at runtime.
                    bases = [
                        _certified_symbol(base, aliases, module_aliases, parents)
                        for base in statement.bases
                    ]
                    if (
                        len(statement.bases) == 1
                        and bases
                        and bases[0] in _ROOT_METHODS
                    ):
                        valid_classes.add(statement)
                    else:
                        findings.append(
                            _finding(
                                path,
                                statement,
                                "QG240",
                                "测试类必须直接继承来源已认证的标准 TestCase / StatefulTestCase；"
                                "禁止自定义非 Case 测试类和额外继承层。",
                                statement.name,
                            )
                        )
                    continue
                findings.append(
                    _finding(
                        path,
                        statement,
                        "QG240",
                        "单元测试文件仅允许导入、模块文档与标准 Case 子类；"
                        "禁止类外函数、变量、语句或执行入口。",
                        "unit-test-module",
                    )
                )
            if not valid_classes:
                findings.append(
                    _finding(
                        path,
                        tree.body[0] if tree.body else ast.Pass(lineno=1, col_offset=0),
                        "QG240",
                        "单元测试文件必须包含至少一个来源已认证、直接继承标准 Case 根类的具体测试类。",
                        "unit-test-module",
                    )
                )
            for statement in ast.walk(tree):
                if isinstance(statement, ast.Assert):
                    findings.append(
                        _finding(
                            path,
                            statement,
                            "QG240",
                            "测试文件禁止 assert；断言式结果裁决由标准 TestUnit.run 独占。",
                            "assert",
                        )
                    )
        return findings


_RQG_FROZEN_DIRECTORY_PATHS = frozenset({
    "agents/", "assets/", "docs/", "docs/api-reference/",
    "offline/", "offline/wheelhouse/", "profiles/",
    "profiles/council-of-harnesses/", "profiles/geek-ai-agent/",
    "profiles/geek-ai-rag/", "profiles/qg-example-profile/",
    "profiles/repository-quality-guard/", "references/", "runtime/",
    "runtime/contracts/", "runtime/contracts/c/", "runtime/contracts/cpp/",
    "runtime/contracts/go/", "runtime/contracts/java/",
    "runtime/contracts/typescript/", "runtime/src/", "scripts/", "templates/",
    "tests/", "tests/cases/", "tests/dataloaders/",
})


def repository_directory_layout_findings(
    root: Path,
    topology: RepositoryTopology | None = None,
    module_facts: list[ModuleFacts] | None = None,
) -> list[Finding]:
    """Apply RQG's frozen repository/test directory contract under QG240.

    This is intentionally enabled only by the RQG self-audit scanner call site;
    it does not impose RQG's private layout on unrelated repositories.
    """
    root = Path(root).resolve()
    actual: set[str] = set()
    for current, directories, files in os.walk(root):
        relative_parent = Path(current).relative_to(root)
        directories[:] = sorted(
            name for name in directories
            if name not in {".git", "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache", ".qg-work"}
        )
        # Private Profile internals are not part of the product-root directory
        # allowlist. Keep each direct profiles/<profile>/ directory visible,
        # then validate its optional tests/ tree under the same closed test
        # contract below instead of treating it as a root-level extra directory.
        if relative_parent.parts[:1] == ("profiles",) and len(relative_parent.parts) >= 2:
            directories[:] = []
            continue
        for name in directories:
            relative = (relative_parent / name).as_posix() + "/"
            actual.add(relative)

    findings: list[Finding] = []

    def add(path: str, violation: str, message: str) -> None:
        findings.append(Finding(
            code="QG240",
            severity="critical",
            confidence="high",
            path=path.rstrip("/"),
            line=1,
            column=1,
            symbol=path.rstrip("/"),
            message=message,
            suggestion="恢复冻结的仓库目录清单；新增、删除、重命名或嵌套目录必须先获得明确批准。",
            evidence={
                "framework": "rqgtest",
                "kind": "RQG_DIRECTORY_LAYOUT",
                "violation": violation,
                "relative_path": path.rstrip("/"),
                "absolute_blocker": True,
            },
            source="test-contract",
        ))

    for relative in sorted(actual - _RQG_FROZEN_DIRECTORY_PATHS):
        add(relative, "unexpected_directory", "仓库目录不在冻结清单内；禁止新增或嵌套未批准目录。")
    for relative in sorted(_RQG_FROZEN_DIRECTORY_PATHS - actual):
        add(relative, "missing_directory", "冻结的仓库目录缺失；禁止未经批准删除、重命名或重嵌套目录。")

    tests_root = root / "tests"
    if tests_root.is_dir():
        for item in sorted(tests_root.iterdir(), key=lambda path: path.name):
            if item.is_dir():
                if item.name in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache", ".qg-work"}:
                    continue  # Regenerable tooling/cache output is not repository source layout.
                if item.name not in {"cases", "dataloaders"}:
                    add(item.relative_to(root).as_posix(), "unexpected_tests_child", "tests/ 直接子目录只允许 cases/ 与 dataloaders/。")
            elif item.is_file() and not (item.name.startswith("test_") and item.suffix == ".py"):
                add(item.relative_to(root).as_posix(), "unexpected_tests_file", "tests/ 根目录只允许 test_*.py Case 模块及 cases/、dataloaders/。")

    dataloaders = tests_root / "dataloaders"
    if dataloaders.is_dir():
        for item in sorted(dataloaders.iterdir(), key=lambda path: path.name):
            if item.is_file() and item.suffix != ".py":
                add(item.relative_to(root).as_posix(), "non_python_dataloader_asset", "dataloaders/ 只允许符合协议的 Python DataLoader 模块；静态数据应放 cases/。")

    # Private Profiles have independent test roots: their fixtures and loaders
    # stay under profiles/<profile>/tests/, never in the public root tests/.
    # The rest of each private Profile tree is intentionally outside the frozen
    # product-root layout contract, but an existing tests/ tree must obey the
    # exact same Case/cases/DataLoader shape and rqgtest module contract.
    profiles_root = root / "profiles"
    known_fact_paths = {
        Path(item.path).resolve() if Path(item.path).is_absolute()
        else (root / item.path).resolve()
        for item in (module_facts or ())
    }
    if profiles_root.is_dir():
        for profile_root in sorted(
            (item for item in profiles_root.iterdir() if item.is_dir()),
            key=lambda path: path.name,
        ):
            profile_tests = profile_root / "tests"
            if not profile_tests.is_dir():
                continue
            for item in sorted(profile_tests.iterdir(), key=lambda path: path.name):
                relative = item.relative_to(root).as_posix()
                if item.is_dir():
                    if item.name in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache", ".qg-work"}:
                        continue
                    if item.name not in {"cases", "dataloaders"}:
                        add(relative, "unexpected_tests_child", "Profile tests/ 直接子目录只允许 cases/ 与 dataloaders/。")
                elif item.is_file() and not (item.name.startswith("test_") and item.suffix == ".py"):
                    add(relative, "unexpected_tests_file", "Profile tests/ 根目录只允许 test_*.py Case 模块及 cases/、dataloaders/。")

            profile_cases = profile_tests / "cases"
            if profile_cases.is_dir():
                for item in sorted(profile_cases.iterdir(), key=lambda path: path.name):
                    if item.is_dir() and item.name in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache", ".qg-work"}:
                        continue
                    if item.is_dir():
                        add(item.relative_to(root).as_posix(), "unexpected_directory", "Profile tests/cases/ 不允许新增或嵌套目录。")

            profile_dataloaders = profile_tests / "dataloaders"
            if profile_dataloaders.is_dir():
                for item in sorted(profile_dataloaders.iterdir(), key=lambda path: path.name):
                    relative = item.relative_to(root).as_posix()
                    if item.is_dir() and item.name in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache", ".qg-work"}:
                        continue
                    if item.is_dir():
                        add(relative, "unexpected_directory", "Profile tests/dataloaders/ 不允许新增或嵌套目录。")
                    elif item.is_file() and item.suffix != ".py":
                        add(relative, "non_python_dataloader_asset", "Profile tests/dataloaders/ 只允许 Python DataLoader 模块；静态数据应放本 Profile 的 cases/。")

            # Git ignores private Profile payloads, so parse their test modules
            # explicitly when they are absent from the ordinary scanner facts.
            # This preserves QG240-246 TestCase/DataLoader checks without
            # applying product-root layout rules to unrelated Profile folders.
            for test_file in sorted(profile_tests.rglob("*.py")):
                resolved = test_file.resolve()
                if resolved in known_fact_paths:
                    continue
                try:
                    tree = ast.parse(test_file.read_text(encoding="utf-8"), filename=str(test_file))
                except (OSError, SyntaxError) as error:
                    add(test_file.relative_to(root).as_posix(), "invalid_test_source", f"Profile 测试源码无法解析：{error}")
                    continue
                findings.extend(
                    case_contract_findings(
                        test_file.relative_to(root), tree, root, topology=topology
                    )
                )

    # Reuse the scanner's parsed imports and resolved symbol graph to identify
    # classes consumed only from tests. A spelling-based denylist is insufficient:
    # a real production import or resolved production call suppresses this finding.
    if topology is not None and module_facts is not None:
        module_paths: dict[str, Path] = {}
        fact_by_path: dict[Path, ModuleFacts] = {}
        for fact in module_facts:
            source = Path(fact.path)
            if not source.is_absolute():
                source = root / source
            source = source.resolve()
            module_paths[fact.module] = source
            fact_by_path[source] = fact

        importers: dict[Path, set[Path]] = {}
        for source, fact in fact_by_path.items():
            tree = fact.tree
            if tree is None:
                continue
            relative = source.relative_to(root)
            package = (
                fact.module
                if relative.name == "__init__.py"
                else fact.module.rpartition(".")[0]
            )
            targets: set[str] = set()
            importlib_aliases: set[str] = set()
            import_module_aliases: set[str] = set()
            for statement in tree.body:
                if isinstance(statement, ast.Import):
                    importlib_aliases.update(
                        alias.asname or alias.name.split(".", 1)[0]
                        for alias in statement.names if alias.name == "importlib"
                    )
                elif isinstance(statement, ast.ImportFrom) and statement.module == "importlib":
                    import_module_aliases.update(
                        alias.asname or alias.name
                        for alias in statement.names if alias.name == "import_module"
                    )
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    targets.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    try:
                        base = (
                            resolve_name("." * node.level + (node.module or ""), package)
                            if node.level else (node.module or "")
                        )
                    except ImportError:
                        continue
                    if base:
                        targets.add(base)
                        targets.update(
                            f"{base}.{alias.name}"
                            for alias in node.names if alias.name != "*"
                        )
                elif isinstance(node, ast.Call) and node.args:
                    function = node.func
                    dynamic_import = (
                        isinstance(function, ast.Attribute)
                        and function.attr == "import_module"
                        and isinstance(function.value, ast.Name)
                        and function.value.id in importlib_aliases
                    ) or (
                        isinstance(function, ast.Name)
                        and function.id in import_module_aliases
                    ) or (
                        isinstance(function, ast.Name)
                        and function.id == "__import__"
                        and "__import__" not in bound_names(tree.body, skip_nested=True)
                    )
                    if dynamic_import and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        targets.add(node.args[0].value)

            for imported in targets:
                parts = imported.split(".")
                target = None
                for boundary in range(len(parts), 0, -1):
                    target = module_paths.get(".".join(parts[:boundary]))
                    if target is not None:
                        break
                if target is not None:
                    importers.setdefault(target, set()).add(source)

        incoming: dict[str, list[object]] = {}
        for edge in topology.edges:
            incoming.setdefault(edge.target_id, []).append(edge)
        symbols_by_path: dict[Path, list[object]] = {}
        for symbol in topology.symbols:
            symbol_path = Path(symbol.path)
            if not symbol_path.is_absolute():
                symbol_path = root / symbol_path
            symbols_by_path.setdefault(symbol_path.resolve(), []).append(symbol)

        framework_bases = {
            "TestCase", "StatefulTestCase", "DataLoader", "JSONDataLoader",
            "JSONLDataLoader", "CSVDataLoader", "PickleDataLoader",
            "CoreQualityRule", "QualityRule", "QualityGuardProfile", "NodeVisitor",
        }
        module_helper_paths: set[Path] = set()

        def resolved_consumers(symbol_ids: set[str]) -> tuple[list[object], list[object]]:
            edges = [edge for symbol_id in symbol_ids for edge in incoming.get(symbol_id, ())]
            test_edges = [edge for edge in edges if is_test_source_path(Path(edge.path))]
            production_edges = [edge for edge in edges if not is_test_source_path(Path(edge.path))]
            return test_edges, production_edges

        def emit_helper_finding(
            source: Path,
            symbol_id: str,
            violation: str,
            test_importers: set[Path],
            production_importers: set[Path],
            test_edges: list[object],
            production_edges: list[object],
            line: int = 1,
        ) -> None:
            relative = source.relative_to(root).as_posix()
            findings.append(Finding(
                code="QG240",
                severity="critical",
                confidence="high",
                path=relative,
                line=line,
                column=1,
                symbol=symbol_id,
                message=(
                    "已解析的 import/consumer 图证明该非 Case 辅助模块或类只有测试消费者，"
                    "没有生产、CLI 或 Release 消费者；不得绕过 rqgtest 协议维护测试专用实现。"
                ),
                suggestion=(
                    "删除测试专用辅助实现，或让正式 Case 直接验证真实生产行为；"
                    "不要添加 helper/support 目录、模块或非 Case 辅助类。"
                ),
                evidence={
                    "framework": "rqgtest",
                    "kind": "RQG_TEST_ONLY_SUPPORT_GRAPH",
                    "violation": violation,
                    "relative_path": relative,
                    "symbol_id": symbol_id,
                    "test_importers": sorted(path.relative_to(root).as_posix() for path in test_importers),
                    "production_importers": sorted(path.relative_to(root).as_posix() for path in production_importers),
                    "test_consumers": sorted({Path(edge.path).as_posix() for edge in test_edges}),
                    "production_consumers": sorted({Path(edge.path).as_posix() for edge in production_edges}),
                    "absolute_blocker": True,
                },
                source="test-contract",
            ))

        for source, symbols in sorted(symbols_by_path.items(), key=lambda item: item[0].as_posix()):
            if not source.is_file() or is_test_source_path(source.relative_to(root)):
                continue
            relative = source.relative_to(root)
            # Script/profile entrypoints can be loaded externally rather than
            # imported by another source file; do not infer missing consumers.
            if relative.parts[0] in {"scripts", "profiles"}:
                continue
            fact = fact_by_path.get(source)
            tree = fact.tree if fact is not None else None
            if tree is not None and any(
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
                and any(
                    isinstance(comparator, ast.Constant)
                    and comparator.value == "__main__"
                    for comparator in node.test.comparators
                )
                for node in tree.body
            ):
                continue
            consumers = {
                importer for importer in importers.get(source, set())
                if importer.resolve() != source.resolve()
            }
            test_importers = {
                importer for importer in consumers
                if is_test_source_path(importer.relative_to(root))
            }
            production_importers = {
                importer for importer in consumers
                if not is_test_source_path(importer.relative_to(root))
            }
            if not test_importers:
                continue

            top_level_symbols = [
                symbol for symbol in symbols
                if not symbol.nested and symbol.kind in {"class", "function"}
            ]
            private_only_functions = (
                not production_importers
                and bool(top_level_symbols)
                and all(symbol.kind == "function" for symbol in top_level_symbols)
                and all(
                    symbol.visibility in {Visibility.PRIVATE, Visibility.INTERNAL}
                    and not symbol.exported
                    for symbol in top_level_symbols
                )
                and tree is not None
                and not any(
                    isinstance(node, (ast.Assign, ast.AnnAssign))
                    and "__all__" in ast.unparse(node)
                    for node in tree.body
                )
                and all(
                    isinstance(node, (ast.Import, ast.ImportFrom))
                    or (
                        index == 0
                        and isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    )
                    or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("_")
                    or isinstance(node, (ast.Assign, ast.AnnAssign))
                    and all(
                        isinstance(target, ast.Name) and target.id.startswith("_")
                        for target in (
                            node.targets if isinstance(node, ast.Assign) else [node.target]
                        )
                    )
                    for index, node in enumerate(tree.body)
                )
            )
            if private_only_functions:
                function_ids = {symbol.symbol_id for symbol in top_level_symbols}
                test_edges, production_edges = resolved_consumers(function_ids)
                if test_edges and not production_edges:
                    module_helper_paths.add(source)
                    emit_helper_finding(
                        source,
                        fact.module if fact is not None else relative.as_posix(),
                        "test_only_helper_module",
                        test_importers,
                        production_importers,
                        test_edges,
                        production_edges,
                    )
                    continue

            for symbol in symbols:
                if symbol.exported or symbol.nested or symbol.kind not in {"class", "function"}:
                    continue
                if source in module_helper_paths:
                    continue
                is_internal = symbol.visibility in {Visibility.PRIVATE, Visibility.INTERNAL}
                # Public functions can be legitimate production APIs directly tested
                # through their API. A public class is suspicious only when its whole
                # module has no production importer. Private/internal symbols are
                # checked independently so helpers cannot hide in a shared module.
                if not is_internal and (symbol.kind != "class" or production_importers):
                    continue
                if symbol.kind == "class" and any(
                    base.split(".")[-1] in framework_bases for base in symbol.bases
                ):
                    continue
                target_ids = {symbol.symbol_id}
                if symbol.kind == "class":
                    target_ids.update(
                        candidate.symbol_id
                        for candidate in symbols
                        if candidate.symbol_id.startswith(symbol.symbol_id + ".")
                    )
                test_edges, production_edges = resolved_consumers(target_ids)
                if symbol.kind == "class":
                    production_edges = [
                        edge for edge in production_edges
                        if not edge.source_id.startswith(symbol.symbol_id + ".")
                    ]
                if not test_edges or production_edges:
                    continue
                emit_helper_finding(
                    source,
                    symbol.symbol_id,
                    "test_only_class_consumer" if symbol.kind == "class" else "test_only_helper_function",
                    test_importers,
                    production_importers,
                    test_edges,
                    production_edges,
                    line=symbol.line,
                )
    return findings


def _closed_case_body_findings(
    path: Path, node: ast.ClassDef, base: str, tree: ast.Module
) -> list[Finding]:
    """Reject Case-defined targets while retaining a single QG240 owner.

    Test leaf classes cannot introduce mutable instance state or local targets.
    Input and oracle storage belongs to the published Case root contract.
    """
    permitted = _ROOT_METHODS[base] | {"__init__"}
    findings: list[Finding] = []
    for member in node.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if member.name not in permitted:
                findings.append(
                    _finding(
                        path,
                        member,
                        "QG240",
                        "标准 Case 不允许额外行为方法；get_enterpoint 必须直接引用现有生产函数，"
                        "不得把自定义测试包装方法作为受测入口。",
                        f"{node.name}.{member.name}",
                    )
                )
            if member.name == "__init__":
                findings.extend(_case_init_contract_findings(path, node, member))
            # Even an allowed method cannot introduce instance attributes.
            # This is separate from the base framework's own case_id/outcome.
            instance_name = member.args.args[0].arg if member.args.args else None
            if instance_name is not None and not any(
                isinstance(decorator, ast.Name) and decorator.id == "staticmethod"
                for decorator in member.decorator_list
            ):
                findings.extend(
                    _case_instance_state_findings(
                        path, node, member, instance_name, tree
                    )
                )
            # A nested callable can conceal a synthetic target even inside a
            # method bearing an authorized framework name.
            for descendant in _case_callable_definitions(member):
                findings.append(
                    _finding(
                        path,
                        descendant,
                        "QG240",
                        "Case 方法内禁止嵌套函数或 lambda；受测行为必须由真正的生产入口执行。",
                        f"{node.name}.{member.name}",
                    )
                )
            continue
        if (
            isinstance(member, ast.Expr)
            and isinstance(member.value, ast.Constant)
            and isinstance(member.value.value, str)
        ):
            continue  # Class docstring is not an executable fixture.
        findings.append(
            _finding(
                path,
                member,
                "QG240",
                "标准 Case 禁止自定义类属性、类级工厂、嵌套类及其他额外成员。"
                "输入和预期值只能作为 Case 实例的构造数据。",
                node.name,
            )
        )
    return findings


def _case_init_contract_findings(
    path: Path, case: ast.ClassDef, constructor: ast.FunctionDef | ast.AsyncFunctionDef
) -> list[Finding]:
    """The leaf constructor only forwards constant Case metadata to its root.

    Enforce the one `super().__init__(...)` statement rather than building a
    second inference engine for optional author-defined constructor parameters.
    """
    body = list(constructor.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    valid = (
        len(body) == 1
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Call)
        and isinstance(body[0].value.func, ast.Attribute)
        and body[0].value.func.attr == "__init__"
        and isinstance(body[0].value.func.value, ast.Call)
        and isinstance(body[0].value.func.value.func, ast.Name)
        and body[0].value.func.value.func.id == "super"
        and not body[0].value.func.value.args
        and not body[0].value.func.value.keywords
        and not body[0].value.args
        and all(keyword.arg in {"type", "config"} for keyword in body[0].value.keywords)
        and len({keyword.arg for keyword in body[0].value.keywords})
        == len(body[0].value.keywords)
    )
    if valid:
        return []
    return [
        _finding(
            path,
            constructor,
            "QG240",
            "Case 的 __init__ 仅用于 super().__init__(type=..., config=...) 声明固定元数据；"
            "不得在构造器内执行编排或创建字段。",
            f"{case.name}.__init__",
        )
    ]


def _case_instance_state_findings(
    path: Path,
    case: ast.ClassDef,
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    receiver: str,
    tree: ast.Module,
) -> list[Finding]:
    """Prove a Case leaf wrote instance state or bypassed target construction.

    Instance fixture declarations belong to the published framework contract,
    not ad-hoc leaf fields.  Pure local data and framework initialization calls
    do not constitute extra state.
    """
    issues: list[Finding] = []
    shadowed_builtins = (
        bound_names(tree.body, skip_nested=True)
        | bound_names(method.body, skip_nested=True)
        | {arg.arg for arg in method.args.args}
    )
    for part in ast.walk(method):
        direct_field = (
            isinstance(part, ast.Attribute)
            and isinstance(part.value, ast.Name)
            and part.value.id == receiver
            and isinstance(part.ctx, (ast.Store, ast.Del))
        )
        mapping_write = (
            isinstance(part, ast.Subscript)
            and isinstance(part.ctx, (ast.Store, ast.Del))
            and isinstance(part.value, ast.Attribute)
            and isinstance(part.value.value, ast.Name)
            and part.value.value.id == receiver
            and part.value.attr == "__dict__"
        )
        dynamic_field = False
        bypass_ctor = False
        if isinstance(part, ast.Call):
            fn = part.func
            direct_setattr = (
                isinstance(fn, ast.Name)
                and fn.id in {"setattr", "delattr"}
                and fn.id not in shadowed_builtins
                and part.args
                and isinstance(part.args[0], ast.Name)
                and part.args[0].id == receiver
            )
            object_setattr = (
                isinstance(fn, ast.Attribute)
                and fn.attr in {"__setattr__", "__delattr__"}
                and isinstance(fn.value, ast.Name)
                and fn.value.id in {"object", receiver}
                and (fn.value.id != "object" or "object" not in shadowed_builtins)
                and (
                    fn.value.id == receiver
                    or bool(part.args)
                    and isinstance(part.args[0], ast.Name)
                    and part.args[0].id == receiver
                )
            )
            mapping_call = (
                isinstance(fn, ast.Attribute)
                and fn.attr in {"update", "setdefault", "clear", "pop", "popitem"}
                and isinstance(fn.value, ast.Attribute)
                and isinstance(fn.value.value, ast.Name)
                and fn.value.value.id == receiver
                and fn.value.attr == "__dict__"
            )
            dynamic_field = bool(direct_setattr or object_setattr or mapping_call)
            bypass_ctor = (
                isinstance(fn, ast.Attribute)
                and fn.attr == "__new__"
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "object"
                and "object" not in shadowed_builtins
            )
        if not (direct_field or mapping_write or dynamic_field or bypass_ctor):
            continue
        message = (
            "Case 禁止 object.__new__ 等绕过受测类正常构造的输入合成；"
            "应经真实构造接口观察实际输入输出。"
            if bypass_ctor
            else "标准 Case 子类禁止新增或修改实例属性，含 __init__、setattr 和 __dict__；"
            "输入、输出及预期须使用框架定义的数据接口。"
        )
        issues.append(
            _finding(path, part, "QG240", message, f"{case.name}.{method.name}")
        )
    return issues


def _case_entrypoint_origin_findings(
    path: Path,
    case: ast.ClassDef,
    declared: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    tree: ast.Module,
    repository_root: Path | None,
    topology: RepositoryTopology | None,
    test_roots: tuple[str, ...],
) -> list[Finding]:
    """Reject a proven nonproduction target, but retain open-world uncertainty.

    A real exported SDK function remains valid without an internal business
    CALLS edge.  A standard library or third-party callable is not the user's
    code, however, and a method on the Case itself is never a production API.
    """
    method = declared.get("get_enterpoint")
    if method is None:
        return []
    imports: dict[str, str | None] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            for alias in stmt.names:
                name = alias.asname or alias.name
                origin = stmt.module if stmt.level == 0 else None
                if name in imports and imports[name] != origin:
                    imports[name] = None
                else:
                    imports[name] = origin
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                name = alias.asname or alias.name.split(".")[0]
                origin = alias.name if alias.asname else alias.name.split(".")[0]
                if name in imports and imports[name] != origin:
                    imports[name] = None
                else:
                    imports[name] = origin
    # Imports that are reassigned in the module cannot be trusted as owners.
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        for name in bound_names([stmt], skip_nested=True):
            imports.pop(name, None)
    # Local imports are equally source-bound; only unshadowed straight-line
    # bindings establish an origin.  Dynamic rebindings remain UNKNOWN.
    for stmt in method.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            for alias in stmt.names:
                name = alias.asname or alias.name
                origin = stmt.module if stmt.level == 0 else None
                if name in imports and imports[name] != origin:
                    imports[name] = None
                else:
                    imports[name] = origin
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                name = alias.asname or alias.name.split(".")[0]
                origin = alias.name if alias.asname else alias.name.split(".")[0]
                if name in imports and imports[name] != origin:
                    imports[name] = None
                else:
                    imports[name] = origin
        else:
            for name in bound_names([stmt], skip_nested=True):
                imports.pop(name, None)
    instance_name = method.args.args[0].arg if method.args.args else None
    # Reuse the repository-wide, source-bound CALLABLE_REFERENCE edges rather
    # than trusting the module path of an intermediate re-export.  An import
    # from a production module can actually resolve to an authored test helper.
    # The graph must resolve precisely one callable at the return expression;
    # unknown/multiple targets never prove an invalid entrypoint.
    entry_node_ids = (
        {
            symbol.symbol_id
            for symbol in topology.symbols
            if symbol.path == path
            and symbol.line == method.lineno
            and symbol.name == method.name
            and symbol.kind in {"method", "function"}
        }
        if topology is not None
        else set()
    )
    source_symbols = (
        {symbol.symbol_id: symbol for symbol in topology.symbols}
        if topology is not None
        else {}
    )
    findings: list[Finding] = []

    def is_repository_cli_process_adapter(target: ast.expr) -> bool:
        """Authenticate subprocess.run only when a Case exercises a real repo CLI.

        ``rqgtest`` invokes the returned callable with the Case input tuple. For
        process-level CLI contracts, ``subprocess.run`` is the execution
        adapter, while the actual subject is the repository-owned script named
        in the yielded command. Accept that adapter only when the same Case
        supplies an independent output Case and compares process status/output;
        arbitrary subprocesses and third-party entrypoints remain rejected.
        """
        if not (
            isinstance(target, ast.Attribute)
            and target.attr == "run"
            and isinstance(target.value, ast.Name)
            and imports.get(target.value.id) == "subprocess"
            and repository_root is not None
        ):
            return False
        input_method = declared.get("get_input_data")
        output_method = declared.get("get_output_data")
        equals_method = declared.get("equals")
        if input_method is None or output_method is None or equals_method is None:
            return False

        # A command variable must carry an existing repository CLI path and be
        # yielded as the actual positional input to subprocess.run.
        command_names: set[str] = set()
        for statement in ast.walk(input_method):
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            value = (
                statement.value
                if isinstance(statement, ast.Assign)
                else statement.value
                if isinstance(statement, ast.AnnAssign)
                else None
            )
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
                if isinstance(statement, ast.AnnAssign)
                else []
            )
            if value is None:
                continue
            script_found = False
            for node in ast.walk(value):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                raw = Path(node.value)
                if raw.suffix != ".py" or not raw.parts or raw.parts[0] not in {"scripts", "runtime"}:
                    continue
                candidate = (repository_root / raw).resolve()
                try:
                    candidate.relative_to(repository_root.resolve())
                except ValueError:
                    continue
                if candidate.is_file():
                    script_found = True
                    break
            if script_found:
                command_names.update(
                    node.id for target_node in targets for node in ast.walk(target_node)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                )
        if not command_names:
            return False
        yielded_command = any(
            isinstance(node, ast.Yield)
            and node.value is not None
            and any(
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Load)
                and child.id in command_names
                for child in ast.walk(node.value)
            )
            for node in ast.walk(input_method)
        )
        if not yielded_command:
            return False

        observed_fields = {
            node.attr for node in ast.walk(equals_method) if isinstance(node, ast.Attribute)
        }
        return "returncode" in observed_fields and bool(
            {"stdout", "stderr"} & observed_fields
        )

    for statement in ast.walk(method):
        if not isinstance(statement, ast.Return) or statement.value is None:
            continue
        target = statement.value
        # The entrypoint getter must provide a callable reference, not invoke
        # arbitrary construction or production work before TestUnit owns it.
        if isinstance(target, ast.Call):
            findings.append(
                _finding(
                    path,
                    target,
                    "QG240",
                    "get_enterpoint 必须返回现有生产入口引用，不得先调用函数/构造器取得返回结果。",
                    f"{case.name}.get_enterpoint",
                )
            )
            continue
        if is_repository_cli_process_adapter(target):
            continue
        if (
            topology is not None
            and isinstance(target, (ast.Name, ast.Attribute))
            and len(entry_node_ids) == 1
        ):
            resolved = {
                edge.target_id
                for source_id in entry_node_ids
                for edge in topology.outgoing(
                    source_id, (UsageKind.CALLABLE_REFERENCE,)
                )
                if edge.path == path
                and edge.line == statement.lineno
                and edge.target_id in source_symbols
            }
            if len(resolved) == 1:
                actual = source_symbols[next(iter(resolved))]
                if actual.test or is_test_source_path(
                    actual.path, test_roots=test_roots
                ):
                    findings.append(
                        _finding(
                            path,
                            statement,
                            "QG240",
                            "共享图证明受测入口实际归属于测试源码，"
                            "即使经生产模块再导出也不能冒充生产函数。",
                            f"{case.name}.get_enterpoint",
                        )
                    )
                    continue
                # A unique real production symbol overrides an intermediate
                # import module's filename; do not invent external ownership.
                continue
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == instance_name
        ):
            findings.append(
                _finding(
                    path,
                    statement,
                    "QG240",
                    "get_enterpoint 返回 Case 自身方法或字段，目标不是独立生产函数。",
                    f"{case.name}.get_enterpoint",
                )
            )
            continue
        if (
            isinstance(target, ast.Name)
            and target.id in {"int", "str", "bool", "float", "list", "dict"}
            and target.id not in imports
            and target.id not in bound_names(tree.body, skip_nested=True)
            and target.id not in bound_names(method.body, skip_nested=True)
            and all(arg.arg != target.id for arg in method.args.args)
        ):
            findings.append(
                _finding(
                    path,
                    statement,
                    "QG240",
                    "受测入口是内置类型/函数，没有调用本仓库的生产功能。",
                    f"{case.name}.get_enterpoint",
                )
            )
            continue
        module = None
        if isinstance(target, ast.Name):
            module = imports.get(target.id)
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            module = imports.get(target.value.id)
        if module is None or repository_root is None:
            continue
        origin = _repository_module_path(repository_root, module)
        if origin is None or is_test_source_path(
            origin.relative_to(repository_root), test_roots=test_roots
        ):
            findings.append(
                _finding(
                    path,
                    statement,
                    "QG240",
                    "受测入口来源已证明为第三方/标准库或测试代码；"
                    "应直接调用本仓库生产源码定义的函数。",
                    f"{case.name}.get_enterpoint",
                )
            )
    return findings


def _repository_module_path(root: Path, module: str) -> Path | None:
    """Return a physical in-repository module, never a matching name guess."""
    parts = module.split(".")
    for prefix in (root, root / "src", root / "sources"):
        target = prefix.joinpath(*parts)
        for candidate in (target.with_suffix(".py"), target / "__init__.py"):
            if candidate.is_file():
                return candidate
    return None



def _case_callable_definitions(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda]:
    """Find callable definitions under one Case method, once per AST node."""
    found: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda] = []
    for root in [
        *method.body,
        *method.decorator_list,
        *method.args.defaults,
        *(value for value in method.args.kw_defaults if value is not None),
    ]:
        for child in ast.walk(root):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                found.append(child)
    return found


class EmptyTestUnitRule(CoreQualityRule[TestContractContext]):
    """QG243 rejects provably empty Case input streams under rqgtest 0.2.

    The executor never receives a Case collection from a user-written runner:
    the production Case itself supplies samples via get_input_data().
    """

    code = "QG243"

    def evaluate(self, context: TestContractContext) -> list[Finding]:
        """Report a definite empty data source without executing user Case code.

        Args:
            context: Source-authenticated Case and its parsed input method.

        Returns:
            One QG243 finding for an empty stream, otherwise no findings.
        """
        owner = context.statement
        if not isinstance(owner, ast.ClassDef):
            raise TypeError("QG243 requires an authenticated concrete Case class")
        source = (context.declared or {}).get("get_input_data")
        if source is None:
            return []
        body = list(source.body)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        if len(body) != 1:
            return []
        statement = body[0]
        empty = False
        if isinstance(statement, ast.Return):
            value = statement.value
            empty = _provably_empty_cases(value) if value is not None else True
            if (isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name) and value.func.id == "iter"
                    and len(value.args) == 1 and not value.keywords
                    and "iter" not in context.builtin_shadowed):
                empty = _provably_empty_cases(value.args[0])
        elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.YieldFrom):
            empty = _provably_empty_cases(statement.value.value)
        if not empty:
            return []
        return [
            _finding(
                context.path, source, "QG243",
                "Case 的 get_input_data 返回确定为空的样本流，不能形成有效执行。",
                f"{owner.name}.get_input_data",
            )
        ]


class SelfRunTestUnitRule(CoreQualityRule[TestContractContext]):
    """QG246 reserves TestUnit construction/execution for the official runner.

    A test module defines only Cases. A source-authenticated TestUnit.run()
    call inside those Cases is a guaranteed violation, including a proven local
    alias. Unrelated variables named TestUnit are not granted framework origin.
    """

    code = "QG246"

    def evaluate(self, context: TestContractContext) -> list[Finding]:
        """Reject a Case that owns TestUnit construction or execution.

        Args:
            context: Current test AST statement plus authenticated rqgtest imports.

        Returns:
            QG246 findings only for a proven TestUnit source binding.
        """
        if not is_test_source_path(context.path, test_roots=context.test_roots):
            return []
        node = context.statement
        if isinstance(node, ast.ClassDef) and node.name == "TestUnit":
            return [
                _finding(
                    context.path, node, "QG246",
                    "单元测试目录不得定义自有 TestUnit；测试仅提供标准 Case，执行由 rqgtest CLI 负责。",
                    "TestUnit",
                )
            ]
        if not isinstance(node, ast.Call):
            return []
        target = node.func
        if isinstance(target, ast.Attribute) and target.attr in {"run", "require_pass"}:
            target = target.value
        if not _certified_testunit_source(target, context, node):
            return []
        return [
            _finding(
                context.path, node, "QG246",
                "单元测试目录不得自行创建或调用 TestUnit；由标准发现器与 CLI 执行 Case。",
                "TestUnit",
            )
        ]


def _certified_testunit_source(
    expression: ast.expr, context: TestContractContext, call: ast.Call
) -> bool:
    """Resolve direct published executor references or one proven local alias.

    Assignment under a conditional, repeated binding or method parameter is
    not elevated from UNKNOWN to a high-confidence framework reference.
    """
    if _certified_symbol(
        expression, context.aliases, context.module_aliases, context.parents
    ) == "TestUnit":
        return True
    if (isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Name)
            and expression.func.id == "getattr"
            and "getattr" not in context.builtin_shadowed
            and not _locally_bound("getattr", expression, context.parents)
            and len(expression.args) == 2 and not expression.keywords
            and isinstance(expression.args[1], ast.Constant)
            and expression.args[1].value in {"run", "require_pass"}):
        return _certified_symbol(
            expression.args[0], context.aliases, context.module_aliases,
            context.parents,
        ) == "TestUnit"
    if not isinstance(expression, (ast.Name, ast.Attribute)):
        return False
    node = context.parents.get(call)
    while node is not None and not isinstance(node, ast.Module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local_imports = straight_line_local_imports(node)
            if isinstance(expression, ast.Name):
                binding = local_imports.get(expression.id)
                if binding is not None and binding[1] < call.lineno:
                    if binding[0] in {"import:rqgtest.TestUnit", "import:rqgtest.core.unit.TestUnit"}:
                        return True
            elif isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
                binding = local_imports.get(expression.value.id)
                if (binding is not None and binding[1] < call.lineno
                        and expression.attr == "TestUnit"
                        and binding[0] in {"import:rqgtest", "import:rqgtest.core.unit"}):
                    return True
                return False
            parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if not isinstance(expression, ast.Name):
                return False
            if any(arg.arg == expression.id for arg in parameters):
                return False
            # A sole top-level straight-line assignment within this function
            # is a proven alias. Calls before assignment are never certified.
            assignments = [
                stmt for stmt in node.body
                if isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == expression.id
            ]
            all_bindings = [
                sub for sub in ast.walk(node)
                if isinstance(sub, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
                and any(isinstance(target, ast.Name) and target.id == expression.id
                        for target in (
                            sub.targets if isinstance(sub, ast.Assign)
                            else [sub.target]
                        ))
            ]
            if (
                len(assignments) == len(all_bindings) == 1
                and assignments[0].lineno < call.lineno
            ):
                rhs = assignments[0].value
                local_binding = None
                if isinstance(rhs, ast.Name):
                    local_binding = local_imports.get(rhs.id)
                    if local_binding is not None and local_binding[1] >= assignments[0].lineno:
                        local_binding = None
                elif isinstance(rhs, ast.Attribute) and isinstance(rhs.value, ast.Name):
                    local_binding = local_imports.get(rhs.value.id)
                    if local_binding is not None and local_binding[1] < assignments[0].lineno:
                        local_binding = (local_binding[0] + "." + rhs.attr, local_binding[1])
                    else:
                        local_binding = None
                return (local_binding is not None
                        and local_binding[0] in {"import:rqgtest.TestUnit", "import:rqgtest.core.unit.TestUnit"}) or _certified_symbol(
                    rhs, context.aliases, context.module_aliases, context.parents
                ) == "TestUnit"
            return False
        node = context.parents.get(node)
    return False

class CaseInterfaceRule(CoreQualityRule[TestContractContext]):
    """QG241 owns required Case methods and their executable signatures.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG241"

    def evaluate(self, context: TestContractContext) -> list[Finding]:
        """Evaluate QG241 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        path, tree, parents = context.path, context.tree, context.parents
        statement, declared, base = context.statement, context.declared, context.base
        findings: list[Finding] = []
        required_names = _ROOT_METHODS[base]
        missing = sorted(required_names - declared.keys())
        if missing and not _is_asserted_rejection(
            statement, tree, parents, "TypeError"
        ):
            findings.append(
                _finding(
                    path,
                    statement,
                    "QG241",
                    "Case 缺少必需的公开行为方法：" + ", ".join(missing),
                    statement.name,
                )
            )
        required = {"get_enterpoint": 0, "equals": 2}
        required.update({"get_input_data": 0, "get_output_data": 0})
        for method_name, required_args in required.items():
            method = declared.get(method_name)
            if method is None:
                continue
            property_required = False
            if _invalid_case_override(method, required_args, property_required, context):
                findings.append(
                    _finding(
                        path,
                        method,
                        "QG241",
                        "标准 Case 方法不满足实例绑定、同步调用、参数个数或 property 契约："
                        + method_name,
                        statement.name,
                    )
                )
        return findings


class AlwaysTrueOracleRule(CoreQualityRule[TestContractContext]):
    """QG242 owns provably unconditional output acceptance.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG242"

    def evaluate(self, context: TestContractContext) -> list[Finding]:
        """Evaluate QG242 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        path, statement, declared = context.path, context.statement, context.declared
        method = declared.get("equals")
        if method is not None and _always_true_comparator(method):
            return [
                _finding(
                    path,
                    method,
                    "QG242",
                    "equals 无条件通过，无法检出错误输出。",
                    statement.name,
                )
            ]
        return []


class OracleIndependenceRule(CoreQualityRule[TestContractContext]):
    """QG244 owns output observation and forbidden implementation-shape oracles.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG244"

    def evaluate(self, context: TestContractContext) -> list[Finding]:
        """Evaluate QG244 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        path, statement, declared = context.path, context.statement, context.declared
        tree, parents = context.tree, context.parents
        findings: list[Finding] = []
        method = declared.get("equals")
        if (
            method is not None
            and not _always_true_comparator(method)
            and _ignores_observed_output(method)
        ):
            findings.append(
                _finding(
                    path,
                    method,
                    "QG244",
                    "equals 未读取被测接口实际输出，无法依据观察值判断正确性。",
                    statement.name,
                )
            )
        for method_name in ("equals", "get_output_data"):
            method = declared.get(method_name)
            if method is None:
                continue
            entry = declared.get("get_enterpoint")
            target_refs = entrypoint_return_bindings(entry, tree) if entry is not None else set()
            witness = _source_shape_witness(method, tree, parents, target_refs)
            if witness is not None:
                findings.append(
                    _finding(
                        path,
                        witness,
                        "QG244",
                        "行为 Oracle 从受测入口读取函数源码/字节码形状，"
                        "而不是约束公开调用结果。",
                        statement.name,
                    )
                )
        return findings


class ProductionEntrypointIsolationRule(CoreQualityRule[TestContractContext]):
    """QG245 blocks proven Case-owned invocation of its real entrypoint.

    No lexical name alone can prove target identity: evaluate against the
    bound return references from get_enterpoint and reject only a call through
    the same unshadowed source binding, or an explicit immediate call to
    self.get_enterpoint(). Opaque dynamic dispatch is not a proven violation.
    """

    code = "QG245"

    def evaluate(self, context: TestContractContext) -> list[Finding]:
        """Find calls that execute the same production target a Case declares.

        Args:
            context: A concrete Case class with its source and topology bindings.

        Returns:
            QG245 findings for provable entrypoint calls inside Case methods.
        """
        declared = context.declared or {}
        entry = declared.get("get_enterpoint")
        if entry is None:
            return []
        root_refs = entrypoint_return_bindings(entry, context.tree)
        owner = context.statement
        if not isinstance(owner, ast.ClassDef):
            return []
        result: list[Finding] = []
        for method_name in _ROOT_METHODS[context.base]:
            method = declared.get(method_name)
            if method is None:
                continue
            witness = entrypoint_call_witness(method, root_refs, context.tree)
            if witness is None:
                witness = _graph_entrypoint_call_witness(
                    method, entry, context.topology, context.path
                )
            if witness is not None:
                result.append(
                    _finding(
                        context.path,
                        witness,
                        "QG245",
                        "Case 的四个合同方法禁止自行执行被测生产入口；"
                        "必须由 TestUnit 调用，并使用独立输入与 Oracle。",
                        f"{owner.name}.{method_name}",
                    )
                )
        return result


def _graph_entrypoint_call_witness(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    entry: ast.FunctionDef | ast.AsyncFunctionDef,
    topology: RepositoryTopology | None,
    path: Path,
) -> ast.Call | None:
    """Prove source/target equality from shared R1/R2/R3 typed edges.

    Only a high-confidence DIRECT_CALL to an exact callable-reference target
    qualifies. Dynamic dispatch or an unresolved edge is not upgraded.
    """
    if topology is None:
        return None
    entry_ids = {
        symbol.symbol_id
        for symbol in topology.symbols
        if symbol.kind in {"method", "function"}
        and symbol.path == path
        and symbol.line == entry.lineno
    }
    caller_ids = {
        symbol.symbol_id
        for symbol in topology.symbols
        if symbol.kind in {"method", "function"}
        and symbol.path == path
        and symbol.line == method.lineno
    }
    if len(entry_ids) != 1 or len(caller_ids) != 1:
        return None
    return_lines = {
        node.lineno
        for node in ast.walk(entry)
        if isinstance(node, ast.Return) and node.value is not None
    }
    targets = {
        edge.target_id
        for entry_id in entry_ids
        for edge in topology.outgoing(entry_id, (UsageKind.CALLABLE_REFERENCE,))
        if edge.path == path and edge.confidence == "high" and edge.line in return_lines
    }
    if not targets:
        return None
    # Traverse only real, source-bound direct calls. A helper that calls the
    # target is still an execution of the production function by the Case.
    # No MAY_DISPATCH, callback candidate or unproven lexical-name edge enters
    # this graph proof.
    reachable = set(targets)
    frontier = list(targets)
    # Reverse traversal is bounded by known symbols, not by an arbitrary depth.
    incoming = {}
    for edge in topology.edges:
        if edge.kind != UsageKind.DIRECT_CALL or edge.confidence != "high":
            continue
        incoming.setdefault(edge.target_id, set()).add(edge.source_id)
    while frontier:
        target = frontier.pop()
        for source in incoming.get(target, ()):
            if source not in reachable:
                reachable.add(source)
                frontier.append(source)
    lines = {
        edge.line
        for caller_id in caller_ids
        for edge in topology.outgoing(caller_id, (UsageKind.DIRECT_CALL,))
        if edge.path == path
        and edge.confidence == "high"
        and edge.target_id in reachable
    }
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and node.lineno in lines:
            return node
    return None



def _certified_symbol(
    expression: ast.expr,
    aliases: dict[str, str],
    module_aliases: dict[str, str],
    parents: dict[ast.AST, ast.AST],
) -> str | None:
    """Resolve only explicit canonical imports, including module-qualified uses."""
    if isinstance(expression, ast.Name):
        if _locally_bound(expression.id, expression, parents):
            return None
        return aliases.get(expression.id)
    if not isinstance(expression, ast.Attribute):
        return None
    parts: list[str] = []
    node = expression
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or _locally_bound(node.id, expression, parents):
        return None
    bound_module = module_aliases.get(node.id)
    if bound_module is None:
        return None
    qualified = bound_module + "." + ".".join(reversed(parts))
    if qualified.startswith(_FRAMEWORK + "."):
        symbol = qualified[len(_FRAMEWORK) + 1 :]
        if symbol in {*_ROOT_METHODS, "TestUnit", "ITestCase"}:
            return symbol
        if symbol == "core.unit.TestUnit":
            return "TestUnit"
    return None


def _provably_empty_cases(value: ast.expr) -> bool:
    """An empty Case batch remains empty under a pure conditional selection."""
    if isinstance(value, (ast.List, ast.Tuple)):
        return not value.elts
    if isinstance(value, ast.IfExp):
        return _provably_empty_cases(value.body) and _provably_empty_cases(value.orelse)
    return False


def _invalid_case_override(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    required_args: int,
    property_required: bool,
    context: TestContractContext,
) -> bool:
    """Reject an incompatible override using one authenticated Case context."""
    abstract_names = context.abstract_names
    abc_modules = context.abc_modules
    parents = context.parents
    builtin_shadowed = context.builtin_shadowed
    property_names = context.property_names
    builtins_modules = context.builtins_modules
    proven_decorators: set[str] = set()
    for decorator in method.decorator_list:
        canonical: str | None = None
        if isinstance(decorator, ast.Name) and not _locally_bound(
            decorator.id, decorator, parents
        ):
            if decorator.id in {"property", "staticmethod", "classmethod"}:
                if decorator.id not in builtin_shadowed:
                    canonical = decorator.id
            elif decorator.id in abstract_names:
                canonical = "abstractmethod"
            elif decorator.id in property_names:
                canonical = "property"
        elif (
            isinstance(decorator, ast.Attribute)
            and isinstance(decorator.value, ast.Name)
            and not _locally_bound(decorator.value.id, decorator, parents)
        ):
            if decorator.attr == "abstractmethod" and decorator.value.id in abc_modules:
                canonical = "abstractmethod"
            elif (
                decorator.attr == "property" and decorator.value.id in builtins_modules
            ):
                canonical = "property"
        if canonical is None:
            return False  # A wrapper may change binding, arity or abstraction.
        proven_decorators.add(canonical)
    if proven_decorators & {"classmethod", "staticmethod", "abstractmethod"}:
        return True
    if ("property" in proven_decorators) != property_required:
        return True
    if isinstance(method, ast.AsyncFunctionDef):
        return True
    body = [
        statement
        for statement in method.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]
    if len(body) == 1:
        statement = body[0]
        if isinstance(statement, ast.Raise) and statement.exc is not None:
            raised = (
                statement.exc.func
                if isinstance(statement.exc, ast.Call)
                else statement.exc
            )
            if isinstance(raised, ast.Name) and raised.id == "NotImplementedError":
                return True
        if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Name):
            if statement.value.id == "NotImplemented":
                return True
    args = method.args
    positions = [*args.posonlyargs, *args.args]
    expected = required_args + 1  # Python passes the bound instance.
    required_positional = len(positions) - len(args.defaults)
    if required_positional > expected:
        return True
    if len(positions) < expected and args.vararg is None:
        return True
    if any(default is None for default in args.kw_defaults):
        return True  # Required keyword-only args are never supplied by TestUnit.
    return False


def _expr_always_true(node: ast.expr | None) -> bool:
    """Only simplify identities that are literally true regardless of runtime data."""
    if isinstance(node, ast.Constant):
        return node.value is True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        # Negating the literal False has no target-specific side effects.
        return isinstance(node.operand, ast.Constant) and node.operand.value is False
    if isinstance(node, ast.IfExp):
        return _expr_always_true(node.body) and _expr_always_true(node.orelse)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_expr_always_true(value) for value in node.values)
        if isinstance(node.op, ast.Or):
            for value in node.values:
                if _expr_always_true(value):
                    return True
                if not isinstance(value, ast.Constant) or value.value is not False:
                    break
            return False
    return False


def _always_true_comparator(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Recognize unconditional acceptance, without guessing target semantics."""
    statements = [
        stmt
        for stmt in method.body
        if not (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        )
    ]
    if len(statements) == 1 and isinstance(statements[0], ast.Return):
        return _expr_always_true(statements[0].value)
    if len(statements) == 1 and isinstance(statements[0], ast.If):
        branch = statements[0]
        return (
            len(branch.body) == 1
            and len(branch.orelse) == 1
            and isinstance(branch.body[0], ast.Return)
            and isinstance(branch.orelse[0], ast.Return)
            and _expr_always_true(branch.body[0].value)
            and _expr_always_true(branch.orelse[0].value)
        )
    if len(statements) == 2 and isinstance(statements[0], ast.If):
        branch, fallback = statements
        return (
            not branch.orelse
            and len(branch.body) == 1
            and isinstance(branch.body[0], ast.Return)
            and isinstance(fallback, ast.Return)
            and _expr_always_true(branch.body[0].value)
            and _expr_always_true(fallback.value)
        )
    return False


def _ignores_observed_output(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A comparator must read its second argument; caller only passes two observations."""
    if len(method.args.posonlyargs) + len(method.args.args) != 3:
        return False  # Unknown signature: QG241 owns incompatible signatures.
    actual_name = [*method.args.posonlyargs, *method.args.args][2].arg
    # Only the method body can observe the value supplied by TestUnit.
    # Defining an unused nested function does not execute its body; a live
    # reference to that function (or dynamic lookup) keeps execution UNKNOWN.
    executable: list[ast.AST] = []
    nested_readers: list[ast.AST] = []
    pending: list[ast.AST] = list(method.body)
    while pending:
        node = pending.pop()
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            if any(
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Load)
                and child.id == actual_name
                for child in ast.walk(node)
            ):
                nested_readers.append(node)
            continue
        executable.append(node)
        pending.extend(ast.iter_child_nodes(node))
    if any(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == actual_name
        for node in executable
    ):
        return False
    if not nested_readers:
        return True
    # Nested lambdas and classes, decorators, or indirectly invoked closures
    # are not statically certified as unexecuted; avoid blocking on a guess.
    if any(
        not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        or node.decorator_list
        for node in nested_readers
    ):
        return False
    unused_names = {node.name for node in nested_readers}
    return not any(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in unused_names
        or isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"locals", "globals", "vars", "eval", "exec"}
        for node in executable
    )


def _source_shape_witness(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    target_refs: set[str],
) -> ast.AST | None:
    """Find only explicitly bound introspection of this Case's public entry point."""
    inspect_aliases = {
        alias.asname or "inspect"
        for stmt in tree.body
        if isinstance(stmt, ast.Import)
        for alias in stmt.names
        if alias.name == "inspect"
    }
    introspection_aliases = {
        alias.asname or alias.name
        for stmt in tree.body
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "inspect"
        for alias in stmt.names
        if alias.name in {"getsource", "getsourcelines"}
    }
    source_names = {"getsource", "getsourcelines"}
    shadowed = bound_names(
        [
            stmt
            for stmt in tree.body
            if not (
                isinstance(stmt, ast.Import)
                and any(alias.name == "inspect" for alias in stmt.names)
                or isinstance(stmt, ast.ImportFrom)
                and stmt.module == "inspect"
            )
        ],
        skip_nested=True,
    )
    inspect_aliases.difference_update(shadowed)
    introspection_aliases.difference_update(shadowed)

    def is_entry(expr: ast.expr) -> bool:
        """Recognize the canonical no-argument TestCase entrypoint accessor call.

        Args:
            expr: AST expression tested for direct self.get_enterpoint() invocation.

        Returns:
            True only for an unqualified, no-argument accessor call on self."""
        return (
            isinstance(expr, ast.Call)
            and not expr.args
            and not expr.keywords
            and isinstance(expr.func, ast.Attribute)
            and expr.func.attr == "get_enterpoint"
            and isinstance(expr.func.value, ast.Name)
            and expr.func.value.id == "self"
        )

    # Track an unambiguous, never-reassigned local alias to the entry point.
    writes: dict[str, int] = {}
    for node in ast.walk(method):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            writes[node.id] = writes.get(node.id, 0) + 1
    entry_aliases = {
        stmt.targets[0].id
        for stmt in method.body
        if isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and is_entry(stmt.value)
        and writes.get(stmt.targets[0].id) == 1
    }

    def is_source_target(expr: ast.expr) -> bool:
        """Recognize the entrypoint call or its unique non-reassigned local alias.

        Args:
            expr: AST expression that may represent the observed entrypoint.

        Returns:
            True for the direct accessor or a proven single-assignment alias;
            never treats arbitrary local names as entrypoint evidence."""
        if is_entry(expr) or (isinstance(expr, ast.Name) and expr.id in entry_aliases):
            return True
        if isinstance(expr, (ast.Name, ast.Attribute)):
            base = expr
            while isinstance(base, ast.Attribute):
                base = base.value
            # A shadowed import name does not authenticate the production target.
            if isinstance(base, ast.Name) and _locally_bound(base.id, expr, parents):
                return False
            return stable_reference(expr, tree) in target_refs
        return False

    # Nested functions and lambdas can be declared without being invoked.
    # Reading source only inside such a declaration is not proof that the
    # running comparator has inspected its entry point. Preserve UNKNOWN.
    body_nodes = list(reversed(method.body))
    while body_nodes:
        node = body_nodes.pop()
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            continue
        body_nodes.extend(reversed(list(ast.iter_child_nodes(node))))
        if isinstance(node, ast.Attribute) and node.attr in {"__code__", "__closure__"}:
            if is_source_target(node.value):
                return node
        if (
            not isinstance(node, ast.Call)
            or not node.args
            or not is_source_target(node.args[0])
        ):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if (
                func.attr in source_names
                and isinstance(func.value, ast.Name)
                and func.value.id in inspect_aliases
                and not _locally_bound(func.value.id, node, parents)
            ):
                return node
        if isinstance(func, ast.Name):
            if func.id in introspection_aliases and not _locally_bound(
                func.id, node, parents
            ):
                return node
    return None


def _is_asserted_rejection(
    node: ast.AST, tree: ast.Module, parents: dict[ast.AST, ast.AST], error: str
) -> bool:
    """Do not report a broken Case that an actual pytest guard expects to reject."""
    pytest_imports: set[ast.stmt] = set()
    pytest_aliases: set[str] = set()
    raises_aliases: set[str] = set()
    foreign_names: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                local = alias.asname or alias.name.split(".")[0]
                if alias.name == "pytest":
                    pytest_aliases.add(local)
                    pytest_imports.add(statement)
                else:
                    foreign_names.add(local)
        elif isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                local = alias.asname or alias.name
                if statement.module == "pytest" and alias.name == "raises":
                    raises_aliases.add(local)
                    pytest_imports.add(statement)
                else:
                    foreign_names.add(local)
    # A foreign import, global assignment or duplicate binding means the
    # advertised pytest.raises guard is not source-proven anymore.
    shadowed = (
        bound_names(
            [statement for statement in tree.body if statement not in pytest_imports],
            skip_nested=True,
        )
        | foreign_names
        | (pytest_aliases & raises_aliases)
    )
    pytest_aliases.difference_update(shadowed)
    raises_aliases.difference_update(shadowed)
    current = parents.get(node)
    while current is not None and not isinstance(current, ast.Module):
        if isinstance(current, ast.With):
            for item in current.items:
                ctx = item.context_expr
                if not (
                    isinstance(ctx, ast.Call)
                    and len(ctx.args) == 1
                    and not ctx.keywords
                ):
                    continue
                expected = ctx.args[0]
                if not isinstance(expected, ast.Name) or expected.id != error:
                    continue
                name = ctx.func
                if (
                    isinstance(name, ast.Name)
                    and name.id in raises_aliases
                    and not _locally_bound(name.id, ctx, parents)
                ):
                    return True
                if (
                    isinstance(name, ast.Attribute)
                    and name.attr == "raises"
                    and isinstance(name.value, ast.Name)
                    and name.value.id in pytest_aliases
                    and not _locally_bound(name.value.id, ctx, parents)
                ):
                    return True
        current = parents.get(current)
    return False



def _locally_bound(name: str, node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Fail conservative when an enclosing lexical scope replaces the import."""
    current = parents.get(node)
    crossed_function = False
    while current is not None and not isinstance(current, ast.Module):
        if isinstance(current, ast.ClassDef) and not crossed_function:
            bound = (
                parents.bound_names(current)
                if isinstance(parents, _CachedParents)
                else bound_names(current.body, skip_nested=True)
            )
            if name in bound:
                return True
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            crossed_function = True
            parameters = current.args
            named = [*parameters.posonlyargs, *parameters.args, *parameters.kwonlyargs]
            if any(arg.arg == name for arg in named):
                return True
            if any(
                arg is not None and arg.arg == name
                for arg in (parameters.vararg, parameters.kwarg)
            ):
                return True
            body = current.body if not isinstance(current, ast.Lambda) else []
            bound = (
                (
                    parents.bound_names(current)
                    if isinstance(parents, _CachedParents)
                    else bound_names(body, skip_nested=True)
                )
                if not isinstance(current, ast.Lambda)
                else set()
            )
            if name in bound:
                return True
        current = parents.get(current)
    return False


def _finding(
    path: Path, node: ast.AST, code: str, message: str, symbol: str
) -> Finding:
    """Keep each witness tied to its real source location."""
    return Finding(
        code=code,
        severity="critical"
        if code in {"QG240", "QG241", "QG242", "QG244", "QG245", "QG246"}
        else "error",
        confidence="high",
        path=path.as_posix(),
        line=node.lineno,
        column=node.col_offset + 1,
        symbol=symbol,
        message=message,
        suggestion="修复当前行为 Case 的可执行合同；不约束被测业务函数的内部实现。",
        evidence={
            "framework": _FRAMEWORK,
            "kind": "PROVEN_TEST_CONTRACT",
            "absolute_blocker": True,
        },
        source="test-contract",
    )
