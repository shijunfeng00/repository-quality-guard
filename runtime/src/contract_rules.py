from __future__ import annotations

import ast
from collections import defaultdict

from .ast_utils import dotted_name
from .facts import ModuleFacts
from .model import Finding
from .policy_common import (
    CONTRACT_NAME_HINTS,
    MUTATING_METHODS,
    all_parameters,
    annotation_names,
    call_name,
    direct_body_nodes,
    make_finding,
)

PAIR_ARGUMENT_COUNT = 2

MIN_PARALLEL_CONTRACT_ITEMS = 2
MIN_FIELD_ALIAS_CALLS = 2
MIN_DEFAULT_FALLBACK_VALUES = 2
MAPPING_PROTOCOL_METHODS = {
    "clear",
    "copy",
    "get",
    "items",
    "keys",
    "pop",
    "popitem",
    "setdefault",
    "update",
    "values",
}


def check_contract_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
) -> list[Finding]:
    """
    检查消费方猜测、修补或双协议访问内部数据的模式。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。

    Returns:
        契约消费相关发现列表。
    """
    nodes = direct_body_nodes(node)
    findings: list[Finding] = []
    findings.extend(check_type_probe_rules(facts, node, nodes, qualname))
    findings.extend(check_length_contract_rules(facts, node, nodes, qualname))
    findings.extend(check_field_contract_rules(facts, node, nodes, qualname))
    findings.extend(check_runtime_contract_rules(facts, node, nodes, qualname))
    return findings


def check_type_probe_rules(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """
    检查内部对象类型猜测和临时容器包装。

    Args:
        facts: 当前模块静态事实。
        function: 当前函数定义。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        类型猜测相关发现列表。
    """
    parameter_annotations = {
        argument.arg: argument.annotation
        for argument in all_parameters(function)
        if argument.annotation is not None
    }
    findings: list[Finding] = []
    for item in nodes:
        if isinstance(item, ast.Call) and call_name(item) == "isinstance" and item.args:
            checked = dotted_name(item.args[0])
            if contract_like_name(checked):
                tail = checked.rsplit(".", 1)[-1]
                annotation = parameter_annotations.get(tail)
                annotation_types = annotation_names(annotation)
                annotation_root = root_annotation_name(annotation)
                concrete_annotation = bool(
                    annotation_types
                ) and annotation_root not in {
                    "Any",
                    "object",
                    "Union",
                }
                findings.append(
                    make_finding(
                        facts,
                        item,
                        "QG060",
                        f"对契约候选对象 `{checked}` 使用 isinstance() 猜测返回形态。",
                        symbol=qualname,
                        severity="critical" if concrete_annotation else "error",
                        confidence="high" if concrete_annotation else "medium",
                        suggestion=(
                            "具体类型标注的内部参数应按正式契约使用；"
                            "Any、联合类型或局部 payload/result 仍不得在内部链路长期猜测；"
                            "应在唯一输入适配边界完成分派并形成固定内部类型。"
                        ),
                        evidence={
                            "annotated_parameter": annotation is not None,
                            "annotation_types": sorted(annotation_types),
                            "annotation_root": annotation_root,
                        },
                    )
                )
        if isinstance(item, ast.IfExp) and wraps_by_type_probe(item):
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG061",
                    "根据 isinstance() 把内部值临时包装为列表或字典。",
                    symbol=qualname,
                    severity="critical",
                    suggestion="生产方固定容器类型，消费方直接按契约使用；禁止用 isinstance 三元包装替代正式 schema。",
                )
            )
    return findings


def root_annotation_name(annotation: ast.expr | None) -> str:
    """
    提取参数注解的顶层类型名称。

    Args:
        annotation: 参数类型注解。

    Returns:
        顶层类型名称；PEP 604 联合类型返回 Union，无法识别时返回空字符串。
    """
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return "Union"
    if isinstance(annotation, ast.Subscript):
        return dotted_name(annotation.value).rsplit(".", 1)[-1]
    return dotted_name(annotation).rsplit(".", 1)[-1]


