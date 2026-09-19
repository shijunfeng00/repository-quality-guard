from __future__ import annotations

import ast

from .facts import ModuleFacts
from .model import Finding
from .policy_common import (
    annotation_names,
    call_name,
    direct_body_nodes,
    function_returns,
    make_finding,
)


def check_return_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
) -> list[Finding]:
    """
    检查明确不兼容的返回类型、可疑字典形态和隐式 None 路径。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。

    Returns:
        返回值相关发现列表。
    """
    returns = function_returns(node)
    shapes = {return_shape(item.value) for item in returns}
    concrete_shapes = {shape for shape in shapes if shape not in {"none", "dynamic"}}
    annotation_names_set = annotation_names(node.returns)
    findings = [
        incompatible_return_finding(
            facts, node, qualname, returns, shapes, concrete_shapes, annotation_names_set
        ),
        dictionary_shape_finding(facts, node, qualname, returns, concrete_shapes),
        implicit_none_finding(facts, node, qualname, shapes, concrete_shapes, annotation_names_set),
    ]
    return [item for item in findings if item is not None] + protocol_text_findings(
        facts, node, qualname, returns, annotation_names_set
    )


def incompatible_return_finding(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    returns: list[ast.Return],
    shapes: set[str],
    concrete_shapes: set[str],
    annotation_names_set: set[str],
) -> Finding | None:
    """
    检查同一函数是否返回互不兼容的顶层类型。

    Args:
        facts: 当前模块静态事实。
        node: 待检查函数。
        qualname: 函数限定名称。
        returns: 函数直接拥有的 return 节点。
        shapes: 全部返回结构签名。
        concrete_shapes: 排除 None 和动态值后的结构签名。
        annotation_names_set: 返回注解涉及的类型名称。

    Returns:
        顶层返回类型不兼容时返回发现，否则返回 None。
    """
    top_level_shapes = {top_level_return_shape(shape) for shape in concrete_shapes}
    flexible_annotation = bool(annotation_names_set & {"Any", "Json", "JSON", "object", "Union"})
    if len(top_level_shapes) <= 1 or flexible_annotation:
        return None
    return make_finding(
        facts,
        returns[0] if returns else node,
        "QG045",
        f"`{qualname}` 存在不兼容返回类型：{sorted(top_level_shapes)}。",
        symbol=qualname,
        severity="error",
        suggestion="同一内部接口固定顶层返回类型；合法多形态返回应使用明确联合类型。",
        evidence={"shapes": sorted(shapes)},
    )


def dictionary_shape_finding(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    returns: list[ast.Return],
    concrete_shapes: set[str],
) -> Finding | None:
    """
    检查字典返回值是否存在多组字段集合。

    Args:
        facts: 当前模块静态事实。
        node: 待检查函数。
        qualname: 函数限定名称。
        returns: 函数直接拥有的 return 节点。
        concrete_shapes: 已确认的返回结构签名。

    Returns:
        字典字段集合存在多种形态时返回候选发现，否则返回 None。
    """
    dictionary_shapes = {shape for shape in concrete_shapes if shape.startswith("dict:")}
    if len(dictionary_shapes) <= 1:
        return None
    return make_finding(
        facts,
        returns[0] if returns else node,
        "QG144",
        f"`{qualname}` 的字典返回字段集合存在 {len(dictionary_shapes)} 种形态。",
        symbol=qualname,
        severity="info",
        confidence="medium",
        suggestion=(
            "确认这是带判别字段的合法联合，还是同一接口在不同路径遗漏字段；"
            "不要仅因字段集合不同就机械补空值。"
        ),
        evidence={"shapes": sorted(dictionary_shapes)},
    )


