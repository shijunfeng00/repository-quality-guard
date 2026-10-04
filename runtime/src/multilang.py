"""多语言源码事实适配与跨语言结构规则。"""

from __future__ import annotations

import json
import posixpath
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .cpp_build_contract import (
    CompilerFamily,
    TranslationUnitBuildSpec,
    repository_path,
    load_compilation_database,
)
from .bootstrap_settings import node_parser_environment
from .config import GuardConfig
from .graph_utils import strongly_connected_components
from .model import Finding

_SOURCE = "quality-guard-multilang"
SCRIPT_LANGUAGES = {"javascript", "typescript"}
_NODE_LANGUAGES = SCRIPT_LANGUAGES | {"css"}
_CPP_DECL_KINDS = (
    "FunctionDecl",
    "CXXMethodDecl",
    "CXXConstructorDecl",
    "CXXDestructorDecl",
)
_FUNC_RE = re.compile(
    r"(?P<kind>FunctionDecl|CXXMethodDecl|CXXConstructorDecl|CXXDestructorDecl)\s+"
    r"\S+\s+<(?P<range>[^>]+)>\s+.*?"
    r"(?P<name>[~A-Za-z_][A-Za-z0-9_:~]*)\s+'(?P<sig>[^']+)'"
)
_LOCATION_RE = re.compile(r"^(?P<prefix>.*?):?(?P<line>\d+):(?P<column>\d+)$")
_BRANCH_NODES = {
    "IfStmt",
    "ForStmt",
    "CXXForRangeStmt",
    "WhileStmt",
    "DoStmt",
    "CaseStmt",
    "ConditionalOperator",
    "CXXCatchStmt",
}
_NEST_NODES = {
    "IfStmt",
    "ForStmt",
    "CXXForRangeStmt",
    "WhileStmt",
    "DoStmt",
    "SwitchStmt",
    "CXXTryStmt",
}