def check_length_contract_rules(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """
    检查静默截断和长度修复。

    Args:
        facts: 当前模块静态事实。
        function: 当前函数定义。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        长度契约相关发现列表。
    """
    findings: list[Finding] = []
    guards = equal_length_guards(function)
    for item in nodes:
        if isinstance(item, ast.Call) and call_name(item) == "zip":
            strict = next(
                (keyword.value for keyword in item.keywords if keyword.arg == "strict"),
                None,
            )
            argument_names = [dotted_name(argument) for argument in item.args]
            guarded = zip_arguments_guarded(argument_names, item.lineno, guards)
            if (
                len(item.args) >= MIN_PARALLEL_CONTRACT_ITEMS
                and not (isinstance(strict, ast.Constant) and strict.value is True)
                and not guarded
            ):
                findings.append(
                    make_finding(
                        facts,
                        item,
                        "QG062",
                        "zip() 未指定 strict=True，长度不一致时可能静默截断。",
                        symbol=qualname,
                        severity="info",
                        confidence="medium",
                        suggestion="等长属于契约时使用 strict=True；明确需要最短序列语义时保留并写明。",
                        evidence={"arguments": argument_names},
                    )
                )
        if is_length_repair(item):
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG063",
                    "使用 min(len(...)) 或交叉长度切片静默修复列表长度。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="让生产方保证长度关系；非法结构应显式失败。",
                )
            )
    return findings


def equal_length_guards(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[frozenset[str], int]]:
    """
    收集在后续语句前已显式保证两个序列等长的守卫。

    Args:
        function: 当前函数定义。

    Returns:
        无序变量对及守卫结束行组成的列表。
    """
    guards: list[tuple[frozenset[str], int]] = []
    for statement in function.body:
        pair: frozenset[str] | None = None
        if isinstance(statement, ast.Assert):
            pair = equal_length_pair(statement.test, require_equal=True)
        elif isinstance(statement, ast.If) and branch_stops(statement.body):
            pair = equal_length_pair(statement.test, require_equal=False)
        if pair is not None:
            guards.append((pair, statement.end_lineno or statement.lineno))
    return guards


def equal_length_pair(
    expression: ast.expr,
    *,
    require_equal: bool,
) -> frozenset[str] | None:
    """
    从 len(a) 与 len(b) 比较中提取变量对。

    Args:
        expression: 比较表达式。
        require_equal: True 时接受相等比较，否则接受不等比较。

    Returns:
        可识别时返回两个变量组成的无序集合。
    """
    if not isinstance(expression, ast.Compare) or len(expression.ops) != 1:
        return None
    accepted = (ast.Eq,) if require_equal else (ast.NotEq,)
    if not isinstance(expression.ops[0], accepted):
        return None
    operands = [expression.left, expression.comparators[0]]
    names: list[str] = []
    for operand in operands:
        if not (
            isinstance(operand, ast.Call)
            and call_name(operand) == "len"
            and operand.args
        ):
            return None
        name = dotted_name(operand.args[0])
        if not name:
            return None
        names.append(name)
    return frozenset(names) if len(set(names)) == PAIR_ARGUMENT_COUNT else None


def branch_stops(body: list[ast.stmt]) -> bool:
    """
    判断守卫分支是否以 return 或 raise 终止。

    Args:
        body: 分支语句列表。

    Returns:
        分支包含无条件终止语句时返回 True。
    """
    return any(isinstance(statement, (ast.Return, ast.Raise)) for statement in body)