def implicit_none_finding(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    shapes: set[str],
    concrete_shapes: set[str],
    annotation_names_set: set[str],
) -> Finding | None:
    """
    检查非空返回契约是否存在落到函数末尾的路径。

    Args:
        facts: 当前模块静态事实。
        node: 待检查函数。
        qualname: 函数限定名称。
        shapes: 全部返回结构签名。
        concrete_shapes: 已确认的返回结构签名。
        annotation_names_set: 返回注解涉及的类型名称。

    Returns:
        存在隐式 None 路径时返回发现，否则返回 None。
    """
    returns_value = bool(concrete_shapes or "dynamic" in shapes)
    nullable_annotation = bool(annotation_names_set & {"None", "NoneType", "Optional"})
    valid_contract = node.returns is not None and returns_value and not nullable_annotation
    if not valid_contract or is_generator_function(node) or block_terminates(node.body):
        return None
    return make_finding(
        facts,
        node,
        "QG046",
        f"`{qualname}` 部分路径返回值，其他路径可能隐式返回 None。",
        symbol=qualname,
        severity="error",
        suggestion="补齐全部可正常落到函数末尾的控制流路径，或把可空性写入类型契约。",
        evidence={"shapes": sorted(shapes)},
    )


def protocol_text_findings(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    returns: list[ast.Return],
    annotation_names_set: set[str],
) -> list[Finding]:
    """
    检查协议语义函数是否直接返回 str 或 repr 展示文本。

    Args:
        facts: 当前模块静态事实。
        node: 待检查函数。
        qualname: 函数限定名称。
        returns: 函数直接拥有的 return 节点。
        annotation_names_set: 返回注解涉及的类型名称。

    Returns:
        展示文本替代正式协议的候选发现列表。
    """
    protocol_name = node.name.lower()
    markers = ("serializ", "history", "openai", "payload", "response", "record")
    if not any(marker in protocol_name for marker in markers) or "str" in annotation_names_set:
        return []
    return [
        make_finding(
            facts,
            return_node,
            "QG047",
            f"`{qualname}` 直接返回 str()/repr()，可能以展示文本替代正式协议。",
            symbol=qualname,
            severity="warning",
            suggestion="跨模块、HTTP 或持久化数据使用显式序列化方法和固定结构。",
        )
        for return_node in returns
        if isinstance(return_node.value, ast.Call)
        and call_name(return_node.value) in {"str", "repr"}
    ]


def top_level_return_shape(shape: str) -> str:
    """
    把字典字段签名等细节归并为顶层返回类型。

    Args:
        shape: return_shape 生成的结构签名。

    Returns:
        顶层返回类型名称。
    """
    return shape.split(":", 1)[0]


def is_generator_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """
    判断函数体是否包含属于当前函数的 yield。

    Args:
        node: 待检查函数。

    Returns:
        当前函数是生成器时返回 True。
    """
    return any(isinstance(item, (ast.Yield, ast.YieldFrom)) for item in direct_body_nodes(node))


def return_shape(node: ast.expr | None) -> str:
    """
    对返回表达式生成保守的结构分类。

    Args:
        node: Return 节点中的值表达式。

    Returns:
        明确容器或标量类型；无法静态确定时返回 dynamic。
    """
    if node is None:
        return "none"
    container_shape = container_return_shape(node)
    if container_shape is not None:
        return container_shape
    expression_shape = expression_return_shape(node)
    if expression_shape is not None:
        return expression_shape
    call_shape = call_return_shape(node)
    return call_shape or "dynamic"


def container_return_shape(node: ast.expr) -> str | None:
    """
    识别字典、元组、列表和集合返回结构。

    Args:
        node: 返回表达式。

    Returns:
        可识别的容器结构签名；不是容器时返回 None。
    """
    if isinstance(node, ast.Dict):
        return dictionary_return_shape(node)
    if isinstance(node, ast.Tuple):
        return f"tuple:{len(node.elts)}"
    if isinstance(node, (ast.List, ast.ListComp)):
        return "list"
    if isinstance(node, (ast.Set, ast.SetComp)):
        return "set"
    return None


