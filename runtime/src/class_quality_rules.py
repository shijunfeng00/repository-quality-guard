from __future__ import annotations

import ast
from collections import defaultdict

from .ast_utils import decorator_names, dotted_name
from .config import GuardConfig
from .facts import ModuleFacts
from .model import Finding
from .policy_common import (
    RepositorySignals,
    class_is_abstract,
    direct_body_nodes,
    is_compatibility_name,
    make_finding,
)

MIN_CLASS_METHODS_FOR_FIELD_LOCALITY = 5


def check_class_rules(
    facts: ModuleFacts,
    node: ast.ClassDef,
    qualname: str,
    config: GuardConfig,
    signals: RepositorySignals,
) -> list[Finding]:
    """
    检查类职责、方法形态和无价值抽象信号。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的类定义。
        qualname: 类限定名称。
        config: 当前仓库质量配置。
        signals: 跨模块信号汇总对象。

    Returns:
        当前类对应的发现列表。
    """
    methods = class_methods(node)
    findings = check_class_method_shape(facts, node, qualname, methods, config)
    findings.extend(check_passthrough_members(facts, methods, qualname))
    init = next((method for method in methods if method.name == "__init__"), None)
    if init is not None:
        findings.extend(check_constructor_shape(facts, init, qualname, config))
    findings.extend(check_field_locality(facts, node, qualname, config))
    register_class_relationships(facts, node, qualname, signals)
    if is_compatibility_name(node.name.lower()):
        findings.append(
            make_finding(
                facts,
                node,
                "QG094",
                f"类名 `{node.name}` 表明存在兼容、旧版或回退实现。",
                symbol=qualname,
                severity="info",
                suggestion="搜索真实调用和外部契约；没有兼容需求时删除旧实现。",
            )
        )
    return findings