def zip_arguments_guarded(
    arguments: list[str],
    line: int,
    guards: list[tuple[frozenset[str], int]],
) -> bool:
    """
    判断 zip 的全部简单参数对是否已由前置长度守卫覆盖。

    Args:
        arguments: zip 实参名称。
        line: zip 调用行号。
        guards: 已收集的长度守卫。

    Returns:
        两参数 zip 已由更早守卫保证等长时返回 True。
    """
    if len(arguments) != PAIR_ARGUMENT_COUNT or not all(arguments):
        return False
    pair = frozenset(arguments)
    return any(
        guard_pair == pair and guard_line < line for guard_pair, guard_line in guards
    )


def check_field_contract_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """
    检查字段别名和对象/字典双协议访问。

    Args:
        facts: 当前模块静态事实。
        node: 当前函数定义。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        字段契约相关发现列表。
    """
    accesses: dict[str, set[str]] = defaultdict(set)
    findings: list[Finding] = []
    for item in nodes:
        if is_alias_field_compatibility(item):
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG064",
                    "同一位置兼容多个字段名，形成永久字段别名层。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="统一生产方字段并迁移旧数据。",
                )
            )
        if isinstance(item, ast.Subscript):
            base = dotted_name(item.value)
            if base:
                accesses[base].add("mapping")
        elif isinstance(item, ast.Attribute):
            if item.attr in MAPPING_PROTOCOL_METHODS:
                continue
            base = dotted_name(item.value)
            if base:
                accesses[base].add("attribute")
    for base, modes in accesses.items():
        if modes == {"mapping", "attribute"} and contract_like_name(base):
            findings.append(
                make_finding(
                    facts,
                    node,
                    "QG065",
                    f"`{base}` 在同一函数中同时按对象属性和字典结构访问。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="固定一种内部协议，不要同时兼容对象与字典。",
                )
            )
    return findings


def check_runtime_contract_rules(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """
    检查默认值替代、assert 和无效不可变调用。

    Args:
        facts: 当前模块静态事实。
        function: 当前函数定义。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        运行时契约相关发现列表。
    """
    parameters = {argument.arg for argument in all_parameters(function)}
    findings: list[Finding] = []
    for item in nodes:
        if isinstance(item, ast.BoolOp) and is_contract_default_substitution(item):
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG066",
                    "内部契约值通过 `or` 回退为空容器、空字符串或固定默认值。",
                    symbol=qualname,
                    severity="info",
                    confidence="medium",
                    suggestion="可选值在类型和分支中表达；必填值缺失应直接失败。",
                )
            )
        if isinstance(item, ast.Assert):
            referenced = {
                child.id
                for child in ast.walk(item.test)
                if isinstance(child, ast.Name) and child.id in parameters
            }
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG069",
                    (
                        "使用 assert 校验函数输入或接口契约。"
                        if referenced
                        else "使用 assert 表达内部不变量。"
                    ),
                    symbol=qualname,
                    severity="warning" if referenced else "info",
                    confidence="high" if referenced else "medium",
                    suggestion="外部输入和运行时契约使用显式异常；assert 仅用于可被优化移除的内部不变量。",
                    evidence={"parameters": sorted(referenced)},
                )
            )
        if isinstance(item, ast.Expr) and isinstance(item.value, ast.Call):
            immutable_name = ignored_immutable_result(item.value, facts)
            if immutable_name:
                findings.append(
                    make_finding(
                        facts,
                        item,
                        "QG070",
                        f"调用不可变对象方法 `{immutable_name}` 后丢弃返回值。",
                        symbol=qualname,
                        severity="warning",
                        suggestion="接收返回值或删除无效调用。",
                    )
                )
    return findings


def contract_like_name(name: str) -> bool:
    """
    判断名称是否像内部契约对象。

    Args:
        name: 点分变量名称。

    Returns:
        最末名称命中常见契约词时返回 True。
    """
    if not name:
        return False
    tail = name.rsplit(".", 1)[-1].lower()
    return tail in CONTRACT_NAME_HINTS or tail.endswith(
        ("_state", "_result", "_config")
    )


