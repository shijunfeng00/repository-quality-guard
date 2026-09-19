from __future__ import annotations

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .config import GuardConfig, is_test_path
from .git_utils import run_readonly_git
from .model import Finding
from .policy_common import is_boundary_module

_DICT_DEFAULT_ARG_COUNT = 2
_GETATTR_DEFAULT_ARG_COUNT = 3
_MISSING_EXCEPTIONS = {"AttributeError", "KeyError"}
_BROAD_EXCEPTIONS = {"Exception", "BaseException"}
_EMPTY_CALL_NAMES = {"dict", "list", "set", "tuple"}
_DYNAMIC_ATTRIBUTE_CALLS = {"getattr", "hasattr"}


@dataclass(slots=True, frozen=True)
class FallbackPattern:
    """表示一处“缺失时继续执行”的契约兜底行为。

    同时保留 AST 结构键和可读表达式，供 Git before/after 多重集合匹配。
    """

    path: str
    qualname: str
    line: int
    kind: str
    receiver: str
    selector: str
    receiver_text: str
    selector_text: str
    default: str
    syntax: str

    @property
    def semantic_key(self) -> tuple[str, str, str]:
        """返回忽略具体语法后的行为匹配键。

        Returns:
            由行为种类、接收者和选择器组成的稳定键。
        """
        normalized_kind = (
            "missing_read" if self.kind in {"mapping_read", "attribute_read"} else self.kind
        )
        return normalized_kind, self.receiver, self.selector

    @property
    def full_key(self) -> tuple[str, str, str, str, str, str]:
        """返回包含定义、默认值和语法的完整比较键。

        Returns:
            可用于 Counter 多重集合差分的完整模式键。
        """
        return (
            self.qualname,
            self.kind,
            self.receiver,
            self.selector,
            self.default,
            self.syntax,
        )


class _OwnedNodeVisitor(ast.NodeVisitor):
    """遍历一个定义直接拥有的节点，跳过内嵌定义。"""

    def __init__(self, root: ast.AST) -> None:
        """保存遍历根并初始化节点列表。

        Args:
            root: 当前函数或方法的 AST 根节点。

        Returns:
            None。
        """
        self.root = root
        self.nodes: list[ast.AST] = []

    def generic_visit(self, node: ast.AST) -> None:
        """记录节点，并阻止进入当前定义内部的新定义。

        Args:
            node: 当前访问的 AST 节点。

        Returns:
            None。
        """
        nested_definition = isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        )
        if node is not self.root and nested_definition:
            return
        self.nodes.append(node)
        super().generic_visit(node)


class _DefinitionCollector(ast.NodeVisitor):
    """收集模块中的函数、方法和嵌套函数限定名。"""

    def __init__(self) -> None:
        """初始化限定名栈与定义结果。

        Returns:
            None。
        """
        self.stack: list[str] = []
        self.definitions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """进入类作用域并继续收集其方法。

        Args:
            node: 类定义节点。

        Returns:
            None。
        """
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """收集同步函数。

        Args:
            node: 同步函数定义节点。

        Returns:
            None。
        """
        self._collect_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """收集异步函数。

        Args:
            node: 异步函数定义节点。

        Returns:
            None。
        """
        self._collect_function(node)

    def _collect_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """记录函数限定名并继续收集嵌套定义。"""
        qualname = ".".join((*self.stack, node.name))
        self.definitions.append((qualname, node))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()


def _expr_key(node: ast.AST | None) -> str:
    """生成忽略位置、保留结构的表达式键。"""
    return (
        "<missing>"
        if node is None
        else ast.dump(
            node,
            annotate_fields=False,
            include_attributes=False,
        )
    )


def _display_expr(node: ast.AST | None) -> str:
    """尽可能生成供报告展示的短表达式。"""
    if node is None:
        return "<missing>"
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return _expr_key(node)