def class_methods(node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """
    返回类直接定义的方法。

    Args:
        node: 类定义。

    Returns:
        不包含嵌套类方法的方法列表。
    """
    return [
        statement
        for statement in node.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def check_class_method_shape(
    facts: ModuleFacts,
    node: ast.ClassDef,
    qualname: str,
    methods: list[ast.FunctionDef | ast.AsyncFunctionDef],
    config: GuardConfig,
) -> list[Finding]:
    """
    检查公开、私有、静态及无实例状态方法的比例。

    Args:
        facts: 当前模块静态事实。
        node: 类定义。
        qualname: 类限定名称。
        methods: 类直接定义的方法。
        config: 当前仓库质量配置。

    Returns:
        类方法形态发现列表。
    """
    public = [method for method in methods if not method.name.startswith("_")]
    private = [
        method for method in methods if method.name.startswith("_") and method.name != "__init__"
    ]
    static_like = [
        method
        for method in methods
        if set(decorator_names(method.decorator_list)) & {"staticmethod", "classmethod"}
    ]
    no_self = [method for method in methods if method_does_not_use_self(method)]
    findings: list[Finding] = []
    if len(public) == 1 and len(methods) <= config.single_method_class_max_methods:
        findings.append(
            make_finding(
                facts,
                node,
                "QG080",
                f"类 `{qualname}` 只有一个公开方法，可能只是函数包装。",
                symbol=qualname,
                severity="info",
                suggestion="确认该类是否拥有生命周期、状态或协议边界；否则优先使用直接函数。",
            )
        )
    if public and len(private) >= config.private_helper_ratio * len(public):
        findings.append(
            make_finding(
                facts,
                node,
                "QG081",
                f"类 `{qualname}` 有 {len(private)} 个私有 helper，仅 {len(public)} 个公开方法。",
                symbol=qualname,
                severity="info",
                suggestion="检查 helper 是否只是切碎主流程，或是否应归入独立领域组件。",
            )
        )
    if methods and len(static_like) / len(methods) >= config.static_method_ratio:
        findings.append(
            make_finding(
                facts,
                node,
                "QG082",
                f"类 `{qualname}` 的大部分方法是 staticmethod/classmethod。",
                symbol=qualname,
                severity="info",
                suggestion="若类不持有真实状态，考虑改为模块函数或更明确的领域对象。",
            )
        )
    if methods and len(no_self) / len(methods) >= config.no_self_method_ratio:
        findings.append(
            make_finding(
                facts,
                node,
                "QG083",
                f"类 `{qualname}` 中 {len(no_self)}/{len(methods)} 个实例方法不使用实例状态。",
                symbol=qualname,
                severity="info",
                suggestion="检查是否为了面向对象而面向对象，或是否应收敛为函数。",
            )
        )
    if is_single_field_dataclass(node):
        findings.append(
            make_finding(
                facts,
                node,
                "QG084",
                f"dataclass `{qualname}` 只有一个字段，可能只是再次包装已有值。",
                symbol=qualname,
                severity="info",
                suggestion="仅在该类型提供独立不变量或领域语义时保留。",
            )
        )
    return findings


def check_passthrough_members(
    facts: ModuleFacts,
    methods: list[ast.FunctionDef | ast.AsyncFunctionDef],
    qualname: str,
) -> list[Finding]:
    """
    检查只转发成员属性或调用的方法。

    Args:
        facts: 当前模块静态事实。
        methods: 类直接定义的方法。
        qualname: 类限定名称。

    Returns:
        无价值中间层候选列表。
    """
    return [
        make_finding(
            facts,
            method,
            "QG085",
            f"`{qualname}.{method.name}` 只转发另一个对象的属性或方法。",
            symbol=f"{qualname}.{method.name}",
            severity="info",
            suggestion="无独立契约、权限、事务或转换时删除该中间层。",
        )
        for method in methods
        if is_passthrough_property(method) or is_passthrough_method(method)
    ]


def register_class_relationships(
    facts: ModuleFacts,
    node: ast.ClassDef,
    qualname: str,
    signals: RepositorySignals,
) -> None:
    """
    登记继承关系和抽象类供仓库级规则使用。

    Args:
        facts: 当前模块静态事实。
        node: 类定义。
        qualname: 类限定名称。
        signals: 跨模块信号汇总对象。

    Returns:
        None。
    """
    for base in (dotted_name(item).rsplit(".", 1)[-1] for item in node.bases):
        if base:
            signals.subclass_bases[base].append((facts.path, node.lineno, qualname))
    if class_is_abstract(node):
        signals.abstract_classes[node.name] = (facts.path, node.lineno, qualname)


def method_does_not_use_self(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """
    判断实例方法是否不读取 self/cls。

    Args:
        node: 方法定义。

    Returns:
        普通实例方法未使用首参数时返回 True。
    """
    decorators = {name.rsplit(".", 1)[-1] for name in decorator_names(node.decorator_list)}
    if decorators & {"staticmethod", "classmethod", "abstractmethod", "property"}:
        return False
    positional = [*node.args.posonlyargs, *node.args.args]
    if not positional or positional[0].arg not in {"self", "cls"}:
        return False
    receiver = positional[0].arg
    return not any(
        isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load) and item.id == receiver
        for item in direct_body_nodes(node)
    )


def is_single_field_dataclass(node: ast.ClassDef) -> bool:
    """
    判断类是否为只有一个声明字段的 dataclass。

    Args:
        node: 类定义。

    Returns:
        命中 dataclass 且只有一个字段时返回 True。
    """
    decorators = {name.rsplit(".", 1)[-1] for name in decorator_names(node.decorator_list)}
    if "dataclass" not in decorators:
        return False
    fields = [statement for statement in node.body if isinstance(statement, ast.AnnAssign)]
    return len(fields) == 1


def is_passthrough_property(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """
    判断 property 是否只返回内部对象属性。

    Args:
        node: 方法定义。

    Returns:
        命中只读属性转发时返回 True。
    """
    decorators = {name.rsplit(".", 1)[-1] for name in decorator_names(node.decorator_list)}
    body = body_without_docstring(node)
    return (
        "property" in decorators
        and len(body) == 1
        and isinstance(body[0], ast.Return)
        and isinstance(body[0].value, ast.Attribute)
        and isinstance(body[0].value.value, ast.Attribute)
    )


def is_passthrough_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """
    判断方法是否只把参数原样传给成员对象。

    Args:
        node: 方法定义。

    Returns:
        命中单层成员调用转发时返回 True。
    """
    body = body_without_docstring(node)
    if len(body) != 1:
        return False
    statement = body[0]
    value = statement.value if isinstance(statement, (ast.Return, ast.Expr)) else None
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Attribute)
        and isinstance(value.func.value.value, ast.Name)
        and value.func.value.value.id in {"self", "cls"}
    )


def body_without_docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.stmt]:
    """
    返回排除首部 docstring 的方法体。

    Args:
        node: 方法定义。

    Returns:
        不包含 docstring 语句的语句列表。
    """
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def check_constructor_shape(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    config: GuardConfig,
) -> list[Finding]:
    """
    检查构造函数依赖数量和机械保存参数模式。

    Args:
        facts: 当前模块静态事实。
        node: 构造方法定义。
        qualname: 类限定名称。
        config: 当前仓库质量配置。

    Returns:
        构造函数结构发现列表。
    """
    parameters = [
        argument.arg
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if argument.arg not in {"self", "cls"}
    ]
    stored = {
        target.attr
        for statement in body_without_docstring(node)
        if isinstance(statement, (ast.Assign, ast.AnnAssign))
        for target in (
            statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        )
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    }
    if len(parameters) <= config.max_constructor_dependencies:
        return []
    return [
        make_finding(
            facts,
            node,
            "QG086",
            f"`{qualname}.__init__` 接收 {len(parameters)} 个依赖，并保存其中 {len(stored)} 个。",
            symbol=f"{qualname}.__init__",
            severity="warning",
            suggestion="检查类是否承担过多职责；不要仅用一个大配置对象掩盖依赖膨胀。",
            evidence={"parameters": parameters, "stored_fields": sorted(stored)},
        )
    ]