def wraps_by_type_probe(node: ast.IfExp) -> bool:
    """
    判断条件表达式是否按类型探测临时包装容器。

    Args:
        node: 条件表达式。

    Returns:
        命中 `x if isinstance(x, list) else [x]` 等模式时返回 True。
    """
    if not isinstance(node.test, ast.Call) or call_name(node.test) != "isinstance":
        return False
    checked = dotted_name(node.test.args[0]) if node.test.args else ""
    if not contract_like_name(checked):
        return False
    return isinstance(node.body, (ast.Name, ast.Attribute)) and isinstance(
        node.orelse, (ast.List, ast.Dict, ast.Tuple)
    )


def is_length_repair(node: ast.AST) -> bool:
    """
    判断节点是否表现为静默截断长度的修复模式。

    Args:
        node: 待分析节点。

    Returns:
        命中 min(len(...)) 或使用其他对象长度切片时返回 True。
    """
    if isinstance(node, ast.Call) and call_name(node) == "min":
        return (
            sum(
                isinstance(argument, ast.Call) and call_name(argument) == "len"
                for argument in node.args
            )
            >= MIN_PARALLEL_CONTRACT_ITEMS
        )
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        upper = node.slice.upper
        return (
            isinstance(upper, ast.Call)
            and call_name(upper) == "len"
            and bool(upper.args)
            and dotted_name(upper.args[0]) != dotted_name(node.value)
        )
    return False


def is_alias_field_compatibility(node: ast.AST) -> bool:
    """
    判断表达式是否在同一接收者上回退读取多个字段名。

    Args:
        node: 待分析节点。

    Returns:
        命中 receiver.get("new") or receiver.get("old") 时返回 True。
    """
    if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
        return False
    calls = [item for item in node.values if isinstance(item, ast.Call)]
    if len(calls) < MIN_FIELD_ALIAS_CALLS:
        return False
    receivers: set[str] = set()
    keys: set[str] = set()
    for call in calls:
        if (
            not isinstance(call.func, ast.Attribute)
            or call.func.attr != "get"
            or not call.args
        ):
            return False
        receivers.add(dotted_name(call.func.value))
        key = call.args[0]
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return False
        keys.add(key.value)
    return len(receivers) == 1 and len(keys) > 1


def is_contract_default_substitution(node: ast.BoolOp) -> bool:
    """
    判断 `or` 表达式是否对契约值使用固定默认结果。

    Args:
        node: 布尔表达式。

    Returns:
        左侧像契约对象且后续包含固定默认值时返回 True。
    """
    if (
        not isinstance(node.op, ast.Or)
        or len(node.values) < MIN_DEFAULT_FALLBACK_VALUES
    ):
        return False
    first = dotted_name(node.values[0])
    if not contract_like_name(first):
        return False
    return any(is_default_literal(item) for item in node.values[1:])


def is_default_literal(node: ast.expr) -> bool:
    """
    判断表达式是否为常见兜底字面量。

    Args:
        node: 待判断表达式。

    Returns:
        空容器、空字符串、零或 False 时返回 True。
    """
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    if not isinstance(node, ast.Constant):
        return False
    value = node.value
    return (
        value is None
        or value is False
        or value == ""
        or (type(value) is int and value == 0)
    )


def ignored_immutable_result(node: ast.Call, facts: ModuleFacts) -> str:
    """
    判断调用是否丢弃常见不可变对象实例方法返回值。

    Args:
        node: 调用表达式。
        facts: 当前模块事实，用于排除 ``os.replace`` 等导入模块函数。

    Returns:
        命中的方法名称，否则返回空字符串。
    """
    if not isinstance(node.func, ast.Attribute):
        return ""
    receiver = dotted_name(node.func.value)
    receiver_root = receiver.partition(".")[0]
    if receiver_root in facts.imports:
        return ""
    immutable_methods = {
        "capitalize",
        "casefold",
        "expandtabs",
        "format",
        "joinpath",
        "lower",
        "lstrip",
        "replace",
        "resolve",
        "rstrip",
        "strip",
        "upper",
        "with_name",
        "with_suffix",
    }
    return node.func.attr if node.func.attr in immutable_methods else ""