def node_facts(snapshot: RepositoryAnalysisSnapshot) -> list[dict[str, Any]]:
    """使用随 Skill 发布的 parser 从统一快照提取 JS/TS/CSS 事实。

    Args:
        snapshot: 已读取源文件的统一仓库快照。

    Returns:
        bundled Node parser 生成的 JS/TS/CSS 静态事实列表。

    Raises:
        RuntimeError: Node/parser 依赖缺失、超时或解析失败。
    """
    items = [
        {
            "path": path,
            "language": snapshot.language_units[path].language,
            "source": snapshot.language_units[path].source,
        }
        for path in snapshot.language_paths
        if snapshot.language_units[path].language in _NODE_LANGUAGES
    ]
    if not items:
        return []

    node = shutil.which("node")
    if node is None:
        raise RuntimeError(
            "仓库包含 JS/TS/CSS，但系统缺少 Node.js；拒绝静默跳过多语言 AST。"
        )
    try:
        environment = node_parser_environment()
    except RuntimeError as error:
        raise RuntimeError(
            "仓库包含 JS/TS/CSS，但锁定 Node parser 依赖尚未安装；"
            "请运行 runtime/install_dependencies.py 或重新 deploy Skill。"
        ) from error
    parser = Path(__file__).resolve().parent / "multilang_parser.js"
    try:
        result = subprocess.run(
            [node, str(parser)],
            input=json.dumps(items, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            capture_output=True,
            check=False,
            env=environment,
            timeout=60,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("多语言 AST parser 执行超过 60 秒，已终止。") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise RuntimeError(f"多语言 AST parser 失败：{detail}")
    return list(json.loads(result.stdout))


def _script_usage(
    scripts: list[dict[str, Any]],
) -> tuple[
    list[tuple[dict[str, Any], dict[str, Any]]],
    Counter[str],
]:
    """计算具名 JS/TS definition 及调用/符号引用使用次数。"""
    definitions: list[tuple[dict[str, Any], dict[str, Any]]] = []
    references: Counter[str] = Counter()
    declarations: Counter[str] = Counter()
    calls: Counter[str] = Counter()

    for script in scripts:
        references.update(script["identifier_refs"])
        references.update(script["symbol_refs"])
        for definition in script["definitions"]:
            if definition["name"].startswith("<anonymous@"):
                continue
            definitions.append((script, definition))
            declarations[definition["name"]] += 1
            calls.update(definition["calls"])

    names = set(references) | set(declarations) | set(calls)
    usage = Counter(
        {
            name: max(calls[name], max(0, references[name] - declarations[name]))
            for name in names
        }
    )
    return definitions, usage


def _script_module_findings(
    scripts: list[dict[str, Any]],
    config: GuardConfig,
) -> list[Finding]:
    """按模块顺序报告 parser diagnostics 和模块规模契约。"""
    findings: list[Finding] = []
    for script in scripts:
        for diagnostic in script["diagnostics"]:
            findings.append(
                Finding(
                    code="QG000",
                    severity="critical",
                    confidence="high",
                    path=script["path"],
                    line=1,
                    column=1,
                    message=(
                        f"{script['language']} 语法解析失败：{diagnostic['message']}"
                    ),
                    suggestion="先修复语法错误，再继续多语言结构审计。",
                    source=_SOURCE,
                )
            )
        if script["lines"] > config.max_module_lines:
            findings.append(
                Finding(
                    code="QG019",
                    severity="critical",
                    confidence="high",
                    path=script["path"],
                    line=1,
                    column=1,
                    message=f"模块共 {script['lines']} 行，超过硬上限 {config.max_module_lines}。",
                    suggestion="按稳定职责/状态 owner 拆分；禁止仅按行数机械切文件。",
                    evidence={
                        "language": script["language"],
                        "lines": script["lines"],
                        "review_threshold": config.module_line_review_threshold,
                        "hard_limit": config.max_module_lines,
                    },
                    source=_SOURCE,
                )
            )
        elif script["lines"] > config.module_line_review_threshold:
            findings.append(
                Finding(
                    code="QG019",
                    severity="info",
                    confidence="medium",
                    path=script["path"],
                    line=1,
                    column=1,
                    message=(
                        f"模块共 {script['lines']} 行，进入 owner cohesion 语义复核区间。"
                    ),
                    suggestion=(
                        "确认文件仍对应单一稳定 owner；没有独立职责/状态边界时不要机械拆分。"
                    ),
                    evidence={
                        "language": script["language"],
                        "lines": script["lines"],
                        "review_threshold": config.module_line_review_threshold,
                        "hard_limit": config.max_module_lines,
                        "semantic_review_required": True,
                        "semantic_review_question": "Q4,Q9",
                        "semantic_review_kind": "owner-cohesion-size",
                    },
                    source=_SOURCE,
                )
            )

    return findings


def _definition_findings(
    scripts: list[dict[str, Any]],
    config: GuardConfig,
) -> list[Finding]:
    """生成 JS/TS 函数级通用质量发现。"""
    findings: list[Finding] = []
    definitions, usage = _script_usage(scripts)
    by_scope: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    shapes: defaultdict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(
        list
    )

    findings.extend(_script_module_findings(scripts, config))

    for script, definition in definitions:
        lines = definition["end_line"] - definition["line"] + 1
        name = definition["name"]
        symbol = definition["qualname"]
        common = {
            "path": script["path"],
            "line": definition["line"],
            "column": definition["column"],
            "symbol": symbol,
            "source": _SOURCE,
        }
        if lines > config.max_function_lines:
            findings.append(
                Finding(
                    code="QG014",
                    severity="critical",
                    confidence="high",
                    message=(
                        f"`{symbol}` 长度 {lines} 行，超过阈值 "
                        f"{config.max_function_lines}。"
                    ),
                    suggestion=(
                        "按独立阶段/副作用拆分，并同时检查 owner 是否因 helper "
                        "extraction 继续膨胀。"
                    ),
                    evidence={"language": script["language"], "lines": lines},
                    **common,
                )
            )
        nesting = definition["max_nesting"]
        if nesting > config.max_nesting:
            findings.append(
                Finding(
                    code="QG016",
                    severity="warning",
                    confidence="high",
                    message=(
                        f"`{symbol}` 最大控制流嵌套 {nesting} 层，超过阈值 "
                        f"{config.max_nesting}。"
                    ),
                    suggestion="使用早返回或阶段化流程降低嵌套。",
                    evidence={
                        "language": script["language"],
                        "max_nesting": nesting,
                    },
                    **common,
                )
            )
        branches = definition["branches"]
        if branches > config.max_branches:
            findings.append(
                Finding(
                    code="QG017",
                    severity="warning",
                    confidence="medium",
                    message=(
                        f"`{symbol}` 估算分支数 {branches}，超过阈值 "
                        f"{config.max_branches}。"
                    ),
                    suggestion="检查是否混入多阶段职责或模式分支。",
                    evidence={
                        "language": script["language"],
                        "branches": branches,
                    },
                    **common,
                )
            )
        if (
            lines < config.short_max_lines
            and usage[name] <= config.low_use_max_calls
            and name not in config.ignored_names
        ):
            findings.append(
                Finding(
                    code="QG001",
                    severity="info",
                    confidence="medium",
                    message=(
                        f"{definition['kind']} `{symbol}` 仅 {lines} 行，"
                        f"静态调用/引用 {usage[name]} 次。"
                    ),
                    suggestion=(
                        "事件 handler、dispatch callback、稳定谓词可保留；"
                        "纯局部步骤优先内联。"
                    ),
                    evidence={
                        "language": script["language"],
                        "lines": lines,
                        "usage": usage[name],
                    },
                    **common,
                )
            )
        wrapper_target = definition["wrapper_target"]
        if wrapper_target:
            findings.append(
                Finding(
                    code="QG010",
                    severity="warning" if usage[name] <= 1 else "info",
                    confidence="high",
                    message=f"`{symbol}` 只委托给 `{wrapper_target}`。",
                    suggestion=("若没有独立契约、校验、日志或语义转换，直接复用目标。"),
                    evidence={
                        "language": script["language"],
                        "target": wrapper_target,
                        "usage": usage[name],
                    },
                    **common,
                )
            )

        scope_key = definition["scope_key"]
        by_scope[(script["path"], scope_key, name)].append(definition)
        shape = definition["shape"]
        if lines >= 4 and shape:
            shapes[shape].append((script, definition))

    findings.extend(_script_duplicate_findings(by_scope, shapes))
    return findings


def _script_duplicate_findings(
    by_scope: dict[tuple[str, str, str], list[dict[str, Any]]],
    shapes: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]],
) -> list[Finding]:
    """从同 scope 名称和函数体形状索引报告定义重复。"""
    findings: list[Finding] = []
    for (path, scope, name), group in by_scope.items():
        if len(group) <= 1:
            continue
        findings.append(
            Finding(
                code="QG195",
                severity="error",
                confidence="high",
                path=path,
                line=min(item["line"] for item in group),
                column=1,
                symbol=f"{scope}::{name}",
                message=(
                    f"同一作用域 `{scope}` 重复定义 `{name}`，后声明会遮蔽前声明。"
                ),
                suggestion=(
                    "删除重复声明或改成唯一 owner；"
                    "不要依赖 JavaScript declaration shadowing。"
                ),
                evidence={"lines": [item["line"] for item in group]},
                source=_SOURCE,
            )
        )

    for group in shapes.values():
        if len(group) < 2:
            continue
        members = sorted(
            f"{script['path']}::{definition['qualname']}"
            for script, definition in group
        )
        findings.append(
            Finding(
                code="QG200",
                severity="info",
                confidence="medium",
                path=group[0][0]["path"],
                line=min(definition["line"] for _, definition in group),
                column=1,
                symbol=" | ".join(members),
                message=f"{len(group)} 个 JS/TS 定义具有相同规范化 AST 结构。",
                suggestion=(
                    "仅作为复用候选；对称操作、类型适配和语义谓词不得机械合并。"
                ),
                evidence={"members": members},
                source=_SOURCE,
            )
        )
    return findings


