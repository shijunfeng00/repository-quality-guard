from __future__ import annotations

import ast
from collections import defaultdict

from .ast_utils import decorator_names, dotted_name, enclosing_class_name
from .config import GuardConfig
from .facts import ModuleFacts
from .model import Finding
from .policy_common import (
    MUTATING_METHODS,
    READ_ONLY_NAME_PREFIXES,
    WRITE_CALL_MARKERS,
    ParsedModule,
    RepositorySignals,
    StateContract,
    StateField,
    all_parameters,
    annotation_names,
    annotation_text,
    call_name,
    direct_body_nodes,
    expression_type,
    make_finding,
)
from .return_rules import return_shape

STATE_MAPPING_METHODS = {
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


def collect_state_contracts(
    modules: list[ParsedModule],
    config: GuardConfig,
) -> dict[str, StateContract]:
    """
    从类字段、TypedDict 和 dataclass 声明中收集状态契约。

    Args:
        modules: 模块事实与 AST 组成的列表。
        config: 当前仓库质量配置。

    Returns:
        状态类型简单名称到字段契约的映射。
    """
    contracts: dict[str, StateContract] = {}
    configured = set(config.state_types)
    for module in modules:
        for statement in module.tree.body:
            contract = build_state_contract(module.facts, statement, configured)
            if contract is not None:
                contracts[contract.name] = contract
    collect_dynamic_state_fields(modules, contracts, configured)
    return contracts


def build_state_contract(
    facts: ModuleFacts,
    statement: ast.stmt,
    configured: set[str],
) -> StateContract | None:
    """
    从单个类声明构造状态契约。

    Args:
        facts: 当前模块静态事实。
        statement: 模块顶层语句。
        configured: 显式配置的状态类型名称。

    Returns:
        命中状态类型时返回契约，否则返回 None。
    """
    if not isinstance(statement, ast.ClassDef):
        return None
    bases = {dotted_name(base).rsplit(".", 1)[-1] for base in statement.bases}
    if statement.name not in configured:
        return None
    typed_dict = "TypedDict" in bases
    total: bool | None = None
    if typed_dict:
        total = True
        for keyword in statement.keywords:
            if (
                keyword.arg == "total"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, bool)
            ):
                total = keyword.value.value
    contract = StateContract(
        name=statement.name,
        module=facts.module,
        mapping_style=typed_dict or "dict" in bases,
        total=total,
    )
    for child in statement.body:
        field = annotated_state_field(child)
        if field is not None:
            contract.fields[field.name] = field
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            contract.members.add(child.name)
            if child.name == "__init__":
                contract.fields.update(fields_assigned_in_init(child))
    return contract


def collect_dynamic_state_fields(
    modules: list[ParsedModule],
    contracts: dict[str, StateContract],
    configured: set[str],
) -> None:
    """
    收集显式配置状态类型的运行时字段注册。

    Args:
        modules: 模块事实与 AST 组成的列表。
        contracts: 已建立的状态契约。
        configured: 显式配置的状态类型名称。

    Returns:
        None。
    """
    preferred = next((contracts[name] for name in configured if name in contracts), None)
    for module in modules:
        parents = module.parents
        for node in module.nodes:
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update"
            ):
                continue
            receiver = dotted_name(node.func.value)
            contract = None
            if receiver == "self":
                owner = enclosing_class_name(node, parents)
                contract = contracts.get(owner)
            elif receiver.rsplit(".", 1)[-1] == "state":
                contract = preferred
            if contract is None:
                continue
            field = state_update_field(node)
            if field is not None:
                contract.fields[field.name] = field


def state_update_field(node: ast.Call) -> StateField | None:
    """
    从 update(key=..., value=...) 调用提取显式状态字段。

    Args:
        node: update 调用。

    Returns:
        字段名称为字符串常量时返回字段契约，否则返回 None。
    """
    key_node = node.args[0] if node.args else None
    value_node = node.args[1] if len(node.args) > 1 else None
    type_desc = ""
    for keyword in node.keywords:
        if keyword.arg == "key":
            key_node = keyword.value
        elif keyword.arg == "value":
            value_node = keyword.value
        elif (
            keyword.arg == "type_desc"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ):
            type_desc = keyword.value.value
    if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
        return None
    value_type = type_desc or expression_type(value_node)
    return StateField(
        name=key_node.value,
        annotation=value_type,
        is_sequence=value_type.startswith(("list", "List", "Sequence")),
    )


