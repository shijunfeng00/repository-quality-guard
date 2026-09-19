from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import cast

from .ast_utils import decorator_names, dotted_name
from .facts import ModuleFacts
from .model import Confidence, Definition, Finding, Severity

BOOL_TEXT_VALUES = {"0", "1", "false", "true", "False", "True", "FALSE", "TRUE"}
BOOL_INT_VALUES = {0, 1}
BOOL_NAME_PREFIXES = ("is_", "has_", "can_", "should_", "enable_", "disable_", "use_")
BOOL_NAME_WORDS = {
    "active",
    "compress",
    "debug",
    "disabled",
    "enabled",
    "force",
    "optional",
    "recursive",
    "replace",
    "required",
    "stream",
    "strict",
    "trace",
    "verbose",
}
CONTRACT_NAME_HINTS = {
    "config",
    "metadata",
    "payload",
    "response",
    "state",
}
MUTATING_METHODS = {
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "pop",
    "popitem",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "update",
}
WRITE_CALL_MARKERS = {
    "add",
    "append",
    "commit",
    "create",
    "delete",
    "execute",
    "insert",
    "patch",
    "post",
    "put",
    "remove",
    "save",
    "send",
    "set",
    "update",
    "write",
    "write_bytes",
    "write_text",
}
READ_ONLY_NAME_PREFIXES = ("find", "fetch", "get", "list", "load", "query", "read", "search")
PROMPT_NAME_MARKERS = ("prompt", "instruction", "system_message", "system_prompt")
PROMPT_NEGATIVE_MARKERS = ("禁止", "不得", "不要", "严禁", "必须", "只能", "不可", "务必")
GENERIC_FUNCTION_NAMES = {"do", "handle", "manage", "process", "run_task", "execute_task"}
CONFIG_CALLS = {"os.getenv", "os.environ.get", "dotenv.get_key", "decouple.config"}
CONFIG_FILE_CALLS = {"json.load", "tomllib.load", "yaml.load", "yaml.safe_load"}
NETWORK_CALL_PREFIXES = ("requests.", "httpx.", "aiohttp.")
BLOCKING_CALL_PREFIXES = (
    "requests.",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "time.sleep",
)


@dataclass(slots=True, frozen=True)
class ParsedModule:
    """
    保存已解析模块及其基础事实。

    Args:
        facts: 基础扫描阶段生成的模块事实。
        tree: 模块对应的 AST。

    Returns:
        None。
    """

    facts: ModuleFacts
    tree: ast.Module
    nodes: tuple[ast.AST, ...]
    parents: dict[ast.AST, ast.AST]


@dataclass(slots=True, frozen=True)
class StateField:
    """
    描述状态类型中的一个声明字段。

    Args:
        name: 字段名称。
        annotation: 字段类型标注的点分文本。
        is_sequence: 字段是否声明为列表或其他序列容器。

    Returns:
        None。
    """

    name: str
    annotation: str
    is_sequence: bool


@dataclass(slots=True)
class StateContract:
    """
    保存可静态识别的状态类型字段契约。

    Args:
        name: 状态类型简单名称。
        module: 状态类型所属模块。
        fields: 字段名称到声明信息的映射。
        members: 状态类型自身定义的方法和属性名称。
        mapping_style: 状态是否以 TypedDict/dict 协议为主。
        total: TypedDict 是否要求所有字段始终存在；非 TypedDict 时为 None。

    Returns:
        None。
    """

    name: str
    module: str
    fields: dict[str, StateField] = field(default_factory=dict)
    members: set[str] = field(default_factory=set)
    mapping_style: bool = False
    total: bool | None = None


@dataclass(slots=True, frozen=True)
class FunctionSignature:
    """
    保存函数调用规则需要的参数签名。

    Args:
        module: 函数所属模块。
        qualname: 模块内限定名称。
        name: 简单函数名称。
        boolean_positions: 位置参数中声明为 bool 的索引集合。
        boolean_keywords: 声明为 bool 的参数名称集合。

    Returns:
        None。
    """

    module: str
    qualname: str
    name: str
    boolean_positions: frozenset[int]
    boolean_keywords: frozenset[str]