def _script_architecture_findings(
    scripts: list[dict[str, Any]],
) -> list[Finding]:
    """识别动态 prototype composition 隐藏的 owner surface 与 feature 环。"""
    findings: list[Finding] = []
    bag_module: dict[str, str] = {}
    bag_methods: dict[str, list[dict[str, Any]]] = {}
    classes: list[tuple[str, dict[str, Any]]] = []
    compositions: list[tuple[str, dict[str, Any]]] = []
    type_escapes = 0

    for script in scripts:
        type_escapes += int(script["type_escapes"])
        for bag in script["bags"]:
            bag_module[bag["name"]] = script["path"]
            bag_methods[bag["name"]] = bag["methods"]
        classes.extend((script["path"], item) for item in script["classes"])
        compositions.extend((script["path"], item) for item in script["compositions"])

    for module, composition in compositions:
        bags = [name for name in composition["bags"] if name in bag_module]
        modules = sorted({bag_module[name] for name in bags})
        methods = [
            (bag_module[bag], method) for bag in bags for method in bag_methods[bag]
        ]
        method_owners: defaultdict[str, list[str]] = defaultdict(list)
        for owner, method in methods:
            method_owners[method["name"]].append(owner)
        unique_owner = {
            name: owners[0]
            for name, owners in method_owners.items()
            if len(set(owners)) == 1
        }
        edges = [
            (source, unique_owner[call])
            for source, method in methods
            for call in method["calls"]
            if call in unique_owner and unique_owner[call] != source
        ]
        cross_feature_calls = len(edges)

        class_fact = next(
            (item for _, item in classes if item["name"] == composition["class_name"]),
            None,
        )
        constructor_fields = len(class_fact["constructor_fields"]) if class_fact else 0
        composed_methods = sum(len(bag_methods[name]) for name in bags)
        dependency_graph = {name: set() for name in modules}
        for source, target in edges:
            dependency_graph[source].add(target)
        components = [
            sorted(component)
            for component in strongly_connected_components(dependency_graph)
        ]
        components.sort(key=lambda component: (-len(component), component))
        largest = components[0] if components else []

        if composed_methods > 100 or constructor_fields > 50:
            findings.append(
                Finding(
                    code="QG198",
                    severity="warning",
                    confidence="high",
                    path=module,
                    line=1,
                    column=1,
                    symbol=composition["class_name"],
                    message=(
                        f"动态 owner `{composition['class_name']}` 组合 "
                        f"{composed_methods} 个方法并持有 {constructor_fields} 个构造字段。"
                    ),
                    suggestion=(
                        "检查是否仍是分文件后的 God Controller；"
                        "按状态和调用闭包收窄 owner surface。"
                    ),
                    evidence={
                        "composed_methods": composed_methods,
                        "constructor_fields": constructor_fields,
                        "feature_modules": len(modules),
                    },
                    source=_SOURCE,
                )
            )
        if len(largest) >= 5:
            findings.append(
                Finding(
                    code="QG199",
                    severity="warning",
                    confidence="high",
                    path=module,
                    line=1,
                    column=1,
                    symbol=composition["class_name"],
                    message=(
                        f"动态 feature 依赖图存在 {len(largest)} 模块强连通分量。"
                    ),
                    suggestion=(
                        "不要只依赖 ES import 无环；按真实 this/self 调用关系拆分闭环。"
                    ),
                    evidence={
                        "largest_scc_size": len(largest),
                        "modules": largest,
                        "cross_feature_calls": cross_feature_calls,
                        "distinct_edges": len(set(edges)),
                    },
                    source=_SOURCE,
                )
            )

    if type_escapes >= 10:
        first_path = scripts[0]["path"] if scripts else "."
        findings.append(
            Finding(
                code="QG207",
                severity="info",
                confidence="medium",
                path=first_path,
                line=1,
                column=1,
                symbol="javascript",
                message=f"JS/TS 中存在 {type_escapes} 个显式 any JSDoc 逃逸。",
                suggestion=(
                    "动态 composition 可保留必要 escape，但不能把 checkJs PASS "
                    "当作完整 owner contract 证明。"
                ),
                evidence={"any_escapes": type_escapes},
                source=_SOURCE,
            )
        )
    return findings


