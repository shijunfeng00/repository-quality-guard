from __future__ import annotations

import ast
from collections.abc import Iterable
from functools import cache

MAPPING_ANNOTATIONS = {
    "dict",
    "Dict",
    "Mapping",
    "MutableMapping",
    "TypedDict",
    "Json",
    "JSON",
}
MAPPING_NAME_HINTS = {
    "cfg",
    "config",
    "context",
    "data",
    "item",
    "kwargs",
    "metadata",
    "options",
    "payload",
    "record",
    "result",
    "state",
}

LOOKUP_MAPPING_NAMES = {
    "children",
    "classes",
    "contracts",
    "expected",
    "grouped",
    "groups",
    "handlers",
    "indexes",
    "methods",
    "module_paths",
    "parents",
    "profiles",
    "registry",
    "routes",
    "signatures",
    "sources",
    "target_sources",
}
LOOKUP_MAPPING_PREFIXES = ("by_",)
LOOKUP_MAPPING_SUFFIXES = (
    "_by_id",
    "_by_name",
    "_cache",
    "_index",
    "_indexes",
    "_lookup",
    "_map",
    "_mapping",
    "_registry",
    "_table",
)

TRIVIAL_WRAPPER_ASSIGN_RETURN_STATEMENTS = 2


def is_lookup_mapping_name(name: str) -> bool:
    """判断映射名称是否明确表达键到值的查找表，而非字段记录。

    Args:
        name: 映射接收者的局部名称。

    Returns:
        名称具有 registry/index/by-key 等查找表语义时返回 True。
    """
    lowered = name.lower().lstrip("_")
    return (
        lowered in LOOKUP_MAPPING_NAMES
        or lowered.startswith(LOOKUP_MAPPING_PREFIXES)
        or lowered.endswith(LOOKUP_MAPPING_SUFFIXES)
    )


def dotted_name(node: ast.AST) -> str:
    """
    提取名称或属性表达式的点分限定名称。

    Args:
        node: 待分析的 AST 节点。

    Returns:
        可解析时返回点分名称，否则返回空字符串。
    """
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    else:
        return ""
    return ".".join(reversed(parts))


def decorator_names(nodes: Iterable[ast.expr]) -> tuple[str, ...]:
    """
    提取装饰器表达式中的可解析名称。

    Args:
        nodes: 待分析的 AST 节点序列。

    Returns:
        按源码顺序排列的装饰器名称元组。
    """
    result: list[str] = []
    for node in nodes:
        target = node.func if isinstance(node, ast.Call) else node
        name = dotted_name(target)
        if name:
            result.append(name)
    return tuple(result)