@dataclass(slots=True)
class RepositorySignals:
    """
    保存跨模块规则需要汇总的静态信号。

    Args:
        env_reads: 环境变量键到读取位置的映射。
        config_defaults: 配置键及其默认值到读取位置的映射。
        state_writes: 状态类型和字段到写入类型及位置的映射。
        abstract_classes: 抽象类名称到定义位置的映射。
        subclass_bases: 基类名称到实现类定义列表的映射。
        prompt_literals: 长 Prompt 片段到出现位置的映射。
        module_constants: 模块级全大写常量名称到定义位置的映射。
        referenced_names: 仓库内静态读取、属性访问或显式导入过的名称集合。
        registered_tools: 通过装饰器或 Tools.add(...) 注册的真实工具方法。

    Returns:
        None。
    """

    env_reads: dict[str, list[tuple[Path, int, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    config_defaults: dict[tuple[str, str], list[tuple[Path, int]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    state_writes: dict[tuple[str, str], list[tuple[str, Path, int]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    abstract_classes: dict[str, tuple[Path, int, str]] = field(default_factory=dict)
    subclass_bases: dict[str, list[tuple[Path, int, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    prompt_literals: dict[str, list[tuple[Path, int, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    module_constants: dict[str, list[tuple[Path, int, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    referenced_names: set[str] = field(default_factory=set)
    registered_tools: set[str] = field(default_factory=set)


def parse_modules(facts: list[ModuleFacts]) -> list[ParsedModule]:
    """
    把基础模块事实转换为可供策略规则复用的 AST。

    Args:
        facts: 基础扫描阶段生成的模块事实列表。

    Returns:
        语法有效模块的解析结果列表。
    """
    parsed: list[ParsedModule] = []
    for module_facts in facts:
        if module_facts.tree is not None:
            tree = module_facts.tree
            nodes = tuple(ast.walk(tree))
            parents = {
                child: parent
                for parent in nodes
                for child in ast.iter_child_nodes(parent)
            }
            parsed.append(ParsedModule(module_facts, tree, nodes, parents))
    return parsed


def iter_scoped_definitions(
    tree: ast.Module,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, str, str]]:
    """
    按词法作用域枚举模块中的类、函数和方法。

    Args:
        tree: 待遍历的模块 AST。

    Returns:
        节点、限定名称和所属类名称组成的列表。
    """
    result: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, str, str]] = []

    def walk(body: list[ast.stmt], prefix: list[str], class_name: str) -> None:
        """
        递归遍历当前词法作用域。

        Args:
            body: 当前作用域语句列表。
            prefix: 当前限定名称前缀。
            class_name: 当前所属类名称。

        Returns:
            None。
        """
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                qualname = ".".join([*prefix, statement.name])
                result.append((statement, qualname, statement.name))
                walk(statement.body, [*prefix, statement.name], statement.name)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = ".".join([*prefix, statement.name])
                result.append((statement, qualname, class_name))
                walk(statement.body, [*prefix, statement.name], class_name)

    walk(tree.body, [], "")
    return result


def make_finding(
    facts: ModuleFacts,
    node: ast.AST,
    code: str,
    message: str,
    *,
    symbol: str,
    severity: str = "warning",
    confidence: str = "high",
    suggestion: str = "",
    evidence: dict[str, object] | None = None,
) -> Finding:
    """
    构造带源码位置和结构化证据的发现。

    Args:
        facts: 当前模块事实。
        node: 问题对应的 AST 节点。
        code: 规则编号。
        message: 问题说明。
        symbol: 所属符号名称。
        severity: 严重级别。
        confidence: 静态判断置信度。
        suggestion: 修复或人工审查方向。
        evidence: 附加结构化证据。

    Returns:
        完整质量发现对象。
    """
    return Finding(
        code=code,
        severity=cast(Severity, severity),
        confidence=cast(Confidence, confidence),
        path=str(facts.path),
        line=node.lineno,
        column=node.col_offset + 1,
        message=message,
        symbol=symbol,
        suggestion=suggestion,
        evidence={} if evidence is None else evidence,
    )


def all_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    """
    返回函数签名中的全部显式参数。

    Args:
        node: 函数或异步函数定义。

    Returns:
        按源码顺序排列的位置、普通和仅关键字参数。
    """
    return [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]


def parameter_defaults(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, ast.expr | None]:
    """
    建立参数名称到默认值表达式的映射。

    Args:
        node: 函数或异步函数定义。

    Returns:
        所有显式参数及其默认值；没有默认值时为 None。
    """
    positional = [*node.args.posonlyargs, *node.args.args]
    positional_defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(node.args.defaults)
    ) + list(node.args.defaults)
    result = {
        argument.arg: default
        for argument, default in zip(positional, positional_defaults, strict=True)
    }
    result.update(
        {
            argument.arg: default
            for argument, default in zip(
                node.args.kwonlyargs,
                node.args.kw_defaults,
                strict=True,
            )
        }
    )
    return result


def annotation_names(annotation: ast.expr | None) -> set[str]:
    """
    提取类型标注中的名称集合。

    Args:
        annotation: 待解析的类型标注。

    Returns:
        标注 AST 中出现的简单名称集合。
    """
    if annotation is None:
        return set()
    names: set[str] = set()
    for item in ast.walk(annotation):
        if isinstance(item, ast.Name):
            names.add(item.id)
        elif isinstance(item, ast.Attribute):
            names.add(item.attr)
    return names


def annotation_text(annotation: ast.expr | None) -> str:
    """
    把类型标注转换为稳定文本。

    Args:
        annotation: 待转换的类型标注。

    Returns:
        可读标注文本；无标注时返回空字符串。
    """
    return "" if annotation is None else ast.unparse(annotation)


def is_bool_name(name: str) -> bool:
    """
    判断参数名是否具有明确布尔语义。

    Args:
        name: 参数或字段名称。

    Returns:
        命中布尔前缀或布尔词时返回 True。
    """
    lowered = name.lower()
    return lowered.startswith(BOOL_NAME_PREFIXES) or lowered in BOOL_NAME_WORDS


def is_bool_literal(node: ast.expr | None) -> bool:
    """
    判断表达式是否为 True 或 False 字面量。

    Args:
        node: 待检查表达式。

    Returns:
        表达式为 bool 常量时返回 True。
    """
    return isinstance(node, ast.Constant) and type(node.value) is bool


def literal_value(node: ast.expr | None) -> object:
    """
    提取简单常量表达式的 Python 值。

    Args:
        node: 待解析的表达式。

    Returns:
        常量值；无法静态求值时返回专用哨兵字符串。
    """
    return node.value if isinstance(node, ast.Constant) else "<dynamic>"


@cache
def _cached_direct_body_nodes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ast.AST, ...]:
    """
    缓存函数直接控制的 AST 节点遍历结果。

    Args:
        node: 待遍历的函数定义。

    Returns:
        当前函数直接控制的 AST 节点元组。
    """
    result: list[ast.AST] = []
    pending = list(reversed(node.body))
    while pending:
        child = pending.pop()
        result.append(child)
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(reversed(list(ast.iter_child_nodes(child))))
    return tuple(result)


def direct_body_nodes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    """
    枚举函数体节点但跳过嵌套定义。

    Args:
        node: 待遍历的函数定义。

    Returns:
        当前函数直接控制的 AST 节点列表。
    """
    return list(_cached_direct_body_nodes(node))


def function_returns(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Return]:
    """
    返回当前函数直接拥有的 Return 节点。

    Args:
        node: 待分析的函数定义。

    Returns:
        排除嵌套函数与类后的 Return 节点列表。
    """
    return [item for item in direct_body_nodes(node) if isinstance(item, ast.Return)]


def expression_type(node: ast.expr | None) -> str:
    """
    生成赋值表达式的粗粒度静态类型名称。

    Args:
        node: 待分类表达式。

    Returns:
        容器、常量、调用目标或 AST 节点类型名称。
    """
    if node is None:
        return "None"
    if isinstance(node, ast.Constant):
        return type(node.value).__name__
    container_types = (
        ((ast.List, ast.ListComp), "list"),
        ((ast.Dict, ast.DictComp), "dict"),
        ((ast.Set, ast.SetComp), "set"),
        ((ast.Tuple,), "tuple"),
        ((ast.GeneratorExp,), "generator"),
    )
    for node_types, type_name in container_types:
        if isinstance(node, node_types):
            return type_name
    if isinstance(node, ast.Call):
        return "call:" + (dotted_name(node.func) or "dynamic")
    return type(node).__name__


def call_name(node: ast.Call) -> str:
    """
    返回调用目标的点分名称。

    Args:
        node: 调用表达式。

    Returns:
        可解析目标名称，否则返回空字符串。
    """
    return dotted_name(node.func)


def is_boundary_module(module: str, markers: tuple[str, ...]) -> bool:
    """
    判断模块是否属于外部输入或配置边界。

    Args:
        module: 模块点分名称。
        markers: 边界模块名称片段。

    Returns:
        模块路径命中任一边界片段时返回 True。
    """
    lowered = module.lower()
    return any(marker.lower() in lowered for marker in markers)


def collect_function_signatures(parsed: list[ParsedModule]) -> list[FunctionSignature]:
    """
    收集跨模块调用规则需要的布尔参数签名。

    Args:
        parsed: 已解析模块列表。

    Returns:
        仓库函数签名列表。
    """
    signatures: list[FunctionSignature] = []
    for module in parsed:
        for node, qualname, _ in iter_scoped_definitions(module.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            positional = [*node.args.posonlyargs, *node.args.args]
            if positional and positional[0].arg in {"self", "cls"}:
                positional = positional[1:]
            boolean_positions = {
                index
                for index, argument in enumerate(positional)
                if "bool" in annotation_names(argument.annotation) or is_bool_name(argument.arg)
            }
            boolean_keywords = {
                argument.arg
                for argument in all_parameters(node)
                if argument.arg not in {"self", "cls"}
                and ("bool" in annotation_names(argument.annotation) or is_bool_name(argument.arg))
            }
            signatures.append(
                FunctionSignature(
                    module=module.facts.module,
                    qualname=qualname,
                    name=node.name,
                    boolean_positions=frozenset(boolean_positions),
                    boolean_keywords=frozenset(boolean_keywords),
                )
            )
    return signatures


def is_compatibility_name(name: str) -> bool:
    """
    判断名称是否明确表示旧版、回退或兼容入口。

    Args:
        name: 已转小写的定义名称。

    Returns:
        名称以前后缀方式命中兼容标记时返回 True。
    """
    prefixes = ("compat_", "deprecated_", "fallback_", "legacy_", "old_")
    suffixes = ("_compat", "_deprecated", "_fallback", "_legacy", "_old", "_v1", "_v2")
    return name.startswith(prefixes) or name.endswith(suffixes)


def definition_index(definitions: list[Definition]) -> dict[str, list[Definition]]:
    """
    按简单名称建立定义索引。

    Args:
        definitions: 仓库内定义列表。

    Returns:
        简单名称到定义列表的映射。
    """
    result: dict[str, list[Definition]] = defaultdict(list)
    for definition in definitions:
        result[definition.name].append(definition)
    return result


def class_is_abstract(node: ast.ClassDef) -> bool:
    """
    判断类是否显式承担抽象接口职责。

    Args:
        node: 类定义节点。

    Returns:
        继承 ABC/Protocol 或包含 abstractmethod 时返回 True。
    """
    bases = {dotted_name(base).rsplit(".", 1)[-1] for base in node.bases}
    if bases & {"ABC", "Protocol"}:
        return True
    return any(
        decorator.rsplit(".", 1)[-1] == "abstractmethod"
        for statement in node.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in decorator_names(statement.decorator_list)
    )