def _css_findings(facts: list[dict[str, Any]]) -> list[Finding]:
    """生成 CSS 语法与精确声明重复候选。"""
    findings: list[Finding] = []
    groups: defaultdict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for file_fact in facts:
        if file_fact["language"] != "css":
            continue
        for error in file_fact["errors"]:
            findings.append(
                Finding(
                    code="QG000",
                    severity="critical",
                    confidence="high",
                    path=file_fact["path"],
                    line=1,
                    column=1,
                    message=f"CSS AST 解析失败：{error}",
                    suggestion="修复 CSS 语法后重跑。",
                    source=_SOURCE,
                )
            )
        for rule in file_fact["rules"]:
            if rule["declarations"] >= 3 and rule["normalized"]:
                groups[rule["normalized"]].append((file_fact["path"], rule))

    for group in groups.values():
        if len(group) < 2:
            continue
        members = sorted(f"{path}::{rule['selector']}" for path, rule in group)
        findings.append(
            Finding(
                code="QG201",
                severity="info",
                confidence="high",
                path=group[0][0],
                line=min(rule["line"] for _, rule in group),
                column=1,
                symbol=" | ".join(members),
                message=f"{len(group)} 个 CSS selector 具有完全相同的声明块。",
                suggestion=(
                    "只有语义 owner 一致时才合并 selector/class；"
                    "声明值巧合相同不得机械绑定组件。"
                ),
                evidence={"members": members},
                source=_SOURCE,
            )
        )
    return findings