@cache
def parent_nodes(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """
    建立 AST 子节点到直接父节点的映射。

    Args:
        tree: 模块或子树根节点。

    Returns:
        子节点到父节点的映射。
    """
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def enclosing_class_name(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> str:
    """
    查找节点最近的外层类名称。

    Args:
        node: 起始 AST 节点。
        parents: 子节点到父节点的映射。

    Returns:
        最近外层类名称；不存在时返回空字符串。
    """
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return current.name
        current = parents.get(current)
    return ""


def annotation_is_mapping(node: ast.expr | None) -> bool:
    """
    判断类型标注是否表示映射类结构。

    Args:
        node: 待分析的 AST 节点。

    Returns:
        标注表示映射结构时返回 True，否则返回 False。
    """
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in MAPPING_ANNOTATIONS
    if isinstance(node, ast.Attribute):
        return node.attr in MAPPING_ANNOTATIONS
    if isinstance(node, ast.Subscript):
        return annotation_is_mapping(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return annotation_is_mapping(node.left) or annotation_is_mapping(node.right)
    return False


def assigned_names(node: ast.expr) -> set[str]:
    """
    提取赋值目标中包含的名称。

    Args:
        node: 待分析的 AST 节点。

    Returns:
        赋值目标中出现的名称集合。
    """
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in node.elts:
            names.update(assigned_names(item))
        return names
    return set()


def is_constant_default(node: ast.stmt) -> bool:
    """
    判断语句是否把异常转换为固定默认结果。

    Args:
        node: 待分析的 AST 节点。

    Returns:
        语句返回固定默认结果或 pass 时返回 True。
    """
    if isinstance(node, ast.Pass):
        return True
    if not isinstance(node, ast.Return):
        return False
    value = node.value
    return value is None or isinstance(
        value, (ast.Constant, ast.Dict, ast.List, ast.Set, ast.Tuple)
    )


def body_without_docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.stmt]:
    """
    返回排除首部 docstring 后的函数体。

    Args:
        node: 待分析的 AST 节点。

    Returns:
        不包含 docstring 语句的函数体列表。
    """
    body = list(node.body)
    has_docstring = (
        bool(body)
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )
    return body[1:] if has_docstring else body


def _trivial_wrapper_value(
    body: list[ast.stmt],
    property_facade: bool,
) -> tuple[ast.expr | None, str]:
    """提取薄包装调用表达式，或 property 直接暴露的 private 目标。"""
    if len(body) == 1:
        statement = body[0]
        target: ast.expr | None = None
        if property_facade and isinstance(statement, ast.Return):
            target = statement.value
        if (
            property_facade
            and isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.value, ast.Name)
        ):
            target = statement.targets[0]
        if (
            property_facade
            and isinstance(statement, ast.AnnAssign)
            and isinstance(statement.value, ast.Name)
        ):
            target = statement.target
        if property_facade and isinstance(statement, ast.Delete) and len(statement.targets) == 1:
            target = statement.targets[0]
        if isinstance(target, ast.Attribute) and target.attr.startswith("_"):
            return None, dotted_name(target)
        value = statement.value if isinstance(statement, (ast.Return, ast.Expr)) else None
        return value, ""
    if len(body) != TRIVIAL_WRAPPER_ASSIGN_RETURN_STATEMENTS:
        return None, ""
    if not isinstance(body[1], ast.Return):
        return None, ""
    assignment = body[0]
    assigned_name = ""
    assigned_value: ast.expr | None = None
    if isinstance(assignment, ast.Assign) and len(assignment.targets) == 1:
        target = assignment.targets[0]
        assigned_name = target.id if isinstance(target, ast.Name) else ""
        assigned_value = assignment.value if assigned_name else None
    if isinstance(assignment, ast.AnnAssign) and isinstance(assignment.target, ast.Name):
        assigned_name = assignment.target.id
        assigned_value = assignment.value
    returned = body[1].value
    if not assigned_name or not isinstance(returned, ast.Name):
        return None, ""
    return (assigned_value, "") if returned.id == assigned_name else (None, "")


def is_trivial_wrapper(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """识别单层调用转发，以及只暴露 private 字段的 property 外壳。

    Args:
        node: 待分析的函数或异步函数 AST 节点。

    Returns:
        薄包装目标的点分名称；函数包含独立逻辑时返回空字符串。
    """
    body = body_without_docstring(node)
    decorators = decorator_names(node.decorator_list)
    property_facade = any(
        name.rsplit(".", 1)[-1] in {"property", "cached_property", "setter", "deleter"}
        for name in decorators
    )
    value, direct_target = _trivial_wrapper_value(body, property_facade)
    if direct_target:
        return direct_target
    if isinstance(value, ast.Await):
        value = value.value
    if not isinstance(value, ast.Call):
        return ""
    parameter_names = {
        argument.arg
        for argument in (
            list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        )
    }
    optional_arguments = (node.args.vararg, node.args.kwarg)
    parameter_names.update(argument.arg for argument in optional_arguments if argument is not None)
    forwarded: set[str] = set()
    for argument in value.args:
        if isinstance(argument, ast.Name):
            forwarded.add(argument.id)
        elif isinstance(argument, ast.Starred) and isinstance(argument.value, ast.Name):
            forwarded.add(argument.value.id)
    forwarded.update(
        keyword.value.id for keyword in value.keywords if isinstance(keyword.value, ast.Name)
    )
    meaningful_parameters = parameter_names - {"self", "cls"}
    if meaningful_parameters and not meaningful_parameters.issubset(forwarded):
        return ""
    return dotted_name(value.func)


def normalized_function_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """
    生成忽略源码位置的标准化函数体指纹。

    Args:
        node: 待分析的 AST 节点。

    Returns:
        可用于重复实现比较的 AST 字符串。
    """
    module = ast.Module(body=body_without_docstring(node), type_ignores=[])
    return ast.dump(module, annotate_fields=True, include_attributes=False)


class ComplexityVisitor(ast.NodeVisitor):
    """
    统计函数体中的分支数量与最大控制流嵌套。

    遍历时忽略嵌套定义，避免把内部函数复杂度计入外层函数。
    """

    def __init__(self) -> None:
        """
        初始化复杂度统计状态。

        Returns:
            None。
        """
        self.branches = 0
        self.current_nesting = 0
        self.max_nesting = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """
        跳过嵌套同步函数定义。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """
        跳过嵌套异步函数定义。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """
        跳过嵌套类定义。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        return None

    def _visit_nested(self, node: ast.AST, branch_weight: int = 1) -> None:
        """
        统计一个会增加控制流嵌套的 AST 节点。

        Args:
            node: 待分析的 AST 节点。
            branch_weight: 该节点对分支计数的权重。

        Returns:
            None。
        """
        self.branches += branch_weight
        self.current_nesting += 1
        self.max_nesting = max(self.max_nesting, self.current_nesting)
        self.generic_visit(node)
        self.current_nesting -= 1

    def visit_If(self, node: ast.If) -> None:
        """
        统计 if 控制流节点。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self._visit_nested(node)

    def visit_For(self, node: ast.For) -> None:
        """
        统计同步 for 控制流节点。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self._visit_nested(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        """
        统计异步 for 控制流节点。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self._visit_nested(node)

    def visit_While(self, node: ast.While) -> None:
        """
        统计 while 控制流节点。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self._visit_nested(node)

    def visit_With(self, node: ast.With) -> None:
        """
        统计同步上下文管理节点。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self._visit_nested(node, branch_weight=0)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        """
        统计异步上下文管理节点。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self._visit_nested(node, branch_weight=0)

    def visit_Try(self, node: ast.Try) -> None:
        """
        统计异常处理控制流节点。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self._visit_nested(node, branch_weight=max(1, len(node.handlers)))

    def visit_Match(self, node: ast.Match) -> None:
        """
        统计模式匹配控制流节点。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self._visit_nested(node, branch_weight=max(1, len(node.cases)))

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        """
        统计布尔表达式中的额外分支。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self.branches += max(0, len(node.values) - 1)
        self.generic_visit(node)