def collect_registered_tools(
    modules: list[ParsedModule],
    config: GuardConfig,
    signals: RepositorySignals,
) -> None:
    """
    收集仓库显式声明的工具装饰器和注册方法入口。

    Args:
        modules: 模块事实与 AST 组成的列表。
        config: 当前仓库质量配置。
        signals: 跨模块信号汇总对象。

    Returns:
        None。
    """
    decorators = set(config.tool_decorators)
    registration_methods = set(config.tool_registration_methods)
    if not decorators and not registration_methods:
        return
    for module in modules:
        for node in module.nodes:
            name = registered_tool_name(
                node,
                module.facts.module,
                module.parents,
                decorators,
                registration_methods,
            )
            if name:
                signals.registered_tools.add(name)


def registered_tool_name(
    node: ast.AST,
    module: str,
    parents: dict[ast.AST, ast.AST],
    decorators: set[str],
    registration_methods: set[str],
) -> str:
    """
    解析单个 AST 节点对应的显式工具注册名称。

    Args:
        node: 待检查 AST 节点。
        module: 当前模块名称。
        parents: AST 父节点映射。
        decorators: 仓库声明的工具装饰器名称。
        registration_methods: 仓库声明的工具注册方法名称。

    Returns:
        工具限定名称；当前节点不是工具入口时返回空字符串。
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        names = {name.rsplit(".", 1)[-1] for name in decorator_names(node.decorator_list)}
        if names & decorators:
            owner = enclosing_class_name(node, parents)
            prefix = ".".join(part for part in (module, owner) if part)
            return f"{prefix}.{node.name}".strip(".")
    if not is_configured_tool_registration(node, registration_methods):
        return ""
    owner = enclosing_class_name(node, parents)
    if not owner:
        return ""
    argument = node.args[0]
    return f"{module}.{owner}.{argument.attr}"


def is_configured_tool_registration(
    node: ast.AST,
    registration_methods: set[str],
) -> bool:
    """
    判断调用是否符合显式配置的 self.method 工具注册形式。

    Args:
        node: 待检查 AST 节点。
        registration_methods: 仓库声明的工具注册方法名称。

    Returns:
        形如 registry.add(self.method) 且方法名已配置时返回 True。
    """
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in registration_methods
        and node.args
        and isinstance(node.args[0], ast.Attribute)
        and isinstance(node.args[0].value, ast.Name)
        and node.args[0].value.id == "self"
    )


def annotated_state_field(node: ast.stmt) -> StateField | None:
    """
    解析状态类中的注解字段。

    Args:
        node: 类体语句。

    Returns:
        注解字段信息；不是简单注解字段时返回 None。
    """
    if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
        return None
    names = annotation_names(node.annotation)
    return StateField(
        name=node.target.id,
        annotation=annotation_text(node.annotation),
        is_sequence=bool(names & {"list", "List", "Sequence", "MutableSequence"}),
    )


def fields_assigned_in_init(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, StateField]:
    """
    从构造函数中的 self 字段赋值补充状态字段。

    Args:
        node: 构造函数定义。

    Returns:
        字段名称到粗粒度字段信息的映射。
    """
    fields: dict[str, StateField] = {}
    for item in direct_body_nodes(node):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(item, ast.Assign) and len(item.targets) == 1:
            target, value = item.targets[0], item.value
        elif isinstance(item, ast.AnnAssign):
            target, value = item.target, item.value
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            value_type = expression_type(value)
            fields[target.attr] = StateField(
                name=target.attr,
                annotation=value_type,
                is_sequence=value_type in {"list", "tuple"},
            )
    return fields


def check_agent_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    class_name: str,
    contracts: dict[str, StateContract],
    config: GuardConfig,
    signals: RepositorySignals,
) -> list[Finding]:
    """
    检查 Tool、State、History 和 Trace 的边界。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。
        class_name: 所属类名称。
        contracts: 仓库状态契约。
        config: 当前仓库质量配置。
        signals: 跨模块信号汇总对象。

    Returns:
        Agent 专项规则发现列表。
    """
    is_tool = is_tool_definition(facts, node, qualname, class_name, config, signals)
    parameter_contracts = state_parameter_contracts(node, contracts)
    nodes = direct_body_nodes(node)
    findings, accesses, methods = collect_state_operation_findings(
        facts,
        nodes,
        qualname,
        parameter_contracts,
        is_tool,
        signals,
    )
    state_parameters = set(parameter_contracts)
    findings.extend(check_state_scope(facts, node, qualname, accesses))
    findings.extend(check_state_update_entry(facts, node, qualname, state_parameters, config))
    findings.extend(check_state_merge_methods(facts, node, qualname, methods))
    if is_tool:
        findings.extend(check_tool_contract(facts, node, qualname, nodes))
    findings.extend(check_history_trace_rules(facts, node, qualname, nodes, config))
    return findings


def is_tool_definition(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    class_name: str,
    config: GuardConfig,
    signals: RepositorySignals,
) -> bool:
    """
    判断函数是否属于配置声明的 Tool 边界。

    Args:
        facts: 当前模块事实。
        node: 函数定义。
        qualname: 模块内限定名称。
        class_name: 所属类名称。
        config: 当前仓库质量配置。
        signals: 跨模块信号汇总对象。

    Returns:
        命中 Tool 装饰器或 Tool 基类名称时返回 True。
    """
    decorators = {name.rsplit(".", 1)[-1] for name in decorator_names(node.decorator_list)}
    symbol = f"{facts.module}.{qualname}" if facts.module else qualname
    if symbol in signals.registered_tools or decorators & set(config.tool_decorators):
        return True
    return class_name in set(config.tool_base_classes) and not node.name.startswith("_")


def collect_state_operation_findings(
    facts: ModuleFacts,
    nodes: list[ast.AST],
    qualname: str,
    contracts: dict[str, StateContract | None],
    is_tool: bool,
    signals: RepositorySignals,
) -> tuple[list[Finding], dict[str, set[str]], dict[tuple[str, str], set[str]]]:
    """
    收集状态读写并生成字段契约发现。

    Args:
        facts: 当前模块静态事实。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。
        contracts: 状态参数到契约的映射。
        is_tool: 当前函数是否为 Tool。
        signals: 跨模块信号汇总对象。

    Returns:
        发现、字段读取集合和字段修改方法集合。
    """
    findings: list[Finding] = []
    accesses: dict[str, set[str]] = defaultdict(set)
    methods: dict[tuple[str, str], set[str]] = defaultdict(set)
    state_parameters = set(contracts)
    for item in nodes:
        read = state_read(item, contracts)
        if read is not None:
            parameter, field_name = read
            accesses[parameter].add(field_name)
            findings.extend(check_declared_state_read(facts, item, qualname, contracts, read))
        write = state_write(item, state_parameters)
        if write is not None:
            methods[(write[0], write[1])].add(write[3])
            findings.extend(
                record_state_write(facts, item, qualname, contracts, write, is_tool, signals)
            )
    return findings, accesses, methods


def check_declared_state_read(
    facts: ModuleFacts,
    node: ast.AST,
    qualname: str,
    contracts: dict[str, StateContract | None],
    read: tuple[str, str],
) -> list[Finding]:
    """
    检查状态读取字段是否已在契约中声明。

    Args:
        facts: 当前模块静态事实。
        node: 状态读取节点。
        qualname: 函数限定名称。
        contracts: 状态参数到契约的映射。
        read: 状态参数与字段名称。

    Returns:
        未声明字段发现；字段合法时返回空列表。
    """
    parameter, field_name = read
    contract = contracts[parameter]
    if contract is None or field_name in contract.fields:
        return []
    return [
        make_finding(
            facts,
            node,
            "QG125",
            f"读取状态类型 `{contract.name}` 未声明字段 `{field_name}`。",
            symbol=qualname,
            severity="error",
            suggestion="先在唯一状态模型中声明字段，再按正式契约直接访问。",
        )
    ]


def record_state_write(
    facts: ModuleFacts,
    node: ast.AST,
    qualname: str,
    contracts: dict[str, StateContract | None],
    write: tuple[str, str, str, str],
    is_tool: bool,
    signals: RepositorySignals,
) -> list[Finding]:
    """
    记录状态写入并检查 Tool、副字段和序列归并语义。

    Args:
        facts: 当前模块静态事实。
        node: 状态写入节点。
        qualname: 函数限定名称。
        contracts: 状态参数到契约的映射。
        write: 状态参数、字段、值类型和写入方法。
        is_tool: 当前函数是否为 Tool。
        signals: 跨模块信号汇总对象。

    Returns:
        当前状态写入产生的发现列表。
    """
    parameter, field_name, value_type, method_name = write
    contract = contracts[parameter]
    contract_name = contract.name if contract is not None else "<unknown>"
    signals.state_writes[(contract_name, field_name)].append((value_type, facts.path, node.lineno))
    findings = check_declared_state_write(facts, node, qualname, contract, field_name)
    if is_tool:
        findings.append(
            make_finding(
                facts,
                node,
                "QG120",
                f"Tool `{qualname}` 直接修改状态 `{parameter}.{field_name}`。",
                symbol=qualname,
                severity="error",
                suggestion="Tool 返回结构化结果，由 Agent 或唯一 update_state 入口归并。",
            )
        )
    findings.extend(
        check_sequence_state_write(
            facts,
            node,
            qualname,
            contract,
            field_name,
            value_type,
            method_name,
        )
    )
    return findings


def check_declared_state_write(
    facts: ModuleFacts,
    node: ast.AST,
    qualname: str,
    contract: StateContract | None,
    field_name: str,
) -> list[Finding]:
    """
    检查状态写入字段是否已声明。

    Args:
        facts: 当前模块静态事实。
        node: 状态写入节点。
        qualname: 函数限定名称。
        contract: 状态契约。
        field_name: 写入字段名称。

    Returns:
        未声明字段发现列表。
    """
    if contract is None or field_name in contract.fields or field_name.startswith("<"):
        return []
    return [
        make_finding(
            facts,
            node,
            "QG124",
            f"写入状态类型 `{contract.name}` 未声明字段 `{field_name}`。",
            symbol=qualname,
            severity="error",
            suggestion="先在唯一状态模型中声明字段及归并语义。",
        )
    ]


def check_sequence_state_write(
    facts: ModuleFacts,
    node: ast.AST,
    qualname: str,
    contract: StateContract | None,
    field_name: str,
    value_type: str,
    method_name: str,
) -> list[Finding]:
    """
    检查序列状态是否误用 append() 追加整个容器。

    Args:
        facts: 当前模块静态事实。
        node: 状态写入节点。
        qualname: 函数限定名称。
        contract: 状态契约。
        field_name: 写入字段名称。
        value_type: 写入值的粗粒度类型。
        method_name: 写入方法名称。

    Returns:
        嵌套序列风险发现列表。
    """
    if contract is None or field_name not in contract.fields:
        return []
    field = contract.fields[field_name]
    if (
        not field.is_sequence
        or method_name != "append"
        or value_type not in {"list", "tuple", "set"}
    ):
        return []
    return [
        make_finding(
            facts,
            node,
            "QG127",
            f"序列状态字段 `{field_name}` 使用 append() 追加整个 {value_type}。",
            symbol=qualname,
            severity="warning",
            suggestion="确认是否误把 extend() 写成 append()，避免产生嵌套列表。",
        )
    ]


def state_parameter_contracts(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    contracts: dict[str, StateContract],
) -> dict[str, StateContract | None]:
    """
    识别函数签名中的状态参数及其契约类型。

    Args:
        node: 函数定义。
        contracts: 仓库状态契约。

    Returns:
        状态参数名称到契约对象的映射。
    """
    result: dict[str, StateContract | None] = {}
    for argument in all_parameters(node):
        names = annotation_names(argument.annotation)
        matches = names & set(contracts)
        if matches:
            result[argument.arg] = contracts[next(iter(matches))]
        elif argument.arg == "state" and contracts:
            result[argument.arg] = None
    return result


def state_read(
    node: ast.AST,
    contracts: dict[str, StateContract | None],
) -> tuple[str, str] | None:
    """
    解析状态字段读取表达式。

    Args:
        node: 待检查节点。
        contracts: 状态参数到契约的映射。

    Returns:
        参数名和字段名；不是状态读取时返回 None。
    """
    state_parameters = set(contracts)
    call_read = state_call_read(node, state_parameters)
    if call_read is not None:
        return call_read
    attribute_read = state_attribute_read(node, contracts)
    if attribute_read is not None:
        return attribute_read
    return state_subscript_read(node, state_parameters)


def state_call_read(
    node: ast.AST,
    state_parameters: set[str],
) -> tuple[str, str] | None:
    """
    解析 get、pop 和 setdefault 形式的状态读取。

    Args:
        node: 待检查节点。
        state_parameters: 状态参数名称集合。

    Returns:
        参数名与字段名；不是目标调用时返回 None。
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None
    base = dotted_name(node.func.value)
    if not (
        base in state_parameters
        and node.func.attr in {"get", "pop", "setdefault"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return None
    return base, node.args[0].value


def state_attribute_read(
    node: ast.AST,
    contracts: dict[str, StateContract | None],
) -> tuple[str, str] | None:
    """
    解析属性形式的状态字段读取。

    Args:
        node: 待检查节点。
        contracts: 状态参数到契约的映射。

    Returns:
        参数名与字段名；不是状态属性读取时返回 None。
    """
    if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load):
        return None
    base = dotted_name(node.value)
    if base not in contracts:
        return None
    contract = contracts[base]
    if node.attr in STATE_MAPPING_METHODS or (
        contract is not None and node.attr in contract.members
    ):
        return None
    return base, node.attr


def state_subscript_read(
    node: ast.AST,
    state_parameters: set[str],
) -> tuple[str, str] | None:
    """
    解析下标形式的状态字段读取。

    Args:
        node: 待检查节点。
        state_parameters: 状态参数名称集合。

    Returns:
        参数名与字段名；不是字符串下标读取时返回 None。
    """
    if not isinstance(node, ast.Subscript) or not isinstance(node.ctx, ast.Load):
        return None
    base = dotted_name(node.value)
    key = node.slice
    if not (
        base in state_parameters and isinstance(key, ast.Constant) and isinstance(key.value, str)
    ):
        return None
    return base, key.value


def state_write(
    node: ast.AST,
    state_parameters: set[str],
) -> tuple[str, str, str, str] | None:
    """
    解析对状态字段的直接写入或容器修改。

    Args:
        node: 待检查节点。
        state_parameters: 状态参数名称集合。

    Returns:
        参数名、字段名、值类型和修改方法；不是状态写入时返回 None。
    """
    assignment = assignment_state_write(node, state_parameters)
    if assignment is not None:
        return assignment
    return call_state_write(node, state_parameters)


def assignment_state_write(
    node: ast.AST,
    state_parameters: set[str],
) -> tuple[str, str, str, str] | None:
    """
    解析赋值语句形式的状态写入。

    Args:
        node: 待检查节点。
        state_parameters: 状态参数名称集合。

    Returns:
        状态写入信息；不是直接赋值时返回 None。
    """
    target: ast.expr | None = None
    value: ast.expr | None = None
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target, value = node.targets[0], node.value
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        target, value = node.target, node.value
    if target is None:
        return None
    return state_target_write(target, value, state_parameters, "assign")


def call_state_write(
    node: ast.AST,
    state_parameters: set[str],
) -> tuple[str, str, str, str] | None:
    """
    解析 append、extend、update 等方法形式的状态写入。

    Args:
        node: 待检查节点。
        state_parameters: 状态参数名称集合。

    Returns:
        状态写入信息；不是容器修改调用时返回 None。
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in MUTATING_METHODS:
        return None
    value = node.args[0] if node.args else None
    return state_target_write(node.func.value, value, state_parameters, node.func.attr)


def state_target_write(
    target: ast.expr,
    value: ast.expr | None,
    state_parameters: set[str],
    method_name: str,
) -> tuple[str, str, str, str] | None:
    """
    把状态属性或下标目标归一化为写入信息。

    Args:
        target: 写入目标表达式。
        value: 写入值表达式。
        state_parameters: 状态参数名称集合。
        method_name: 赋值或容器修改方法名称。

    Returns:
        状态写入信息；目标不属于状态时返回 None。
    """
    if isinstance(target, ast.Attribute):
        base = dotted_name(target.value)
        if base in state_parameters:
            return base, target.attr, expression_type(value), method_name
    if isinstance(target, ast.Subscript):
        base = dotted_name(target.value)
        key = target.slice
        if (
            base in state_parameters
            and isinstance(key, ast.Constant)
            and isinstance(key.value, str)
        ):
            return base, key.value, expression_type(value), method_name
    return None


def _state_parameter_escapes(node: ast.FunctionDef | ast.AsyncFunctionDef, parameter: str) -> bool:
    """判断完整状态参数是否被直接透传、返回或保存，降低 QG128 误判。

    Args:
        node: 当前函数定义。
        parameter: 已识别为状态对象的参数名称。

    Returns:
        状态对象本身跨出当前函数职责边界时返回 True。
    """
    for item in ast.walk(node):
        if isinstance(item, ast.Call):
            values = [*item.args, *(keyword.value for keyword in item.keywords)]
            if any(isinstance(value, ast.Name) and value.id == parameter for value in values):
                return True
        if isinstance(item, (ast.Return, ast.Yield, ast.YieldFrom)):
            value = item.value
            if isinstance(value, ast.Name) and value.id == parameter:
                return True
        if isinstance(item, (ast.Assign, ast.AnnAssign)):
            value = item.value
            if isinstance(value, ast.Name) and value.id == parameter:
                return True
    return False


def _used_as_callback(facts: ModuleFacts, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """判断定义是否被当前模块作为函数对象传给其他调用，识别图节点/路由等回调协议。

    Args:
        facts: 当前模块静态事实。
        node: 待判断函数定义。

    Returns:
        函数对象作为 positional/keyword 参数出现时返回 True。
    """
    if facts.tree is None:
        return False
    for call in (item for item in ast.walk(facts.tree) if isinstance(item, ast.Call)):
        values = [*call.args, *(keyword.value for keyword in call.keywords)]
        for value in values:
            if isinstance(value, ast.Name) and value.id == node.name:
                return True
            if isinstance(value, ast.Attribute) and value.attr == node.name:
                return True
    return False


def _protocol_like_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """判断方法签名是否明显受 Python/框架 protocol 约束。"""
    if node.name.startswith("__") and node.name.endswith("__"):
        return True
    markers = {"override", "abstractmethod", "property", "callback", "route", "validator"}
    return any(
        marker in dotted_name(decorator).lower()
        for marker in markers
        for decorator in node.decorator_list
    )


def check_state_scope(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    accesses: dict[str, set[str]],
) -> list[Finding]:
    """
    检查函数是否接收整个状态却只读取极少字段。

    Args:
        facts: 当前模块静态事实。
        node: 函数定义。
        qualname: 函数限定名称。
        accesses: 各状态参数读取的字段集合。

    Returns:
        依赖范围过宽发现列表。
    """
    findings: list[Finding] = []
    for parameter, fields in accesses.items():
        if len(fields) != 1:
            continue
        high_confidence = (
            not _protocol_like_method(node)
            and not _used_as_callback(facts, node)
            and not _state_parameter_escapes(node, parameter)
        )
        findings.append(
            make_finding(
                facts,
                node,
                "QG128",
                f"`{qualname}` 接收完整状态 `{parameter}`，但只读取字段 `{next(iter(fields))}`。",
                symbol=qualname,
                severity="warning" if high_confidence else "info",
                confidence="high" if high_confidence else "medium",
                suggestion=(
                    "高置信依赖面过宽：优先改为传入所需子对象或字段；若签名由协议固定，必须在语义审计中证明。"
                    if high_confidence
                    else "检查协议/回调签名与透传语义；若不受约束，缩小状态依赖面。"
                ),
                evidence={
                    "state_parameter": parameter,
                    "fields": sorted(fields),
                    "qg179_exempt": high_confidence,
                    "semantic_review_required": True,
                    "semantic_review_question": "Q4,Q5",
                    "semantic_review_kind": "dependency-surface",
                    "high_confidence": high_confidence,
                },
            )
        )
    return findings


def check_state_update_entry(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    state_parameters: set[str],
    config: GuardConfig,
) -> list[Finding]:
    """
    检查状态写入是否绕过配置的唯一更新入口。

    Args:
        facts: 当前模块静态事实。
        node: 函数定义。
        qualname: 函数限定名称。
        state_parameters: 状态参数名称集合。
        config: 当前仓库质量配置。

    Returns:
        状态更新入口发现列表。
    """
    if not state_parameters or any(marker in node.name for marker in config.state_writer_names):
        return []
    writes = [
        item for item in direct_body_nodes(node) if state_write(item, state_parameters) is not None
    ]
    if not writes:
        return []
    return [
        make_finding(
            facts,
            writes[0],
            "QG134",
            f"`{qualname}` 在非状态更新入口中直接写入配置的状态对象。",
            symbol=qualname,
            severity="warning",
            suggestion=(
                "把状态归并集中到配置的 canonical state-writer 入口："
                + ", ".join(config.state_writer_names)
            ),
        )
    ]


def check_state_merge_methods(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    methods: dict[tuple[str, str], set[str]],
) -> list[Finding]:
    """
    检查同一状态字段是否混用 append 和 extend 等归并语义。

    Args:
        facts: 当前模块静态事实。
        node: 函数定义。
        qualname: 函数限定名称。
        methods: 状态字段到修改方法集合的映射。

    Returns:
        归并语义不一致发现列表。
    """
    findings: list[Finding] = []
    for (_, field_name), method_names in methods.items():
        if {"append", "extend"} <= method_names:
            findings.append(
                make_finding(
                    facts,
                    node,
                    "QG135",
                    f"状态字段 `{field_name}` 在同一函数中混用 append() 和 extend()。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="为字段固定单项追加或批量归并语义。",
                )
            )
    return findings


def check_tool_contract(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    nodes: list[ast.AST],
) -> list[Finding]:
    """
    检查 Tool 返回结构、写副作用和文档化 JSON 字段。

    Args:
        facts: 当前模块静态事实。
        node: Tool 函数定义。
        qualname: Tool 限定名称。
        nodes: Tool 直接拥有的 AST 节点。

    Returns:
        Tool 契约发现列表。
    """
    findings: list[Finding] = []
    returns = [item for item in nodes if isinstance(item, ast.Return)]
    shapes = {return_shape(item.value) for item in returns}
    if len(shapes) > 1:
        findings.append(
            make_finding(
                facts,
                node,
                "QG121",
                f"Tool `{qualname}` 存在多种返回结构：{sorted(shapes)}。",
                symbol=qualname,
                severity="error",
                suggestion="固定 Tool JSON schema，并在工具描述中说明消费方式。",
            )
        )
    if any(shape in {"str", "call:str", "call:repr"} for shape in shapes):
        findings.append(
            make_finding(
                facts,
                node,
                "QG122",
                f"Tool `{qualname}` 返回裸字符串而不是稳定结构。",
                symbol=qualname,
                severity="warning",
                suggestion="返回包含状态、数据、错误和后续动作字段的固定 JSON 对象。",
            )
        )
    write_calls = [
        item
        for item in nodes
        if isinstance(item, ast.Call)
        and call_name(item).rsplit(".", 1)[-1].lower() in WRITE_CALL_MARKERS
    ]
    if node.name.lower().startswith(READ_ONLY_NAME_PREFIXES) and write_calls:
        findings.append(
            make_finding(
                facts,
                write_calls[0],
                "QG123",
                f"读取型 Tool `{qualname}` 调用了写操作 `{call_name(write_calls[0])}`。",
                symbol=qualname,
                severity="warning",
                suggestion="查询工具保持只读和幂等；写操作使用明确命名及安全确认边界。",
            )
        )
    dict_keys = tool_return_dict_keys(returns)
    docstring = ast.get_docstring(node, clean=False) or ""
    undocumented = sorted(key for key in dict_keys if key not in docstring)
    if dict_keys and undocumented:
        findings.append(
            make_finding(
                facts,
                node,
                "QG131",
                f"Tool `{qualname}` 的 docstring 未说明返回字段：{undocumented}。",
                symbol=qualname,
                severity="warning",
                suggestion="在 Returns 中列出固定 JSON 字段、类型、语义和模型如何消费。",
                evidence={"fields": sorted(dict_keys), "undocumented": undocumented},
            )
        )
    return findings


def tool_return_dict_keys(returns: list[ast.Return]) -> set[str]:
    """
    提取 Tool 字典返回值中的静态键名。

    Args:
        returns: Tool 的 Return 节点列表。

    Returns:
        全部可静态确定的字符串键集合。
    """
    keys: set[str] = set()
    for item in returns:
        if not isinstance(item.value, ast.Dict):
            continue
        keys.update(
            key.value
            for key in item.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        )
    return keys


def check_history_trace_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    nodes: list[ast.AST],
    config: GuardConfig,
) -> list[Finding]:
    """
    检查 History、Trace 和失败路径的数据边界。

    Args:
        facts: 当前模块静态事实。
        node: 函数定义。
        qualname: 函数限定名称。
        nodes: 当前函数直接拥有的 AST 节点。
        config: 当前仓库质量配置。

    Returns:
        History 与 Trace 发现列表。
    """
    call_names = {call_name(item).lower() for item in nodes if isinstance(item, ast.Call)}
    uses_history = any(
        any(marker in name for marker in config.history_markers)
        and not any(marker in name for marker in config.trace_markers)
        for name in call_names
    )
    uses_trace = any(
        any(marker in name for marker in config.trace_markers)
        and not any(marker in name for marker in config.history_markers)
        for name in call_names
    )
    findings: list[Finding] = []
    if uses_history and uses_trace:
        findings.append(
            make_finding(
                facts,
                node,
                "QG129",
                f"`{qualname}` 同时处理 History 与 Trace。",
                symbol=qualname,
                severity="warning",
                suggestion="成功对话历史与完整执行轨迹使用独立模型、存储入口和失败过滤规则。",
            )
        )
    for item in nodes:
        if not isinstance(item, ast.ExceptHandler):
            continue
        history_calls = [
            child
            for statement in item.body
            for child in ast.walk(statement)
            if isinstance(child, ast.Call)
            and any(marker in call_name(child).lower() for marker in config.history_markers)
        ]
        if history_calls:
            findings.append(
                make_finding(
                    facts,
                    history_calls[0],
                    "QG130",
                    "异常处理路径把失败步骤写入 History。",
                    symbol=qualname,
                    severity="error",
                    suggestion="失败过程只进入 Trace；History 仅保存成功完成的用户对话和结果。",
                )
            )
    return findings


def check_state_write_types(signals: RepositorySignals) -> list[Finding]:
    """
    检查同一状态字段被写入多种静态结构。

    Args:
        signals: 跨模块静态信号。

    Returns:
        状态字段类型不一致发现列表。
    """
    findings: list[Finding] = []
    for (contract, field_name), writes in signals.state_writes.items():
        value_types = {value_type for value_type, _, _ in writes}
        if len(value_types) <= 1:
            continue
        _, path, line = writes[0]
        findings.append(
            Finding(
                code="QG126",
                severity="error",
                confidence="medium",
                path=str(path),
                line=line,
                column=1,
                message=f"状态字段 `{contract}.{field_name}` 被写入多种结构：{sorted(value_types)}。",
                symbol=f"{contract}.{field_name}",
                suggestion="在状态模型中固定字段类型和归并语义。",
                evidence={
                    "types": sorted(value_types),
                    "writes": [
                        f"{item_path}:{item_line}:{value_type}"
                        for value_type, item_path, item_line in writes
                    ],
                },
            )
        )
    return findings