def _empty_default_key(node: ast.AST) -> str:
    """返回空容器默认值的稳定文本。

    Args:
        node: 待识别的默认表达式。

    Returns:
        命中空容器时返回规范文本，否则返回空字符串。
    """
    empty_nodes = (ast.List, ast.Dict, ast.Set, ast.Tuple)
    if not isinstance(node, empty_nodes):
        return ""
    if isinstance(node, ast.Dict):
        empty = not node.keys
        label = "{}"
    else:
        empty = not node.elts
        label = {ast.List: "[]", ast.Set: "set()", ast.Tuple: "()"}[type(node)]
    return label if empty else ""


def _default_key(node: ast.AST | None) -> str:
    """把常见默认表达式压缩为稳定类别。

    Args:
        node: 默认值表达式；缺省参数传入 None。

    Returns:
        适合 before/after 比较的规范默认值文本。
    """
    if node is None:
        return "None"
    if isinstance(node, ast.Constant):
        return repr(node.value)
    empty = _empty_default_key(node)
    if empty:
        return empty
    empty_call = (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and not node.args
        and not node.keywords
        and node.func.id in _EMPTY_CALL_NAMES
    )
    return f"{node.func.id}()" if empty_call else _display_expr(node)


def _pattern(
    path: str,
    qualname: str,
    node: ast.AST,
    kind: str,
    receiver: ast.AST,
    selector: ast.AST,
    default: ast.AST | None,
    syntax: str,
) -> FallbackPattern:
    """构造包含结构键和可读文本的 fallback 模式。"""
    return FallbackPattern(
        path=path,
        qualname=qualname,
        line=node.lineno,
        kind=kind,
        receiver=_expr_key(receiver),
        selector=_expr_key(selector),
        receiver_text=_display_expr(receiver),
        selector_text=_display_expr(selector),
        default=_default_key(default),
        syntax=syntax,
    )


def _membership(node: ast.AST) -> tuple[ast.AST, ast.AST, bool] | None:
    """解析 `key in mapping` / `key not in mapping`。"""
    valid_compare = (
        isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1
    )
    if not valid_compare:
        return None
    operator = node.ops[0]
    if not isinstance(operator, (ast.In, ast.NotIn)):
        return None
    return node.comparators[0], node.left, isinstance(operator, ast.In)


def _subscript_matches(node: ast.AST, receiver: ast.AST, selector: ast.AST) -> bool:
    """判断表达式是否为同一 receiver/selector 的直接下标。"""
    return (
        isinstance(node, ast.Subscript)
        and _expr_key(node.value) == _expr_key(receiver)
        and _expr_key(node.slice) == _expr_key(selector)
    )


def _contains_subscript(node: ast.AST, receiver: ast.AST, selector: ast.AST) -> bool:
    """判断表达式内部是否读取同一 receiver/selector。"""
    return any(
        isinstance(item, ast.Subscript)
        and _subscript_matches(item, receiver, selector)
        and isinstance(item.ctx, ast.Load)
        for item in ast.walk(node)
    )


def _handler_exception_names(handler: ast.ExceptHandler) -> set[str]:
    """提取 except 捕获的异常名称。"""
    exception_type = handler.type
    if exception_type is None:
        return {"<bare>"}
    candidates = exception_type.elts if isinstance(exception_type, ast.Tuple) else [exception_type]
    return {_display_expr(item).rsplit(".", 1)[-1] for item in candidates}


def _fallback_value(statements: list[ast.stmt]) -> ast.AST | None:
    """提取异常或守卫分支中的固定兜底结果。"""
    for statement in statements:
        if isinstance(statement, ast.Return):
            return statement.value
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            return statement.value
    return None


def _assigned_subscript_default(
    statements: list[ast.stmt],
    receiver: ast.AST,
    selector: ast.AST,
) -> ast.AST | None:
    """查找 `mapping[key] = default` 并返回默认表达式。"""
    for statement in statements:
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        if any(_subscript_matches(target, receiver, selector) for target in targets):
            return statement.value
    return None


def _accesses_in_statements(
    statements: list[ast.stmt],
) -> list[tuple[str, ast.AST, ast.AST]]:
    """收集语句中的下标与属性读取。"""
    module = ast.Module(body=statements, type_ignores=[])
    result: list[tuple[str, ast.AST, ast.AST]] = []
    for item in ast.walk(module):
        if isinstance(item, ast.Subscript) and isinstance(item.ctx, ast.Load):
            result.append(("mapping_read", item.value, item.slice))
        elif isinstance(item, ast.Attribute) and isinstance(item.ctx, ast.Load):
            result.append(("attribute_read", item.value, ast.Constant(item.attr)))
    return result


