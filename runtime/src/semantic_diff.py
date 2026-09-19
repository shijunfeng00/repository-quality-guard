from __future__ import annotations

import ast
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import tree_sitter_python
from apted import APTED, Config
from tree_sitter import Language, Node, Parser

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .config import GuardConfig, is_test_path
from .fallback_laundering import FallbackPattern, extract_fallback_patterns
from .git_utils import run_readonly_git
from .model import Finding

_DEFAULT_SIMILARITY_THRESHOLD = 0.72
_DEFAULT_COARSE_THRESHOLD = 0.48
_DEFAULT_MAX_TREE_NODES = 360
_DEFAULT_CALL_DEPTH = 5
_MIN_TREE_SIZE_RATIO = 0.45


@dataclass(slots=True)
class CanonicalNode:
    """表示可供 APTED 比较的规范化语法树节点。

    ``name`` 保存稳定标签，``children`` 保留有序层级关系。
    """

    name: str
    children: list[CanonicalNode] = field(default_factory=list)

    @property
    def size(self) -> int:
        """计算当前子树节点数量。

        Returns:
            包含当前节点在内的递归节点总数。
        """
        return 1 + sum(child.size for child in self.children)

    def labels(self) -> Counter[str]:
        """统计当前子树的节点标签。

        Returns:
            以标签为键、出现次数为值的多重集合。
        """
        counts: Counter[str] = Counter({self.name: 1})
        for child in self.children:
            counts.update(child.labels())
        return counts


@dataclass(slots=True, frozen=True)
class BehaviorObligation:
    """表示一项与具体语法无关的“缺失时继续执行”行为。

    该对象同时记录行为键、原始位置和跨函数调用路径。
    """

    kind: str
    receiver: str
    selector: str
    default: str
    syntax: str
    origin_path: str
    origin_qualname: str
    line: int
    via: tuple[str, ...] = ()

    @property
    def semantic_key(self) -> tuple[str, str, str]:
        """生成忽略语法和默认值后的修复义务键。

        Returns:
            由行为类型、接收者和选择器组成的稳定元组。
        """
        return self.kind, self.receiver, self.selector


@dataclass(slots=True, frozen=True)
class LocalCall:
    """表示可解析到同一变更集合内函数的一次调用。

    调用参数已转换为调用者作用域中的稳定表达式。
    """

    target_hint: str
    arguments: tuple[str, ...]
    keywords: tuple[tuple[str, str], ...]


@dataclass(slots=True)
class FunctionModel:
    """保存函数的结构树、局部行为和可解析调用。

    路径和限定名共同构成跨文件函数映射的稳定身份。
    """

    path: str
    qualname: str
    parameters: tuple[str, ...]
    tree: CanonicalNode
    obligations: tuple[BehaviorObligation, ...]
    calls: tuple[LocalCall, ...]
    line: int

    @property
    def key(self) -> str:
        """生成路径与限定名组成的稳定函数键。

        Returns:
            ``path::qualname`` 形式的函数身份。
        """
        return f"{self.path}::{self.qualname}"

    @property
    def short_name(self) -> str:
        """提取不含所有者前缀的函数名。

        Returns:
            限定名最后一段。
        """
        return self.qualname.rsplit(".", 1)[-1]

    @property
    def owner(self) -> str:
        """提取类或嵌套作用域限定名。

        Returns:
            函数名前的所有者路径；模块级函数返回空字符串。
        """
        return self.qualname.rsplit(".", 1)[0] if "." in self.qualname else ""


class _AptedConfig(Config):
    """为规范化代码树提供单位成本 APTED 配置。"""

    def rename(self, node1: CanonicalNode, node2: CanonicalNode) -> int:
        """计算两个规范节点的重命名成本。

        Args:
            node1: 旧规范树节点。
            node2: 新规范树节点。

        Returns:
            标签相同返回 0，否则返回 1。
        """
        return int(node1.name != node2.name)

    def children(self, node: CanonicalNode) -> list[CanonicalNode]:
        """读取规范节点的有序子节点。

        Args:
            node: 待遍历的规范树节点。

        Returns:
            节点直接拥有的子节点列表。
        """
        return node.children