class _HtmlFacts(HTMLParser):
    """收集 HTML id 定义位置。"""

    def __init__(self) -> None:
        """初始化 HTML id 收集器。

        Returns:
            None。
        """
        super().__init__(convert_charrefs=True)
        self.ids: list[tuple[str, int]] = []

    def handle_starttag(
        self,
        _tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """记录开始标签中的 id 属性及其源码行号。

        Args:
            _tag: HTML 标签名称；当前规则不按标签类型分支。
            attrs: 标签属性键值对。

        Returns:
            None。
        """
        for key, value in attrs:
            if key.lower() == "id" and value:
                self.ids.append((value, self.getpos()[0]))


def _html_findings(snapshot: RepositoryAnalysisSnapshot) -> list[Finding]:
    """检查 HTML 文档内重复 id。"""
    findings: list[Finding] = []
    for path in snapshot.language_paths:
        unit = snapshot.language_units[path]
        if unit.language != "html":
            continue
        parser = _HtmlFacts()
        parser.feed(unit.source)
        grouped: defaultdict[str, list[int]] = defaultdict(list)
        for value, line in parser.ids:
            grouped[value].append(line)
        for value, lines in grouped.items():
            if len(lines) <= 1:
                continue
            findings.append(
                Finding(
                    code="QG202",
                    severity="error",
                    confidence="high",
                    path=path,
                    line=min(lines),
                    column=1,
                    symbol=value,
                    message=f"HTML id `{value}` 在同一文档重复出现 {len(lines)} 次。",
                    suggestion="id 必须唯一；需要复用样式/行为时改用 class 或 data-*。",
                    evidence={"lines": lines},
                    source=_SOURCE,
                )
            )
    return findings


def _flatten_script_definitions(
    facts: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """把具名 JS/TS definition 映射为稳定 path/qualname 键。"""
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for file_fact in facts:
        if file_fact["language"] not in SCRIPT_LANGUAGES:
            continue
        for definition in file_fact["definitions"]:
            if definition["name"].startswith("<anonymous@"):
                continue
            item = dict(definition)
            item["lines"] = definition["end_line"] - definition["line"] + 1
            result[(file_fact["path"], definition["qualname"])] = item
    return result


def _script_growth_findings(
    base: dict[tuple[str, str], dict[str, Any]],
    target: dict[tuple[str, str], dict[str, Any]],
) -> list[Finding]:
    """比较既有具名 giant definition 的行数和嵌套定义增长。"""
    findings: list[Finding] = []
    for key, target_definition in target.items():
        if key not in base:
            continue
        base_definition = base[key]
        line_delta = target_definition["lines"] - base_definition["lines"]
        nested_delta = target_definition["nested_defs"] - base_definition["nested_defs"]
        giant = (
            target_definition["lines"] > 500 or target_definition["nested_defs"] >= 50
        )
        significant_growth = line_delta >= 20 or nested_delta >= 5
        if giant and significant_growth:
            findings.append(
                Finding(
                    code="QG196",
                    severity="info",
                    confidence="high",
                    path=key[0],
                    line=target_definition["line"],
                    column=1,
                    symbol=key[1],
                    message=(
                        f"既有 giant owner `{key[1]}` 仍保持超大规模且本轮继续恶化。"
                    ),
                    suggestion=(
                        "不得只用 child helper 指标下降宣称质量清零；"
                        "解释 owner 增长是否必要，或继续按真实职责缩减。"
                    ),
                    evidence={
                        "lines": [base_definition["lines"], target_definition["lines"]],
                        "nested_defs": [
                            base_definition["nested_defs"],
                            target_definition["nested_defs"],
                        ],
                    },
                    source=_SOURCE,
                )
            )

    return findings


def _script_diff_findings(
    base_facts: list[dict[str, Any]],
    target_facts: list[dict[str, Any]],
) -> list[Finding]:
    """检测 giant owner 恶化和同 owner 内 helper laundering。"""
    base = _flatten_script_definitions(base_facts)
    target = _flatten_script_definitions(target_facts)
    findings: list[Finding] = []

    findings.extend(_script_growth_findings(base, target))

    for key, base_definition in base.items():
        if base_definition["lines"] <= 500 and base_definition["nested_defs"] < 50:
            continue
        prefix = f"{key[1]}."
        direct_depth = prefix.count(".")
        before = {
            qualname
            for path, qualname in base
            if path == key[0]
            and qualname.startswith(prefix)
            and qualname.count(".") == direct_depth
        }
        after = {
            qualname
            for path, qualname in target
            if path == key[0]
            and qualname.startswith(prefix)
            and qualname.count(".") == direct_depth
        }
        added = sorted(after - before)
        if len(added) < 5:
            continue
        target_definition = target[key] if key in target else base_definition
        findings.append(
            Finding(
                code="QG197",
                severity="info",
                confidence="medium",
                path=key[0],
                line=target_definition["line"],
                column=1,
                symbol=key[1],
                message=(
                    f"既有 giant owner 本轮新增 {len(added)} 个直接 nested helper，"
                    "存在 helper laundering 风险。"
                ),
                suggestion=(
                    "验证复杂度是否真正迁移到独立 owner；若只是同一 closure 内切碎步骤，"
                    "应继续减法或明确保留理由。"
                ),
                evidence={"new_direct_helpers": len(added), "examples": added[:20]},
                source=_SOURCE,
            )
        )
    return findings


def _changed_cpp_paths(
    base: RepositoryAnalysisSnapshot,
    target: RepositoryAnalysisSnapshot,
) -> list[str]:
    """返回相对基线真实变化的 C/C++ 源文件。"""
    return [
        path
        for path in target.language_paths
        if target.language_units[path].language == "cpp"
        and (
            path not in base.language_units
            or base.language_units[path].source != target.language_units[path].source
        )
    ]


def _cpp_changed_findings(
    base: RepositoryAnalysisSnapshot,
    target: RepositoryAnalysisSnapshot,
    config: GuardConfig,
) -> list[Finding]:
    """只对变化 C++ 文件运行 Clang AST 差分结构检查。"""
    changed = _changed_cpp_paths(base, target)
    if not changed:
        return []
    fallback_clang = shutil.which("clang++") or shutil.which("clang")
    target_metrics, target_available = _cpp_metrics(target, changed, fallback_clang)
    if not target_available:
        return []
    comparable_paths = [path for path in changed if path in target_available]
    base_metrics, base_available = _cpp_metrics(base, comparable_paths, fallback_clang)
    base_available.update(
        path for path in comparable_paths if path not in base.language_units
    )
    findings: list[Finding] = []
    for key, current in target_metrics.items():
        path = current["path"]
        if path not in target_available or path not in base_available:
            continue
        baseline = base_metrics[key] if key in base_metrics else None
        crossed = (
            current["lines"] > config.max_function_lines
            or current["branches"] > config.max_branches
            or current["max_nesting"] > config.max_nesting
        )
        worsened = baseline is None or any(
            current[field] > baseline[field]
            for field in ("lines", "branches", "max_nesting")
        )
        if not crossed or not worsened:
            continue
        severity = (
            "critical" if current["lines"] > config.max_function_lines else "warning"
        )
        findings.append(
            Finding(
                code="QG204",
                severity=severity,
                confidence="medium",
                path=current["path"],
                line=current["line"],
                column=1,
                symbol=current["symbol"],
                message=(
                    f"C++ 变更后的 `{current['symbol']}` 结构复杂度越过或恶化："
                    f"{current['lines']} 行 / {current['branches']} 分支 / "
                    f"nesting {current['max_nesting']}。"
                ),
                suggestion=(
                    "保留 -Werror/ctest，同时按真实职责降低结构复杂度；"
                    "不要只依赖编译器 warning gate。"
                ),
                evidence={"baseline": baseline, "current": current},
                source=_SOURCE,
            )
        )
    return findings


def _clang_location(
    token: str,
    current_file: Path | None,
) -> tuple[Path | None, int | None]:
    """解析 Clang AST location token，并保留 line/col 的当前文件上下文。"""
    value = token.strip()
    if value.startswith("line:"):
        match = re.match(r"line:(\d+):\d+$", value)
        return current_file, int(match.group(1)) if match else None
    if value.startswith("col:"):
        return current_file, None
    match = _LOCATION_RE.match(value)
    if match is None:
        return current_file, None
    prefix = match.group("prefix")
    if prefix.endswith("line"):
        return current_file, int(match.group("line"))
    return Path(prefix).resolve(), int(match.group("line"))


def clang_line_range(
    range_text: str,
    current_file: Path | None,
) -> tuple[Path | None, int | None, int | None]:
    """解析 Clang AST source range，并保留当前文件上下文。

    Args:
        range_text: Clang 文本 AST ``<...>`` 内的 range 内容。
        current_file: 上一条 location 已解析出的当前源文件。

    Returns:
        解析后的文件、起始行和结束行；单行 ``col`` 终点沿用起始行。
    """
    parts = [part.strip() for part in range_text.split(",", 1)]
    file_path, start = _clang_location(parts[0], current_file)
    if len(parts) == 1:
        return file_path, start, start
    end_file, end = _clang_location(parts[1], file_path)
    if end is None:
        end = start
    return end_file or file_path, start, end


def clang_error_detail(stderr: str, limit: int = 1600) -> str:
    """Keep the actionable tail of a long Clang diagnostic without losing context.

    Args:
        stderr: Complete Clang diagnostic text.
        limit: Maximum number of characters retained in the rendered detail.

    Returns:
        The complete diagnostic when short enough, otherwise a stable head/tail view.
    """
    detail = stderr.strip()
    if len(detail) <= limit:
        return detail
    head = max(200, limit // 4)
    tail = limit - head - len("\n...\n")
    return f"{detail[:head]}\n...\n{detail[-tail:]}"


def _cpp_include_directive(source_line: str) -> tuple[str, str] | None:
    """Parse one literal C/C++ include directive without inferring macro semantics.

    Args:
        source_line: One repository-authored source line.

    Returns:
        ``(opener, token)`` for literal quote/angle includes, otherwise ``None``.
    """
    stripped = source_line.lstrip()
    if not stripped.startswith("#"):
        return None
    directive = stripped[1:].lstrip()
    if not directive.startswith("include"):
        return None
    remainder = directive[len("include") :].lstrip()
    if len(remainder) < 3 or remainder[0] not in {'"', "<"}:
        return None
    opener = remainder[0]
    closing = '"' if opener == '"' else ">"
    end = remainder.find(closing, 1)
    if end <= 1:
        return None
    return opener, remainder[1:end].replace("\\", "/")


def clang_include_args(
    snapshot: RepositoryAnalysisSnapshot,
    root: Path,
    relative_paths: list[str],
) -> dict[str, tuple[str, ...]]:
    """Build deterministic per-translation-unit local include arguments.

    Only repository-authored literal includes participate. Quote includes may
    resolve to one unique local header root; angle includes without a path
    component remain compiler/system owned. Resolution follows only headers
    reachable from the requested translation unit, so an unrelated vendored
    ``include/`` tree cannot shadow system headers for every C++ file.

    Args:
        snapshot: Shared repository source snapshot.
        root: Materialized repository root passed to Clang.
        relative_paths: Translation units that will be parsed.

    Returns:
        Mapping from each requested path to flattened ``-I <dir>`` arguments.
    """
    cpp_units = {
        PurePosixPath(path).as_posix(): unit
        for path, unit in snapshot.language_units.items()
        if unit.language == "cpp"
    }
    roots_by_token: defaultdict[str, set[str]] = defaultdict(set)
    for relative in cpp_units:
        parts = PurePosixPath(relative).parts
        for start in range(len(parts)):
            token = "/".join(parts[start:])
            prefix = "/".join(parts[:start])
            roots_by_token[token].add(prefix if prefix else ".")

    arguments: dict[str, tuple[str, ...]] = {}
    for requested in relative_paths:
        requested_path = PurePosixPath(requested).as_posix()
        include_directories: set[Path] = set()
        pending = [requested_path] if requested_path in cpp_units else []
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            source_parent = PurePosixPath(current).parent.as_posix()
            for source_line in cpp_units[current].source.splitlines():
                directive = _cpp_include_directive(source_line)
                if directive is None:
                    continue
                opener, raw_token = directive
                token = posixpath.normpath(raw_token)
                relative_candidate = posixpath.normpath(
                    posixpath.join(source_parent, token)
                )
                if relative_candidate in cpp_units:
                    pending.append(relative_candidate)
                    continue
                if token in cpp_units:
                    include_directories.add(root.resolve())
                    pending.append(token)
                    continue
                candidates = roots_by_token[token]
                is_bare_angle = (opener, "/" in token) == ("<", False)
                if is_bare_angle:
                    continue
                if len(candidates) != 1:
                    continue
                include_root = next(iter(candidates))
                resolved = posixpath.normpath(posixpath.join(include_root, token))
                if resolved not in cpp_units:
                    continue
                include_directories.add((root / include_root).resolve())
                pending.append(resolved)

        flattened = [
            argument
            for include_dir in sorted(include_directories)
            for argument in ("-I", str(include_dir))
        ]
        arguments[requested] = tuple(flattened)
    return arguments


def _ast_node_column(text: str, kinds: tuple[str, ...] | set[str]) -> int:
    """返回 Clang 文本 AST 行中最左侧目标节点列。"""
    positions = [text.find(kind) for kind in kinds]
    valid = [position for position in positions if position >= 0]
    return min(valid, default=-1)


_CLANG_PATH_VALUE_FLAGS = {
    "-I",
    "-isystem",
    "-iquote",
    "-include",
    "-isysroot",
    "--sysroot",
}
_CLANG_OUTPUT_VALUE_FLAGS = {"-o", "-MF", "-MT", "-MQ", "-MJ"}
_CLANG_DROP_FLAGS = {"-c", "-M", "-MM", "-MD", "-MMD", "-MP"}


def _remap_build_path(
    spec: TranslationUnitBuildSpec,
    materialized_root: Path,
    value: str,
) -> str:
    """Remap repository-owned compile paths into the immutable materialized snapshot."""
    relative = repository_path(spec.repository_root, spec.directory, value)
    if relative is None:
        return value
    return str((materialized_root / relative).resolve())


def _clang_semantic_arguments(
    spec: TranslationUnitBuildSpec,
    materialized_root: Path,
    relative_path: str,
    compiler_index: int,
) -> list[str]:
    """Normalize one authored Clang command for AST dumping in a materialized snapshot."""
    command: list[str] = []
    arguments = spec.arguments[compiler_index + 1 :]
    index = 0
    source_added = False
    while index < len(arguments):
        argument = arguments[index]
        if argument in _CLANG_DROP_FLAGS:
            index += 1
            continue
        if argument in _CLANG_OUTPUT_VALUE_FLAGS:
            index += 2
            continue
        if any(
            argument.startswith(prefix) for prefix in ("-o", "-MF", "-MT", "-MQ", "-MJ")
        ):
            index += 1
            continue
        if argument in _CLANG_PATH_VALUE_FLAGS and index + 1 < len(arguments):
            command.extend(
                [
                    argument,
                    _remap_build_path(spec, materialized_root, arguments[index + 1]),
                ]
            )
            index += 2
            continue
        if argument.startswith("-I") and len(argument) > 2:
            command.append(
                "-I" + _remap_build_path(spec, materialized_root, argument[2:])
            )
            index += 1
            continue
        authored_path = repository_path(spec.repository_root, spec.directory, argument)
        if authored_path == relative_path:
            command.append(str((materialized_root / relative_path).resolve()))
            source_added = True
        elif Path(argument).is_absolute():
            command.append(_remap_build_path(spec, materialized_root, argument))
        else:
            command.append(argument)
        index += 1
    if not source_added:
        command.append(str((materialized_root / relative_path).resolve()))
    command.extend(["-fsyntax-only", "-Xclang", "-ast-dump"])
    return command


def clang_ast_invocation(
    spec: TranslationUnitBuildSpec,
    materialized_root: Path,
    relative_path: str,
) -> tuple[list[str], Path] | None:
    """Build a semantic AST invocation from a native-Clang build contract.

    Args:
        spec: Authoritative translation-unit build contract.
        materialized_root: Immutable source snapshot used for semantic extraction.
        relative_path: Repository-relative translation-unit source path.

    Returns:
        Clang AST command and working directory, or ``None`` when the authored build
        contract is not native Clang or the compiler is unavailable.
    """
    if spec.family is not CompilerFamily.CLANG:
        return None
    compiler_index = next(
        (
            index
            for index, argument in enumerate(spec.arguments)
            if argument == spec.compiler
        ),
        None,
    )
    if compiler_index is None:
        return None
    resolved_compiler = (
        shutil.which(spec.compiler)
        if not Path(spec.compiler).is_absolute()
        else spec.compiler
    )
    if resolved_compiler is None or not Path(resolved_compiler).exists():
        return None
    command = [
        str(resolved_compiler),
        *_clang_semantic_arguments(
            spec, materialized_root, relative_path, compiler_index
        ),
    ]
    try:
        relative_cwd = spec.directory.resolve().relative_to(
            spec.repository_root.resolve()
        )
    except ValueError:
        cwd = spec.directory
    else:
        cwd = (materialized_root / relative_cwd).resolve()
        cwd.mkdir(parents=True, exist_ok=True)
    return command, cwd


def _fallback_clang_invocation(
    clang: str | None,
    source: Path,
    root: Path,
    include_args: tuple[str, ...] | list[str],
) -> tuple[list[str], Path] | None:
    """Build the legacy best-effort Clang invocation when no build contract exists."""
    if clang is None:
        return None
    return (
        [
            clang,
            "-std=c++20",
            "-fsyntax-only",
            "-Xclang",
            "-ast-dump",
            *include_args,
            str(source),
        ],
        root,
    )


def cpp_semantic_invocation(
    relative_path: str,
    materialized_root: Path,
    build_specs: dict[str, TranslationUnitBuildSpec],
    fallback_clang: str | None,
    fallback_include_args: tuple[str, ...] | list[str],
) -> tuple[list[str], Path] | None:
    """Resolve one TU semantic-provider command without changing native build truth.

    Args:
        relative_path: Repository-relative translation-unit path.
        materialized_root: Immutable source snapshot used by the provider.
        build_specs: Authoritative compilation-database contracts keyed by TU path.
        fallback_clang: Legacy best-effort Clang executable used only without a build contract.
        fallback_include_args: Legacy include arguments paired with the best-effort fallback.

    Returns:
        Semantic-provider command and working directory, or ``None`` when authoritative
        build truth exists but no compatible provider is available.
    """
    if relative_path in build_specs:
        return clang_ast_invocation(
            build_specs[relative_path], materialized_root, relative_path
        )
    return _fallback_clang_invocation(
        fallback_clang,
        (materialized_root / relative_path).resolve(),
        materialized_root,
        fallback_include_args,
    )


def _cpp_metric_declaration(
    text: str,
    location_file: Path | None,
    source_path: Path,
    relative_path: str,
) -> tuple[Path | None, dict[str, Any] | None] | None:
    """Decode one Clang function declaration line into a metric record when relevant."""
    declaration = _FUNC_RE.search(text)
    declaration_column = _ast_node_column(text, _CPP_DECL_KINDS)
    if declaration is None or declaration_column < 0:
        return None
    location_file, start, end = clang_line_range(
        declaration.group("range"), location_file
    )
    if location_file != source_path or start is None or end is None:
        return location_file, None
    symbol = f"{declaration.group('name')} {declaration.group('sig')}"
    return location_file, {
        "path": relative_path,
        "line": start,
        "end_line": end,
        "lines": end - start + 1,
        "branches": 0,
        "max_nesting": 0,
        "depth": declaration_column,
        "symbol": symbol,
    }


def _update_cpp_metric_structure(
    text: str, current: dict[str, Any], controls: list[int]
) -> bool:
    """Consume one structural AST line and report whether the current function remains active."""
    structural_kinds = _BRANCH_NODES | _NEST_NODES | {"BinaryOperator"}
    node_column = _ast_node_column(text, structural_kinds)
    if node_column < 0:
        return True
    if node_column <= current["depth"]:
        return False
    while controls and controls[-1] >= node_column:
        controls.pop()
    kind = next(
        (
            candidate
            for candidate in structural_kinds
            if text.find(candidate) == node_column
        ),
        "",
    )
    if kind in _BRANCH_NODES or (
        kind == "BinaryOperator" and ("'&&'" in text or "'||'" in text)
    ):
        current["branches"] += 1
    if kind in _NEST_NODES:
        controls.append(node_column)
        current["max_nesting"] = max(current["max_nesting"], len(controls))
    return True


def _collect_cpp_metric_stream(
    stream: Any,
    relative_path: str,
    source_path: Path,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Collect function metrics from one successful Clang text-AST stream."""
    metrics: dict[tuple[str, str], dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    controls: list[int] = []
    location_file: Path | None = None
    for raw_line in stream:
        text = raw_line.rstrip("\n")
        declaration = _cpp_metric_declaration(
            text, location_file, source_path, relative_path
        )
        if declaration is not None:
            location_file, current = declaration
            controls = []
            if current is not None:
                metrics[(relative_path, current["symbol"])] = current
            continue
        if current is not None and not _update_cpp_metric_structure(
            text, current, controls
        ):
            current = None
            controls = []
    return metrics


def _cpp_metric_invocation(
    command: list[str],
    cwd: Path,
    relative_path: str,
    source_path: Path,
) -> dict[tuple[str, str], dict[str, Any]] | None:
    """Run one semantic-provider command and return atomic metrics for a successful TU."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    stream = process.stdout
    if stream is None:
        process.kill()
        process.wait()
        return None
    with stream:
        metrics = _collect_cpp_metric_stream(
            stream, relative_path, source_path.resolve()
        )
    return metrics if process.wait() == 0 else None


def _cpp_metrics(
    snapshot: RepositoryAnalysisSnapshot,
    paths: list[str],
    fallback_clang: str | None,
) -> tuple[dict[tuple[str, str], dict[str, Any]], set[str]]:
    """提取 C++ 结构指标，并显式返回语义 provider 可用的 TU。"""
    metrics: dict[tuple[str, str], dict[str, Any]] = {}
    available_paths: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="rqg-cpp-") as directory:
        root = Path(directory)
        for path, unit in snapshot.language_units.items():
            if unit.language == "cpp":
                destination = root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(unit.source, encoding="utf-8")
        include_args_by_path = clang_include_args(snapshot, root, paths)
        build_specs = load_compilation_database(snapshot)
        for relative_path in paths:
            source = root / relative_path
            if not source.is_file():
                continue
            invocation = cpp_semantic_invocation(
                relative_path,
                root,
                build_specs,
                fallback_clang,
                include_args_by_path[relative_path],
            )
            if invocation is None:
                continue
            command, cwd = invocation
            collected = _cpp_metric_invocation(command, cwd, relative_path, source)
            if collected is None:
                continue
            metrics.update(collected)
            available_paths.add(relative_path)
    return metrics, available_paths


def multilang_findings(
    target: RepositoryAnalysisSnapshot,
    config: GuardConfig,
    base: RepositoryAnalysisSnapshot | None = None,
) -> tuple[list[Finding], dict[str, Any]]:
    """从统一仓库快照生成 JS/TS/CSS/HTML/C++ 结构事实和发现。

    Args:
        target: 当前比较目标的统一仓库分析快照。
        config: 当前 Repository Quality Guard 扫描配置。
        base: 可选 Git 基线快照；存在时额外生成跨版本结构差分。

    Returns:
        多语言发现列表，以及稳定的文件计数与 parser 摘要。
    """
    target_facts = node_facts(target)
    scripts = [item for item in target_facts if item["language"] in SCRIPT_LANGUAGES]
    findings = _definition_findings(scripts, config)
    findings.extend(_script_architecture_findings(scripts))
    findings.extend(_css_findings(target_facts))
    findings.extend(_html_findings(target))

    if base is not None:
        base_facts = node_facts(base)
        findings.extend(_script_diff_findings(base_facts, target_facts))
        findings.extend(_cpp_changed_findings(base, target, config))

    findings.sort(
        key=lambda finding: (
            finding.path,
            finding.line,
            finding.column,
            finding.code,
            finding.symbol,
        )
    )
    counts = Counter(unit.language for unit in target.language_units.values())
    cpp_build_specs = load_compilation_database(target)
    compiler_families = Counter(spec.family.value for spec in cpp_build_specs.values())
    summary = {
        "files": dict(sorted(counts.items())),
        "total_files": sum(counts.values()),
        "findings": len(findings),
        "parser": (
            "bundled TypeScript/PostCSS + HTMLParser; C++ build contract + optional Clang AST"
        ),
        "cpp_build_contracts": len(cpp_build_specs),
        "cpp_compiler_families": dict(sorted(compiler_families.items())),
    }
    return findings, summary