def _call_patterns(
    path: str,
    qualname: str,
    node: ast.Call,
) -> list[FallbackPattern]:
    """提取 get、setdefault、hasattr 与 getattr 形式。"""
    patterns: list[FallbackPattern] = []
    if isinstance(node.func, ast.Attribute) and node.args:
        receiver = node.func.value
        if node.func.attr in {"get", "setdefault"}:
            default = node.args[1] if len(node.args) >= _DICT_DEFAULT_ARG_COUNT else None
            kind = "mapping_read" if node.func.attr == "get" else "mapping_init"
            patterns.append(
                _pattern(
                    path,
                    qualname,
                    node,
                    kind,
                    receiver,
                    node.args[0],
                    default,
                    f"dict.{node.func.attr}",
                )
            )
    dynamic_attribute = (
        isinstance(node.func, ast.Name)
        and node.func.id in _DYNAMIC_ATTRIBUTE_CALLS
        and len(node.args) >= _DICT_DEFAULT_ARG_COUNT
        and isinstance(node.args[1], ast.Constant)
    )
    if dynamic_attribute:
        default = node.args[2] if len(node.args) >= _GETATTR_DEFAULT_ARG_COUNT else None
        patterns.append(
            _pattern(
                path,
                qualname,
                node,
                "attribute_read",
                node.args[0],
                node.args[1],
                default,
                node.func.id,
            )
        )
    return patterns


def _ifexp_patterns(
    path: str,
    qualname: str,
    node: ast.IfExp,
) -> list[FallbackPattern]:
    """提取 membership 三元表达式兜底。"""
    membership = _membership(node.test)
    if membership is None:
        return []
    receiver, selector, present = membership
    direct = node.body if present else node.orelse
    fallback = node.orelse if present else node.body
    if not _contains_subscript(direct, receiver, selector):
        return []
    return [
        _pattern(
            path,
            qualname,
            node,
            "mapping_read",
            receiver,
            selector,
            fallback,
            "membership-ifexp",
        )
    ]


def _if_patterns(
    path: str,
    qualname: str,
    node: ast.If,
) -> list[FallbackPattern]:
    """提取 membership 守卫与显式初始化。"""
    membership = _membership(node.test)
    if membership is None:
        return []
    receiver, selector, present = membership
    patterns: list[FallbackPattern] = []
    if not present:
        inserted = _assigned_subscript_default(node.body, receiver, selector)
        if inserted is not None:
            patterns.append(
                _pattern(
                    path,
                    qualname,
                    node,
                    "mapping_init",
                    receiver,
                    selector,
                    inserted,
                    "membership-init",
                )
            )
        fallback = _fallback_value(node.body)
        if fallback is not None:
            patterns.append(
                _pattern(
                    path,
                    qualname,
                    node,
                    "mapping_read",
                    receiver,
                    selector,
                    fallback,
                    "membership-guard",
                )
            )
    elif node.orelse:
        fallback = _fallback_value(node.orelse)
        if fallback is not None:
            patterns.append(
                _pattern(
                    path,
                    qualname,
                    node,
                    "mapping_read",
                    receiver,
                    selector,
                    fallback,
                    "membership-branch",
                )
            )
    return patterns


def _try_patterns(
    path: str,
    qualname: str,
    node: ast.Try,
) -> list[FallbackPattern]:
    """提取 KeyError/AttributeError/宽异常兜底。"""
    patterns: list[FallbackPattern] = []
    accesses = _accesses_in_statements(node.body)
    for handler in node.handlers:
        names = _handler_exception_names(handler)
        if not names & (_MISSING_EXCEPTIONS | _BROAD_EXCEPTIONS):
            continue
        fallback = _fallback_value(handler.body)
        if fallback is None:
            continue
        syntax = "try-except-" + "+".join(sorted(names))
        patterns.extend(
            _pattern(
                path,
                qualname,
                node,
                kind,
                receiver,
                selector,
                fallback,
                syntax,
            )
            for kind, receiver, selector in accesses
        )
    return patterns