class _NameTable:
    """把参数和局部标识符映射到稳定占位符。"""

    def __init__(self, parameters: Iterable[str]) -> None:
        """按声明顺序固定参数占位符。

        Args:
            parameters: 当前函数参数名称序列。

        Returns:
            None。
        """
        self.parameters = {name: f"ARG{index}" for index, name in enumerate(parameters)}
        self.locals: dict[str, str] = {}

    def name(self, raw: str) -> str:
        """把原始标识符映射为稳定占位符。

        Args:
            raw: 源码中的标识符名称。

        Returns:
            参数、局部变量或特殊接收者的规范名称。
        """
        if raw in {"self", "cls"}:
            return raw.upper()
        if raw in self.parameters:
            return self.parameters[raw]
        if raw not in self.locals:
            self.locals[raw] = f"VAR{len(self.locals)}"
        return self.locals[raw]


class _AstCanonicalizer:
    """把 Python AST 转换为去标识符、去位置的有序规范树。"""

    _SKIPPED_FIELDS: ClassVar[frozenset[str]] = frozenset({"ctx", "type_comment", "type_ignores"})

    def __init__(
        self,
        parameters: Iterable[str],
        patterns: Iterable[FallbackPattern] = (),
    ) -> None:
        """初始化作用域名称表与行为模式索引。

        Args:
            parameters: 当前函数参数名称。
            patterns: 当前函数内已经识别的 fallback 行为。

        Returns:
            None。
        """
        self.names = _NameTable(parameters)
        self.patterns_by_line: dict[int, list[FallbackPattern]] = defaultdict(list)
        for pattern in patterns:
            self.patterns_by_line[pattern.line].append(pattern)

    def convert(self, node: ast.AST | None) -> CanonicalNode:
        """递归转换 AST，并保留控制结构与调用形状。

        Args:
            node: 待转换的 Python AST 节点。

        Returns:
            去除位置和普通标识符差异后的规范树节点。
        """
        if node is None:
            return CanonicalNode("NONE")
        behavior = self._behavior_node(node) if isinstance(node, (ast.expr, ast.stmt)) else None
        if behavior is not None:
            return behavior
        special = self._special_node(node)
        return special if special is not None else self._generic_node(node)

    def _special_node(self, node: ast.AST) -> CanonicalNode | None:
        """转换需要名称或字面量特殊处理的节点。"""
        if isinstance(node, ast.Name):
            return CanonicalNode(f"Name:{self.names.name(node.id)}")
        if isinstance(node, ast.arg):
            return CanonicalNode(f"Arg:{self.names.name(node.arg)}")
        if isinstance(node, ast.Attribute):
            return CanonicalNode(f"Attribute:{node.attr}", [self.convert(node.value)])
        if isinstance(node, ast.Constant):
            return CanonicalNode(self._constant_label(node.value))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return self._function_node(node)
        return None

    def _function_node(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> CanonicalNode:
        """转换函数主体，并剔除首行 docstring。"""
        body = list(node.body)
        if body and self._is_docstring(body[0]):
            body = body[1:]
        return CanonicalNode(type(node).__name__, [self.convert(item) for item in body])

    def _generic_node(self, node: ast.AST) -> CanonicalNode:
        """按 AST 字段顺序转换普通节点。"""
        children: list[CanonicalNode] = []
        for field_name, value in ast.iter_fields(node):
            if field_name in self._SKIPPED_FIELDS:
                continue
            if isinstance(value, ast.AST):
                children.append(CanonicalNode(field_name, [self.convert(value)]))
                continue
            if isinstance(value, list):
                converted = [self.convert(item) for item in value if isinstance(item, ast.AST)]
                if converted:
                    children.append(CanonicalNode(field_name, converted))
            elif isinstance(value, str) and field_name in {"name", "arg", "attr"}:
                children.append(CanonicalNode(f"{field_name}:ID"))
        return CanonicalNode(type(node).__name__, children)

    def _behavior_node(self, node: ast.AST) -> CanonicalNode | None:
        """把不同语法表达的同一 fallback 行为折叠为统一节点。"""
        line = node.lineno
        patterns = self.patterns_by_line.get(line, [])
        expected = {
            "dict.get": ast.Call,
            "dict.setdefault": ast.Call,
            "getattr": ast.Call,
            "hasattr": ast.Call,
            "membership-ifexp": ast.IfExp,
            "membership-init": ast.If,
            "membership-guard": ast.If,
            "membership-branch": ast.If,
            "dict-default-merge": ast.Assign,
        }
        for pattern in patterns:
            expected_type = expected.get(pattern.syntax)
            if pattern.syntax.startswith("try-except-"):
                expected_type = ast.Try
            if expected_type is None or not isinstance(node, expected_type):
                continue
            try:
                receiver_expr = ast.parse(pattern.receiver_text, mode="eval").body
                receiver = _canonical_expression(receiver_expr, self.names)
            except SyntaxError:
                receiver = "RECEIVER"
            try:
                selector_expr = ast.parse(pattern.selector_text, mode="eval").body
                selector = _selector(selector_expr)
            except SyntaxError:
                selector = "SELECTOR"
            return CanonicalNode(
                f"Behavior:{pattern.kind}",
                [
                    CanonicalNode(f"Receiver:{receiver}"),
                    CanonicalNode(f"Selector:{selector}"),
                    CanonicalNode(f"Default:{pattern.default}"),
                ],
            )
        return None

    @staticmethod
    def _constant_label(value: object) -> str:
        """把字面量压缩为稳定类别，避免常量换皮破坏映射。"""
        if value is None:
            return "Const:NONE"
        if isinstance(value, bool):
            return "Const:BOOL"
        if isinstance(value, str):
            return "Const:STR"
        if isinstance(value, (int, float, complex)):
            return "Const:NUM"
        if isinstance(value, bytes):
            return "Const:BYTES"
        return f"Const:{type(value).__name__}"

    @staticmethod
    def _is_docstring(statement: ast.stmt) -> bool:
        """判断语句是否为函数首个字符串表达式。"""
        return (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )


class _FunctionCollector(ast.NodeVisitor):
    """收集模块内函数并保留类、函数嵌套限定名。"""

    def __init__(self) -> None:
        """初始化作用域栈和函数收集结果。

        Returns:
            None。
        """
        self.stack: list[str] = []
        self.items: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """进入类作用域并继续收集内部函数。

        Args:
            node: 当前类定义节点。

        Returns:
            None。
        """
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """收集同步函数及其嵌套定义。

        Args:
            node: 当前同步函数定义。

        Returns:
            None。
        """
        self._collect(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """收集异步函数及其嵌套定义。

        Args:
            node: 当前异步函数定义。

        Returns:
            None。
        """
        self._collect(node)

    def _collect(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """记录限定名并继续收集嵌套函数。"""
        qualname = ".".join((*self.stack, node.name))
        self.items.append((qualname, node))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()


class _OwnedVisitor(ast.NodeVisitor):
    """只遍历当前函数直接拥有的节点。"""

    def __init__(self, root: ast.AST) -> None:
        """保存当前定义的遍历根。

        Args:
            root: 当前函数或方法 AST 根节点。

        Returns:
            None。
        """
        self.root = root
        self.nodes: list[ast.AST] = []

    def generic_visit(self, node: ast.AST) -> None:
        """遍历当前定义直接拥有的节点。

        Args:
            node: 当前访问的 AST 节点。

        Returns:
            None。
        """
        nested = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
        if node is not self.root and nested:
            return
        self.nodes.append(node)
        super().generic_visit(node)


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """按调用位置返回函数全部参数名。"""
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    names = [item.arg for item in positional]
    if args.vararg is not None:
        names.append(args.vararg.arg)
    names.extend(item.arg for item in args.kwonlyargs)
    if args.kwarg is not None:
        names.append(args.kwarg.arg)
    return tuple(names)


def _canonical_expression(node: ast.AST, names: _NameTable) -> str:
    """把调用参数和行为接收者转换为可替换的稳定表达式。"""
    if isinstance(node, ast.Name):
        return names.name(node.id)
    if isinstance(node, ast.Attribute):
        return f"ATTR({_canonical_expression(node.value, names)},{node.attr})"
    if isinstance(node, ast.Subscript):
        return f"ITEM({_canonical_expression(node.value, names)},{_selector(node.slice)})"
    if isinstance(node, ast.Constant):
        return _selector(node)
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def _selector(node: ast.AST) -> str:
    """保留字段选择器字面量，其他表达式使用无位置信息 AST。"""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def _pattern_obligation(
    pattern: FallbackPattern,
    parameters: tuple[str, ...],
) -> BehaviorObligation:
    """把 QG176 局部模式转换为跨函数可替换行为。"""
    names = _NameTable(parameters)
    try:
        receiver_node = ast.parse(pattern.receiver_text, mode="eval").body
        receiver = _canonical_expression(receiver_node, names)
    except SyntaxError:
        receiver = pattern.receiver
    try:
        selector_node = ast.parse(pattern.selector_text, mode="eval").body
        selector = _selector(selector_node)
    except SyntaxError:
        selector = pattern.selector
    return BehaviorObligation(
        kind=pattern.kind,
        receiver=receiver,
        selector=selector,
        default=pattern.default,
        syntax=pattern.syntax,
        origin_path=pattern.path,
        origin_qualname=pattern.qualname,
        line=pattern.line,
    )


def _call_target(node: ast.Call, owner: str) -> str | None:
    """把本模块直接调用解析为限定名提示。"""
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if not isinstance(function, ast.Attribute):
        return None
    if isinstance(function.value, ast.Name) and function.value.id in {"self", "cls"}:
        return f"{owner}.{function.attr}" if owner else function.attr
    if isinstance(function.value, ast.Name) and function.value.id[:1].isupper():
        return f"{function.value.id}.{function.attr}"
    return None


def _local_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parameters: tuple[str, ...],
    owner: str,
) -> tuple[LocalCall, ...]:
    """提取当前函数直接拥有的、可静态解析的本地调用。"""
    visitor = _OwnedVisitor(node)
    visitor.visit(node)
    names = _NameTable(parameters)
    calls: list[LocalCall] = []
    for item in visitor.nodes:
        if not isinstance(item, ast.Call):
            continue
        target = _call_target(item, owner)
        if target is None:
            continue
        calls.append(
            LocalCall(
                target_hint=target,
                arguments=tuple(_canonical_expression(arg, names) for arg in item.args),
                keywords=tuple(
                    (keyword.arg or "**", _canonical_expression(keyword.value, names))
                    for keyword in item.keywords
                ),
            )
        )
    return tuple(calls)


def _tree_sitter_root(source: str) -> CanonicalNode:
    """在 Python AST 失败时生成容错结构树。

    Tree-sitter Python 是不可降级的必需解析器。即使 dirty 文件暂时存在
    语法错误，也必须生成真实错误恢复树；依赖缺失时启动阶段直接失败。
    """
    parser = Parser(Language(tree_sitter_python.language()))
    root = parser.parse(source.encode("utf-8")).root_node

    def convert(node: Node) -> CanonicalNode:
        """递归转换 Tree-sitter 节点。

        Args:
            node: 当前 Tree-sitter 语法节点。

        Returns:
            去除标识符与字面量差异后的规范结构树。
        """
        node_type = node.type
        if node_type == "identifier":
            label = "identifier"
        elif node_type in {"string", "integer", "float", "true", "false", "none"}:
            label = "literal"
        else:
            label = node_type
        children = [convert(child) for child in node.named_children]
        return CanonicalNode(label, children)

    return convert(root)


def build_function_models(
    path: str,
    source: str,
    tree: ast.Module | None = None,
) -> dict[str, FunctionModel]:
    """从 Python 源码构建函数级规范树、行为义务和局部调用图。

    Args:
        path: 仓库相对 Python 文件路径。
        source: 文件源码文本。
        tree: 可选的已解析 AST；存在时避免重复 Python AST 解析。

    Returns:
        以函数限定名为键的模型映射；语法错误时返回恢复结构模型。
    """
    module = tree
    if module is None:
        try:
            module = ast.parse(source)
        except SyntaxError:
            recovery = _tree_sitter_root(source)
            return {
                "<syntax-recovery>": FunctionModel(
                    path=path,
                    qualname="<syntax-recovery>",
                    parameters=(),
                    tree=recovery,
                    obligations=(),
                    calls=(),
                    line=1,
                )
            }
    collector = _FunctionCollector()
    collector.visit(module)
    patterns_by_qualname: dict[str, list[FallbackPattern]] = defaultdict(list)
    for pattern in extract_fallback_patterns(path, source, module):
        patterns_by_qualname[pattern.qualname].append(pattern)
    models: dict[str, FunctionModel] = {}
    for qualname, node in collector.items:
        parameters = _parameters(node)
        owner = qualname.rsplit(".", 1)[0] if "." in qualname else ""
        function_patterns = patterns_by_qualname[qualname]
        canonicalizer = _AstCanonicalizer(parameters, function_patterns)
        models[qualname] = FunctionModel(
            path=path,
            qualname=qualname,
            parameters=parameters,
            tree=canonicalizer.convert(node),
            obligations=tuple(
                _pattern_obligation(pattern, parameters) for pattern in function_patterns
            ),
            calls=_local_calls(node, parameters, owner),
            line=node.lineno,
        )
    return models


def _coarse_similarity(left: CanonicalNode, right: CanonicalNode) -> float:
    """使用节点标签 Dice 系数快速淘汰明显不相似函数。"""
    left_counts = left.labels()
    right_counts = right.labels()
    overlap = sum((left_counts & right_counts).values())
    total = sum(left_counts.values()) + sum(right_counts.values())
    return 1.0 if total == 0 else (2.0 * overlap) / total


def tree_similarity(
    left: CanonicalNode,
    right: CanonicalNode,
    *,
    max_nodes: int = _DEFAULT_MAX_TREE_NODES,
) -> tuple[float, str]:
    """计算两棵规范代码树的结构相似度。

    Args:
        left: 旧版本规范树。
        right: 新版本规范树。
        max_nodes: 允许执行 APTED 的最大单树节点数。

    Returns:
        0 到 1 的相似度以及实际使用的算法名称。
    """
    coarse = _coarse_similarity(left, right)
    if coarse < _DEFAULT_COARSE_THRESHOLD:
        return coarse, "label-dice"
    left_size = left.size
    right_size = right.size
    if max(left_size, right_size) > max_nodes:
        return coarse, "label-dice-large-tree"
    distance = float(APTED(left, right, _AptedConfig()).compute_edit_distance())
    denominator = max(left_size, right_size, 1)
    return max(0.0, 1.0 - distance / denominator), "apted"


def _rewrite_receiver(receiver: str, replacements: dict[str, str]) -> str:
    """将被调用函数参数占位符替换为调用点表达式。"""
    rewritten = receiver
    for parameter, argument in sorted(replacements.items(), key=lambda item: -len(item[0])):
        if rewritten == parameter:
            return argument
        rewritten = rewritten.replace(f"({parameter},", f"({argument},")
    return rewritten


def _resolve_call(
    caller: FunctionModel,
    call: LocalCall,
    models: dict[str, FunctionModel],
) -> FunctionModel | None:
    """优先在同文件解析调用，再尝试全局唯一函数名。"""
    same_path = [model for model in models.values() if model.path == caller.path]
    for model in same_path:
        if model.qualname == call.target_hint:
            return model
    if caller.owner:
        owned_hint = f"{caller.owner}.{call.target_hint}"
        for model in same_path:
            if model.qualname == owned_hint:
                return model
    same_path_short = [model for model in same_path if model.short_name == call.target_hint]
    if len(same_path_short) == 1:
        return same_path_short[0]
    global_exact = [model for model in models.values() if model.qualname == call.target_hint]
    if len(global_exact) == 1:
        return global_exact[0]
    global_short = [model for model in models.values() if model.short_name == call.target_hint]
    return global_short[0] if len(global_short) == 1 else None


def _summary(
    model: FunctionModel,
    models: dict[str, FunctionModel],
    *,
    depth: int,
    stack: tuple[str, ...] = (),
) -> tuple[BehaviorObligation, ...]:
    """沿变更函数调用图汇总行为义务，并把 helper 参数代回调用者。"""
    result = list(model.obligations)
    if depth <= 0 or model.key in stack:
        return tuple(result)
    next_stack = (*stack, model.key)
    for call in model.calls:
        target = _resolve_call(model, call, models)
        if target is None or target.key in next_stack:
            continue
        replacements = {f"ARG{index}": argument for index, argument in enumerate(call.arguments)}
        for obligation in _summary(target, models, depth=depth - 1, stack=next_stack):
            result.append(
                BehaviorObligation(
                    kind=obligation.kind,
                    receiver=_rewrite_receiver(obligation.receiver, replacements),
                    selector=obligation.selector,
                    default=obligation.default,
                    syntax=obligation.syntax,
                    origin_path=obligation.origin_path,
                    origin_qualname=obligation.origin_qualname,
                    line=obligation.line,
                    via=(*obligation.via, target.key),
                )
            )
    return tuple(result)


def _changed_python_paths(root: Path, base_revision: str, staged: bool) -> list[str]:
    """返回 Git 比较范围内修改过的生产 Python 文件。"""
    args = ["diff"]
    if staged:
        args.append("--cached")
    args.extend(["--name-only", "--diff-filter=ACMRD", base_revision, "--", "*.py"])
    result = run_readonly_git(root, *args)
    if result.returncode != 0:
        return []
    paths = set(result.stdout.splitlines())
    if not staged:
        untracked = run_readonly_git(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "*.py",
        )
        if untracked.returncode == 0:
            paths.update(untracked.stdout.splitlines())
    return sorted(
        path
        for path in paths
        if path and not is_test_path(path) and not path.startswith(".agents/")
    )


def _git_source(root: Path, revision: str, path: str) -> str | None:
    """读取指定 Git revision 的源码。"""
    result = run_readonly_git(root, "show", f"{revision}:{path}")
    return result.stdout if result.returncode == 0 else None


def _target_source(root: Path, path: str, staged: bool) -> str | None:
    """读取暂存区或工作树源码。"""
    if staged:
        result = run_readonly_git(root, "show", f":{path}")
        return result.stdout if result.returncode == 0 else None
    try:
        return (root / path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _match_functions(
    before: dict[str, FunctionModel],
    after: dict[str, FunctionModel],
    *,
    threshold: float,
    max_nodes: int,
) -> list[tuple[FunctionModel, FunctionModel, float, str]]:
    """先按路径+限定名匹配，再用规范 AST + APTED 映射改名或跨文件移动。"""
    matches: list[tuple[FunctionModel, FunctionModel, float, str]] = []
    used_after: set[str] = set()
    for key, old in before.items():
        if key not in after:
            continue
        new = after[key]
        matches.append((old, new, 1.0, "path+qualname"))
        used_after.add(key)
    unmatched_before = [
        model for key, model in before.items() if key not in after and model.obligations
    ]
    unmatched_after = [model for key, model in after.items() if key not in used_after]
    candidates: list[tuple[float, FunctionModel, FunctionModel, str]] = []
    before_metrics = {
        model.key: (model.tree.size, model.tree.labels()) for model in unmatched_before
    }
    after_metrics = {model.key: (model.tree.size, model.tree.labels()) for model in unmatched_after}
    for old in unmatched_before:
        old_size, old_labels = before_metrics[old.key]
        for new in unmatched_after:
            new_size, new_labels = after_metrics[new.key]
            max_size = max(old_size, new_size, 1)
            size_upper_bound = min(old_size, new_size) / max_size
            if size_upper_bound < max(_MIN_TREE_SIZE_RATIO, threshold):
                continue
            label_overlap = sum((old_labels & new_labels).values())
            label_upper_bound = label_overlap / max_size
            if label_upper_bound < threshold:
                continue
            similarity, algorithm = tree_similarity(old.tree, new.tree, max_nodes=max_nodes)
            if similarity >= threshold:
                candidates.append((similarity, old, new, algorithm))
    used_before_keys: set[str] = set()
    used_after_keys: set[str] = set()
    for similarity, old, new, algorithm in sorted(
        candidates, key=lambda item: item[0], reverse=True
    ):
        if old.key in used_before_keys or new.key in used_after_keys:
            continue
        used_before_keys.add(old.key)
        used_after_keys.add(new.key)
        matches.append((old, new, similarity, algorithm))
    return matches


def _removed_direct_obligations(
    old: FunctionModel,
    new: FunctionModel,
) -> list[BehaviorObligation]:
    """返回在函数自身局部语法中消失的旧行为义务。"""
    old_counts = Counter((item.semantic_key, item.default, item.syntax) for item in old.obligations)
    new_counts = Counter((item.semantic_key, item.default, item.syntax) for item in new.obligations)
    removed_budget = old_counts - (old_counts & new_counts)
    removed: list[BehaviorObligation] = []
    for item in old.obligations:
        key = (item.semantic_key, item.default, item.syntax)
        if removed_budget[key] <= 0:
            continue
        removed.append(item)
        removed_budget[key] -= 1
    return removed


def _advanced_finding(
    old: FunctionModel,
    new: FunctionModel,
    obligation: BehaviorObligation,
    survivor: BehaviorObligation,
    similarity: float,
    algorithm: str,
    base_revision: str,
    project_name: str,
) -> Finding:
    """生成跨函数或改名移动后的修复义务未完成 Critical。"""
    path = new.path
    movement = survivor.origin_qualname != new.qualname or old.qualname != new.qualname
    return Finding(
        code="QG177",
        severity="critical",
        confidence="high",
        path=path,
        line=survivor.line,
        column=1,
        symbol=new.qualname,
        message=(
            "规范化 AST 与调用图显示旧修复义务仍然存在；问题只是被改名、移动或搬入 helper，"
            "没有改变缺失后继续执行的行为。"
        ),
        suggestion=(
            "必填字段必须在类型/schema 中固定并直接访问；真正可选行为只能集中在唯一输入适配"
            "边界。不要通过 helper、异常、membership、默认初始化或函数改名隐藏原行为。"
        ),
        evidence={
            "base_revision": base_revision,
            "project_name": project_name,
            "before_function": old.key,
            "after_function": new.key,
            "before_syntax": obligation.syntax,
            "after_syntax": survivor.syntax,
            "kind": obligation.kind,
            "receiver": survivor.receiver,
            "selector": survivor.selector,
            "survivor_origin": f"{survivor.origin_path}::{survivor.origin_qualname}",
            "call_path": list(survivor.via),
            "function_mapping": algorithm,
            "tree_similarity": round(similarity, 4),
            "moved_or_renamed": movement,
        },
    )


def _collect_project_models(
    root: Path,
    base_revision: str,
    staged: bool,
    base_analysis: RepositoryAnalysisSnapshot | None = None,
    target_analysis: RepositoryAnalysisSnapshot | None = None,
) -> tuple[dict[str, FunctionModel], dict[str, FunctionModel]]:
    """构建 Git before/after 的变更函数模型集合。"""
    before_models: dict[str, FunctionModel] = {}
    after_models: dict[str, FunctionModel] = {}
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
        if before_source is not None:
            for model in build_function_models(
                path,
                before_source,
                before_unit.tree if before_unit is not None else None,
            ).values():
                before_models[model.key] = model
        if after_source is not None:
            for model in build_function_models(
                path,
                after_source,
                after_unit.tree if after_unit is not None else None,
            ).values():
                after_models[model.key] = model
    return before_models, after_models


def _findings_for_match(
    old: FunctionModel,
    new: FunctionModel,
    after_models: dict[str, FunctionModel],
    similarity: float,
    algorithm: str,
    base_revision: str,
    project_name: str,
) -> list[Finding]:
    """核验一对映射函数的旧行为是否仍存在于新调用闭包。"""
    removed = _removed_direct_obligations(old, new)
    if not removed:
        return []
    survivors_by_key: dict[tuple[str, str, str], list[BehaviorObligation]] = defaultdict(list)
    for item in _summary(new, after_models, depth=_DEFAULT_CALL_DEPTH):
        survivors_by_key[item.semantic_key].append(item)
    findings: list[Finding] = []
    for obligation in removed:
        survivor = next(
            (
                item
                for item in survivors_by_key[obligation.semantic_key]
                if item.origin_path != new.path
                or item.origin_qualname != new.qualname
                or old.key != new.key
            ),
            None,
        )
        if survivor is None:
            continue
        findings.append(
            _advanced_finding(
                old,
                new,
                obligation,
                survivor,
                similarity,
                algorithm,
                base_revision,
                project_name,
            )
        )
    return findings


def semantic_repair_findings(
    root: Path,
    config: GuardConfig,
    base_revision: str,
    staged: bool = False,
    base_analysis: RepositoryAnalysisSnapshot | None = None,
    target_analysis: RepositoryAnalysisSnapshot | None = None,
) -> list[Finding]:
    """使用规范 AST、APTED 和跨函数摘要检测深层无效修复。

    Args:
        root: Git 工作树根目录。
        config: 当前仓库质量配置。
        base_revision: Git before 快照。
        staged: 是否以暂存区作为 after。
        base_analysis: 可选的 before 统一源码/AST 快照。
        target_analysis: 可选的 after 统一源码/AST 快照。

    Returns:
        改名、移动或搬入 helper 后仍保留旧行为的 QG177 Critical。
    """
    before_models, after_models = _collect_project_models(
        root,
        base_revision,
        staged,
        base_analysis,
        target_analysis,
    )
    project_name = config.project_name or "generic"
    findings: list[Finding] = []
    for old, new, similarity, algorithm in _match_functions(
        before_models,
        after_models,
        threshold=_DEFAULT_SIMILARITY_THRESHOLD,
        max_nodes=_DEFAULT_MAX_TREE_NODES,
    ):
        findings.extend(
            _findings_for_match(
                old,
                new,
                after_models,
                similarity,
                algorithm,
                base_revision,
                project_name,
            )
        )
    unique: dict[tuple[str, int, str, str, str], Finding] = {}
    for finding in findings:
        evidence = finding.evidence
        identity = (
            finding.path,
            finding.line,
            finding.symbol,
            str(evidence["selector"]),
            str(evidence["survivor_origin"]),
        )
        if identity not in unique:
            unique[identity] = finding
    return sorted(unique.values(), key=lambda item: (item.path, item.line, item.symbol, item.code))