def check_side_effect_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    *,
    allow_print: bool = False,
) -> list[Finding]:
    """
    检查输入参数修改、外部状态和异常吞噬。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。
        allow_print: 是否允许命令行入口使用 print()。

    Returns:
        副作用和异常处理发现列表。
    """
    nodes = direct_body_nodes(node)
    findings: list[Finding] = []
    findings.extend(check_parameter_mutation(facts, node, nodes, qualname))
    findings.extend(check_exception_flow(facts, nodes, qualname))
    findings.extend(check_dynamic_attribute_access(facts, nodes, qualname))
    if not allow_print:
        findings.extend(check_print_calls(facts, nodes, qualname))
    return findings


def check_parameter_mutation(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """
    检查输入参数和跨作用域状态修改。

    Args:
        facts: 当前模块静态事实。
        node: 当前函数定义。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        参数修改相关发现列表。
    """
    parameter_names = {argument.arg for argument in all_parameters(node)} - {
        "self",
        "cls",
    }
    parameter_names -= {"accumulator", "collector", "findings", "output", "signals"}
    mutating_prefixes = (
        "append_",
        "apply_",
        "clear_",
        "collect_",
        "consume_",
        "filter_",
        "merge_",
        "mutate_",
        "pop_",
        "populate_",
        "record_",
        "remove_",
        "set_",
        "update_",
        "write_",
    )
    if node.name.lstrip("_").startswith(mutating_prefixes):
        parameter_names.clear()
    mutated: set[str] = set()
    findings: list[Finding] = []
    for item in nodes:
        if isinstance(item, (ast.Global, ast.Nonlocal)):
            is_global = isinstance(item, ast.Global)
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG051",
                    (
                        f"`{qualname}` 声明 global 并修改模块共享状态。"
                        if is_global
                        else f"`{qualname}` 声明 nonlocal 并修改闭包状态。"
                    ),
                    symbol=qualname,
                    severity="warning" if is_global else "info",
                    confidence="high" if is_global else "medium",
                    suggestion=(
                        "把模块共享状态所有权放入明确对象或返回值。"
                        if is_global
                        else "确认闭包状态仅服务于局部算法，且不会作为隐藏回调副作用逃逸。"
                    ),
                )
            )
        mutated.update(mutated_parameter_names(item, parameter_names))
    for name in sorted(mutated):
        findings.append(
            make_finding(
                facts,
                node,
                "QG050",
                f"`{qualname}` 原地修改输入参数 `{name}`。",
                symbol=qualname,
                severity="warning",
                suggestion="返回新值，或通过命名、类型和 docstring 明确原地修改契约。",
                evidence={"parameter": name},
            )
        )
    return findings


def check_exception_flow(
    facts: ModuleFacts, nodes: list[ast.AST], qualname: str
) -> list[Finding]:
    """
    检查异常处理后继续执行和异常链丢失。

    Args:
        facts: 当前模块静态事实。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        异常流程发现列表。
    """
    findings: list[Finding] = []
    for item in nodes:
        if not isinstance(item, ast.ExceptHandler):
            continue
        if handler_logs_without_raising(item):
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG048",
                    "异常被记录后继续运行，失败状态可能被伪装成成功路径。",
                    symbol=qualname,
                    severity="error",
                    suggestion="重新抛出异常或返回明确失败结构。",
                )
            )
        if translates_exception_without_cause(item):
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG049",
                    "捕获异常后重新包装，但没有使用 `raise ... from error`。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="保留原始异常作为 cause，或不做无价值异常翻译。",
                )
            )
    return findings