def _dict_merge_patterns(
    path: str,
    qualname: str,
    node: ast.Assign,
) -> list[FallbackPattern]:
    """提取 `{default, **mapping}` 默认合并。"""
    if len(node.targets) != 1:
        return []
    target = node.targets[0]
    if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Dict):
        return []
    pairs = list(zip(node.value.keys, node.value.values, strict=True))
    unpacked_self = any(
        key is None and _expr_key(value) == _expr_key(target) for key, value in pairs
    )
    if not unpacked_self:
        return []
    return [
        _pattern(
            path,
            qualname,
            node,
            "mapping_init",
            target,
            key,
            value,
            "dict-default-merge",
        )
        for key, value in pairs
        if key is not None
    ]


def _patterns_for_node(
    path: str,
    qualname: str,
    node: ast.AST,
) -> list[FallbackPattern]:
    """按节点类型分派 fallback 提取器。"""
    if isinstance(node, ast.Call):
        return _call_patterns(path, qualname, node)
    if isinstance(node, ast.IfExp):
        return _ifexp_patterns(path, qualname, node)
    if isinstance(node, ast.If):
        return _if_patterns(path, qualname, node)
    if isinstance(node, ast.Try):
        return _try_patterns(path, qualname, node)
    if isinstance(node, ast.Assign):
        return _dict_merge_patterns(path, qualname, node)
    return []