def expression_return_shape(node: ast.expr) -> str | None:
    """
    识别常量、布尔运算和条件表达式返回结构。

    Args:
        node: 返回表达式。

    Returns:
        可识别的表达式结构；无法判断时返回 None。
    """
    if isinstance(node, ast.Constant):
        return "none" if node.value is None else type(node.value).__name__
    if isinstance(node, ast.Compare) or (
        isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
    ):
        return "bool"
    if isinstance(node, ast.BoolOp):
        return merged_expression_shape(node.values)
    if isinstance(node, ast.IfExp):
        return merged_expression_shape([node.body, node.orelse])
    return None


def call_return_shape(node: ast.expr) -> str | None:
    """
    识别显式类型构造和文本转换调用。

    Args:
        node: 返回表达式。

    Returns:
        已知调用的返回类型；不是已知调用时返回 None。
    """
    if not isinstance(node, ast.Call):
        return None
    name = call_name(node)
    if name in {"str", "repr"}:
        return "str"
    if name in {"bool", "dict", "float", "int", "list", "set", "tuple"}:
        return name
    return None


def merged_expression_shape(values: list[ast.expr]) -> str:
    """
    合并 `or/and` 或条件表达式各分支的保守返回类型。

    Args:
        values: 可能成为表达式结果的分支表达式。

    Returns:
        可确认的共同类型；无法确认时返回 dynamic。
    """
    shapes = {return_shape(value) for value in values}
    concrete = {shape for shape in shapes if shape not in {"dynamic", "none"}}
    return next(iter(concrete)) if len(concrete) == 1 else "dynamic"


def dictionary_return_shape(node: ast.Dict) -> str:
    """
    生成字典返回值的静态字段集合签名。

    Args:
        node: 字典返回表达式。

    Returns:
        排序后的字符串键签名；动态键以问号表示。
    """
    keys: list[str] = []
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.append(key.value)
        else:
            keys.append("?")
    return "dict:" + ",".join(sorted(keys))


def block_terminates(body: list[ast.stmt]) -> bool:
    """
    保守判断语句块是否在全部可正常执行路径上显式终止。

    Args:
        body: 待分析的语句列表。

    Returns:
        顺序执行必然遇到 return、raise 或完整终止分支时返回 True。
    """
    return any(statement_terminates(statement) for statement in body)


def statement_terminates(statement: ast.stmt) -> bool:
    """
    判断单条复合语句是否保证当前函数控制流终止。

    Args:
        statement: 待分析语句。

    Returns:
        所有可正常完成路径均 return 或 raise 时返回 True。
    """
    match statement:
        case ast.Return() | ast.Raise():
            return True
        case ast.If(body=body, orelse=orelse):
            return bool(orelse) and block_terminates(body) and block_terminates(orelse)
        case ast.With(body=body) | ast.AsyncWith(body=body):
            return block_terminates(body)
        case ast.Try():
            return try_statement_terminates(statement)
        case ast.Match(cases=cases):
            exhaustive = any(is_catch_all_pattern(case.pattern) for case in cases)
            return exhaustive and all(block_terminates(case.body) for case in cases)
        case _:
            return False


def try_statement_terminates(statement: ast.Try) -> bool:
    """
    判断 try/except/else/finally 结构是否保证终止。

    Args:
        statement: try 语句。

    Returns:
        所有正常与异常路径均终止时返回 True。
    """
    if statement.finalbody and block_terminates(statement.finalbody):
        return True
    normal_path_terminates = block_terminates(statement.body) or (
        bool(statement.orelse) and block_terminates(statement.orelse)
    )
    handler_paths_terminate = not statement.handlers or all(
        block_terminates(handler.body) for handler in statement.handlers
    )
    return normal_path_terminates and handler_paths_terminate


def is_catch_all_pattern(pattern: ast.pattern) -> bool:
    """
    判断 match case 是否为无条件捕获分支。

    Args:
        pattern: case 模式节点。

    Returns:
        `case _` 或无约束捕获名称时返回 True。
    """
    return isinstance(pattern, ast.MatchAs) and pattern.pattern is None