def check_dynamic_attribute_access(
    facts: ModuleFacts, nodes: list[ast.AST], qualname: str
) -> list[Finding]:
    """
    检查对契约对象的动态属性修改和 __dict__ 访问。

    Args:
        facts: 当前模块静态事实。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        动态属性发现列表。
    """
    findings: list[Finding] = []
    for item in nodes:
        if isinstance(item, ast.Call) and call_name(item) in {"setattr", "delattr"}:
            target = dotted_name(item.args[0]) if item.args else ""
            if contract_like_name(target):
                findings.append(
                    make_finding(
                        facts,
                        item,
                        "QG057",
                        f"对契约对象 `{target}` 使用动态属性修改。",
                        symbol=qualname,
                        severity="warning",
                        suggestion="通过声明字段和显式赋值维护契约。",
                    )
                )
        if isinstance(item, ast.Attribute) and item.attr == "__dict__":
            target = dotted_name(item.value)
            if contract_like_name(target):
                findings.append(
                    make_finding(
                        facts,
                        item,
                        "QG057",
                        f"直接访问契约对象 `{target}.__dict__`。",
                        symbol=qualname,
                        severity="warning",
                        suggestion="使用正式序列化接口或声明字段访问。",
                    )
                )
    return findings


def check_print_calls(
    facts: ModuleFacts, nodes: list[ast.AST], qualname: str
) -> list[Finding]:
    """
    检查生产函数中的 print() 调用。

    Args:
        facts: 当前模块静态事实。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        print() 调用发现列表。
    """
    return [
        make_finding(
            facts,
            item,
            "QG052",
            "生产代码中使用 print()，输出无法进入统一日志和 Trace。",
            symbol=qualname,
            severity="warning",
            suggestion="使用项目日志系统并选择正确级别。",
        )
        for item in nodes
        if isinstance(item, ast.Call) and call_name(item) == "print"
    ]


def mutated_parameter_names(node: ast.AST, parameters: set[str]) -> set[str]:
    """
    提取单个节点中被原地修改的参数名称。

    Args:
        node: 待检查节点。
        parameters: 当前函数参数名称集合。

    Returns:
        命中原地修改的参数名称集合。
    """
    result: set[str] = set()
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, (ast.Attribute, ast.Subscript)):
                continue
            base = mutation_base_name(target)
            if base in parameters:
                result.add(base)
    if isinstance(node, ast.AugAssign):
        base = mutation_base_name(node.target)
        if base in parameters:
            result.add(base)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        base = dotted_name(node.func.value).split(".", 1)[0]
        if base in parameters and node.func.attr in MUTATING_METHODS:
            result.add(base)
    return result


def mutation_base_name(node: ast.expr) -> str:
    """
    提取赋值目标所属的根变量名。

    Args:
        node: 赋值目标表达式。

    Returns:
        根名称；无法确定时返回空字符串。
    """
    current: ast.expr = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


def handler_logs_without_raising(handler: ast.ExceptHandler) -> bool:
    """
    判断异常处理块是否只记录后继续执行。

    Args:
        handler: 异常处理节点。

    Returns:
        处理块包含日志且没有 raise/return 时返回 True。
    """
    has_log = any(
        isinstance(item, ast.Call)
        and (
            call_name(item).startswith(("logger.", "logging."))
            or call_name(item) == "print"
        )
        for statement in handler.body
        for item in ast.walk(statement)
    )
    terminates = any(
        isinstance(item, (ast.Raise, ast.Return))
        for statement in handler.body
        for item in ast.walk(statement)
    )
    return has_log and not terminates


def translates_exception_without_cause(handler: ast.ExceptHandler) -> bool:
    """
    判断异常处理块是否丢失异常链地重新抛出新异常。

    Args:
        handler: 异常处理节点。

    Returns:
        存在 `raise NewError(...)` 且没有 cause 时返回 True。
    """
    for statement in handler.body:
        for item in ast.walk(statement):
            if (
                isinstance(item, ast.Raise)
                and item.exc is not None
                and item.cause is None
            ):
                return True
    return False