def _function_patterns(
    path: str,
    qualname: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[FallbackPattern]:
    """提取单个函数内的契约兜底行为。"""
    visitor = _OwnedNodeVisitor(node)
    visitor.visit(node)
    patterns: list[FallbackPattern] = []
    for item in visitor.nodes:
        patterns.extend(_patterns_for_node(path, qualname, item))
    return patterns


def extract_fallback_patterns(
    path: str,
    source: str,
    tree: ast.Module | None = None,
) -> list[FallbackPattern]:
    """从一个 Python 模块提取全部函数级兜底行为。

    Args:
        path: 仓库相对 Python 文件路径。
        source: 待解析源码文本。
        tree: 可选的已解析 AST；存在时直接复用。

    Returns:
        按源码遍历顺序排列的 fallback 行为模式。
    """
    if tree is None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
    collector = _DefinitionCollector()
    collector.visit(tree)
    patterns: list[FallbackPattern] = []
    for qualname, node in collector.definitions:
        patterns.extend(_function_patterns(path, qualname, node))
    return patterns


def _changed_python_paths(root: Path, base_revision: str, staged: bool) -> list[str]:
    """返回 Git 比较范围内的生产 Python 路径。"""
    args = ["diff"]
    if staged:
        args.append("--cached")
    args.extend(["--name-only", "--diff-filter=ACMR", base_revision, "--", "*.py"])
    result = run_readonly_git(root, *args)
    if result.returncode != 0:
        return []
    return sorted(
        path
        for path in result.stdout.splitlines()
        if path and not is_test_path(path) and not path.startswith(".agents/")
    )


def _git_source(root: Path, revision: str, path: str) -> str | None:
    """读取 Git revision 中的 UTF-8 Python 源码。"""
    result = run_readonly_git(root, "show", f"{revision}:{path}")
    return result.stdout if result.returncode == 0 else None


def _target_source(root: Path, path: str, staged: bool) -> str | None:
    """读取工作树或暂存区中的目标源码。"""
    if staged:
        result = run_readonly_git(root, "show", f":{path}")
        return result.stdout if result.returncode == 0 else None
    candidate = root / path
    try:
        return candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _changed_patterns(
    before: list[FallbackPattern],
    after: list[FallbackPattern],
) -> tuple[list[FallbackPattern], list[FallbackPattern]]:
    """从 before/after 多重集合中移除未变化模式。"""
    before_counts = Counter(pattern.full_key for pattern in before)
    after_counts = Counter(pattern.full_key for pattern in after)
    unchanged = before_counts & after_counts
    removed_budget = before_counts - unchanged
    added_budget = after_counts - unchanged
    removed: list[FallbackPattern] = []
    added: list[FallbackPattern] = []
    for pattern in before:
        if removed_budget[pattern.full_key] <= 0:
            continue
        removed.append(pattern)
        removed_budget[pattern.full_key] -= 1
    for pattern in after:
        if added_budget[pattern.full_key] <= 0:
            continue
        added.append(pattern)
        added_budget[pattern.full_key] -= 1
    return removed, added


def _laundering_finding(
    path: str,
    old: FallbackPattern,
    new: FallbackPattern,
    config: GuardConfig,
    base_revision: str,
) -> Finding:
    """把一对语义相同、语法不同的 fallback 生成 Critical。"""
    module = Path(path).with_suffix("").as_posix().replace("/", ".")
    boundary = is_boundary_module(module, config.boundary_module_markers)
    return Finding(
        code="QG176",
        severity="critical",
        confidence="high",
        path=path,
        line=new.line,
        column=1,
        symbol=new.qualname,
        message=(
            f"契约兜底从 `{old.syntax}` 改写为 `{new.syntax}`，但缺失时继续执行的语义没有被删除。"
        ),
        suggestion=(
            "不要把被禁止的 .get/hasattr/setdefault/宽异常换成 membership、"
            "try/except、默认合并或显式初始化。必填字段直接使用 []/属性；"
            "真正可选性只允许在唯一输入适配边界通过 schema/类型集中表达。"
        ),
        evidence={
            "before_syntax": old.syntax,
            "after_syntax": new.syntax,
            "kind": new.kind,
            "receiver": new.receiver_text,
            "selector": new.selector_text,
            "before_default": old.default,
            "after_default": new.default,
            "boundary_module": boundary,
            "base_revision": base_revision,
        },
    )


def fallback_laundering_findings(
    root: Path,
    config: GuardConfig,
    base_revision: str,
    staged: bool = False,
    base_analysis: RepositoryAnalysisSnapshot | None = None,
    target_analysis: RepositoryAnalysisSnapshot | None = None,
) -> list[Finding]:
    """检测把被禁止兜底改写为等价控制流的无效修复。

    Args:
        root: Git 工作树根目录。
        config: 当前仓库质量配置。
        base_revision: Git before 快照。
        staged: 是否读取暂存区作为 after。
        base_analysis: 可选的 before 统一源码/AST 快照。
        target_analysis: 可选的 after 统一源码/AST 快照。

    Returns:
        每一处 fallback 行为洗白对应的 QG176 Critical。
    """
    findings: list[Finding] = []
    for path in _changed_python_paths(root, base_revision, staged):
        before_unit = base_analysis.unit(path) if base_analysis is not None else None
        after_unit = target_analysis.unit(path) if target_analysis is not None else None
        before_source = (
            before_unit.source
            if before_unit is not None
            else _git_source(root, base_revision, path)
        )
        after_source = (
            after_unit.source
            if after_unit is not None
            else _target_source(root, path, staged)
        )
        if before_source is None or after_source is None:
            continue
        before = extract_fallback_patterns(
            path,
            before_source,
            before_unit.tree if before_unit is not None else None,
        )
        after = extract_fallback_patterns(
            path,
            after_source,
            after_unit.tree if after_unit is not None else None,
        )
        removed, added = _changed_patterns(before, after)
        added_by_key: dict[tuple[str, str, str, str], list[FallbackPattern]] = defaultdict(list)
        for pattern in added:
            added_by_key[(pattern.qualname, *pattern.semantic_key)].append(pattern)
        seen: set[tuple[str, str, str, str, str, str]] = set()
        for old in removed:
            match_key = (old.qualname, *old.semantic_key)
            for new in added_by_key[match_key]:
                identity = (*match_key, old.syntax, new.syntax)
                if old.syntax == new.syntax or identity in seen:
                    continue
                seen.add(identity)
                findings.append(_laundering_finding(path, old, new, config, base_revision))
                break
    findings.sort(key=lambda item: (item.path, item.line, item.symbol, item.code))
    return findings