def check_field_locality(
    facts: ModuleFacts,
    node: ast.ClassDef,
    qualname: str,
    config: GuardConfig,
) -> list[Finding]:
    """
    检查构造阶段保存的实例字段是否只服务于单个方法。

    Args:
        facts: 当前模块静态事实。
        node: 类定义。
        qualname: 类限定名称。
        config: 当前仓库质量配置。

    Returns:
        字段局部性发现列表。
    """
    decorators = {name.rsplit(".", 1)[-1] for name in decorator_names(node.decorator_list)}
    methods = class_methods(node)
    if "dataclass" in decorators or len(methods) < MIN_CLASS_METHODS_FOR_FIELD_LOCALITY:
        return []
    init = next((method for method in methods if method.name == "__init__"), None)
    if init is None:
        return []
    stored_fields = fields_stored_in_constructor(init)
    usages = field_method_usages(methods, stored_fields)
    local_fields = sorted(field for field, method_names in usages.items() if len(method_names) == 1)
    if len(local_fields) < config.single_use_field_threshold:
        return []
    return [
        make_finding(
            facts,
            node,
            "QG087",
            f"类 `{qualname}` 有 {len(local_fields)} 个构造字段只在单个方法中使用。",
            symbol=qualname,
            severity="info",
            suggestion="检查这些字段是否应为局部变量，或类是否混合多个彼此无关的流程。",
            evidence={"fields": local_fields},
        )
    ]


def fields_stored_in_constructor(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    """
    提取构造函数写入的 self 字段。

    Args:
        node: 构造函数定义。

    Returns:
        构造阶段保存的实例字段集合。
    """
    fields: set[str] = set()
    for item in direct_body_nodes(node):
        targets: list[ast.expr] = []
        if isinstance(item, ast.Assign):
            targets = list(item.targets)
        elif isinstance(item, (ast.AnnAssign, ast.AugAssign)):
            targets = [item.target]
        fields.update(
            target.attr
            for target in targets
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        )
    return fields


def field_method_usages(
    methods: list[ast.FunctionDef | ast.AsyncFunctionDef],
    stored_fields: set[str],
) -> dict[str, set[str]]:
    """
    统计构造字段被哪些非构造方法使用。

    Args:
        methods: 类直接定义的方法。
        stored_fields: 构造阶段保存的字段集合。

    Returns:
        字段名到使用方法名称集合的映射。
    """
    usages: dict[str, set[str]] = defaultdict(set)
    for method in methods:
        if method.name == "__init__":
            continue
        for item in direct_body_nodes(method):
            if (
                isinstance(item, ast.Attribute)
                and isinstance(item.value, ast.Name)
                and item.value.id == "self"
                and item.attr in stored_fields
            ):
                usages[item.attr].add(method.name)
    return usages
