from __future__ import annotations

import ast
from dataclasses import dataclass

from .core_rule_contract import CoreQualityRule
import re

from .ast_utils import dotted_name
from .config import GuardConfig
from .facts import ModuleFacts
from .external_api_binding import BLOCKING_APIS, NETWORK_APIS, bound_external_api
from .model import Finding
from .policy_common import (
    BOOL_INT_VALUES,
    BOOL_TEXT_VALUES,
    all_parameters,
    annotation_names,
    call_name,
    direct_body_nodes,
    is_bool_literal,
    is_boundary_module,
    literal_value,
    parameter_defaults,
)


def check_signature_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    config: GuardConfig,
) -> list[Finding]:
    """
    检查布尔输入、变长参数和关键字参数袋。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。
        config: 当前仓库质量配置。

    Returns:
        签名相关发现列表。
    """
    findings = check_parameter_defaults(facts, node, qualname)
    findings.extend(check_star_signature(facts, node, qualname))
    findings.extend(check_kwargs_contract(facts, node, qualname))
    findings.extend(check_deleted_parameters(facts, node, qualname))
    if not is_boundary_module(facts.module, config.boundary_module_markers):
        findings.extend(check_boolean_coercion(facts, node, qualname))
    return findings


def check_parameter_defaults(
    facts: ModuleFacts, node: ast.FunctionDef | ast.AsyncFunctionDef, qualname: str
) -> list[Finding]:
    """Inspect each non-receiver argument's default and annotation contract.

    Align signature parameters with their defaults once. For each argument
    except self/cls, check boolean literals, mixed boolean annotations and
    mutable or nullable defaults; preserve per-parameter finding order.

    Args:
        facts: Module source facts for parameter findings.
        node: Callable declaration supplying parameters and default expressions.
        qualname: Qualified callable identity used in the report.

    Returns:
        Findings for proven inconsistencies in signature defaults."""
    findings: list[Finding] = []
    defaults = parameter_defaults(node)
    for argument in all_parameters(node):
        if argument.arg in {"self", "cls"}:
            continue
        names = annotation_names(argument.annotation)
        default = defaults[argument.arg]
        value = literal_value(default)
        context = ParameterDefaultRuleContext(
            facts, argument, default, qualname, names, value, "bool" in names
        )
        for detector in (BoolDefaultValueRule(), MixedBooleanAnnotationRule()):
            findings.extend(detector.evaluate(context))
        findings.extend(check_single_default(facts, argument, default, qualname))
    return findings


def check_single_default(
    facts: ModuleFacts, argument: ast.arg, default: ast.expr | None, qualname: str
) -> list[Finding]:
    """Run mutable and nullable-default rules for one declared parameter.

    Args:
        facts: Module facts for the parameter's declaration location.
        argument: Argument with its original annotation AST.
        default: Corresponding expression, or None when no default is supplied.
        qualname: Qualified identity of the enclosing callable.

    Returns:
        Parameter default findings from the two dedicated detectors."""
    context = ParameterDefaultRuleContext(
        facts,
        argument,
        default,
        qualname,
        annotation_names(argument.annotation),
        literal_value(default),
        False,
    )
    findings: list[Finding] = []
    for detector in (MutableParameterDefaultRule(), NullableParameterDefaultRule()):
        findings.extend(detector.evaluate(context))
    return findings


def annotation_allows_none(annotation: ast.expr) -> bool:
    """
    判断类型标注是否显式包含 None。

    Args:
        annotation: 待分析的类型标注。

    Returns:
        标注包含 None、Optional 或 NoneType 时返回 True。
    """
    return bool(annotation_names(annotation) & {"None", "NoneType", "Optional"}) or any(
        isinstance(item, ast.Constant) and item.value is None
        for item in ast.walk(annotation)
    )


_KWARGS_CONTRACT_PATTERN = re.compile(
    r"\s*#\s*RQG-KWARGS:\s*keys="
    r"([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)"
    r"\s*;\s*mode=forward-only\s*"
)


def check_star_signature(facts, node, qualname):
    """Check a callable's variadic positional signature for QG policy violations.

    Args:
        facts: Module source and Finding attribution context.
        node: Function AST whose *args contract is inspected.
        qualname: Qualified callable name for the finding.

    Returns:
        Findings produced by VarPositionalSignatureRule, or []."""
    return VarPositionalSignatureRule().evaluate(
        SignatureRuleContext(facts, node, qualname)
    )


def check_kwargs_contract(facts, node, qualname):
    """Validate unrestricted keyword bags against an explicit signature contract.

    Args:
        facts: Module source facts used for rule evidence.
        node: Function AST including **kwargs and any supported typed contract.
        qualname: Qualified name of the declaration.

    Returns:
        OpenKeywordBagRule findings when keyword shape is not constrained."""
    return OpenKeywordBagRule().evaluate(SignatureRuleContext(facts, node, qualname))


def _typed_dict_contract_keys(
    facts: ModuleFacts,
    annotation: ast.expr | None,
) -> tuple[str, ...] | None:
    """解析同模块 class-style ``Unpack[TypedDict]`` 的有限 key 集。"""
    if not isinstance(annotation, ast.Subscript) or facts.tree is None:
        return None
    outer = dotted_name(annotation.value)
    target_name = dotted_name(annotation.slice)
    if (
        not outer
        or outer.rsplit(".", 1)[-1] != "Unpack"
        or not target_name
        or "." in target_name
    ):
        return None
    for statement in facts.tree.body:
        if not isinstance(statement, ast.ClassDef) or statement.name != target_name:
            continue
        bases = [dotted_name(base) for base in statement.bases]
        if not any(name and name.rsplit(".", 1)[-1] == "TypedDict" for name in bases):
            return None
        return tuple(
            item.target.id
            for item in statement.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        )
    return None


def _structured_kwargs_contract_keys(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ...] | None:
    """读取紧邻定义的结构化 ``RQG-KWARGS`` 有限 key 契约。"""
    if not facts.source:
        return None
    anchor = min(
        [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
    )
    if anchor <= 1:
        return None
    lines = facts.source.splitlines()
    raw = lines[anchor - 2]
    match = _KWARGS_CONTRACT_PATTERN.fullmatch(raw)
    if match is None:
        return None
    keys = tuple(item.strip() for item in match.group(1).split(","))
    if not keys or len(set(keys)) != len(keys):
        return None
    return keys


def _kwargs_is_transparent_forwarding(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> bool:
    """判断 kwargs 是否只作为 ``callee(**kwargs)`` 原样传给明确调用。"""
    nodes = direct_body_nodes(node)
    forwarded_loads = {
        id(keyword.value)
        for item in nodes
        if isinstance(item, ast.Call)
        for keyword in item.keywords
        if keyword.arg is None
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == name
    }
    if not forwarded_loads:
        return False
    if any(
        isinstance(item, ast.Name)
        and item.id == name
        and isinstance(item.ctx, (ast.Store, ast.Del))
        for item in nodes
    ):
        return False
    loads = [
        item
        for item in nodes
        if isinstance(item, ast.Name)
        and item.id == name
        and isinstance(item.ctx, ast.Load)
    ]
    return bool(loads) and all(id(item) in forwarded_loads for item in loads)


def check_deleted_parameters(facts, node, qualname):
    """Find parameters that are declared but intentionally discarded by the body.

    Args:
        facts: Module evidence used for exact source attribution.
        node: Callable AST containing arguments and direct-body references.
        qualname: Owning callable's qualified identity.

    Returns:
        DiscardedParameterRule findings for evidenced ignored parameters."""
    return DiscardedParameterRule().evaluate(
        SignatureRuleContext(facts, node, qualname)
    )


def is_protocol_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """
    判断函数是否明显属于 Python 或第三方协议重写入口。

    Args:
        node: 待检查函数。

    Returns:
        特殊方法或带 override/abstractmethod 装饰器时返回 True。
    """
    if node.name.startswith("__") and node.name.endswith("__"):
        return True
    decorators = {dotted_name(item).rsplit(".", 1)[-1] for item in node.decorator_list}
    return bool(decorators & {"override", "abstractmethod"})


def check_boolean_coercion(facts, node, qualname):
    """Detect unvalidated boolean coercion of API parameters.

    Args:
        facts: Source module used to attribute parameter conversions.
        node: Callable declaration and its candidate coercion expressions.
        qualname: Qualified name appearing in QG diagnostics.

    Returns:
        Findings from InternalBooleanCoercionRule for unsafe conversions."""
    return InternalBooleanCoercionRule().evaluate(
        SignatureRuleContext(facts, node, qualname)
    )


def compare_contains_bool_representations(node: ast.Compare) -> bool:
    """
    判断比较表达式是否枚举多种布尔表示。

    Args:
        node: 比较表达式。

    Returns:
        比较值包含文本布尔值或 0/1 时返回 True。
    """
    values: list[object] = []
    for comparator in node.comparators:
        candidates = (
            comparator.elts
            if isinstance(comparator, (ast.Set, ast.List, ast.Tuple))
            else [comparator]
        )
        values.extend(
            item.value for item in candidates if isinstance(item, ast.Constant)
        )
    text_values = {
        value.lower()
        for value in values
        if type(value) is str and value in BOOL_TEXT_VALUES
    }
    int_values = {
        value for value in values if type(value) is int and value in BOOL_INT_VALUES
    }
    return bool(text_values) or int_values == BOOL_INT_VALUES


def check_call_safety_rules(
    facts: ModuleFacts, node: ast.FunctionDef | ast.AsyncFunctionDef, qualname: str
) -> list[Finding]:
    """Inspect direct lexical calls for subprocess, network and execution hazards.

    Bind each direct call to an external API once and record its keyword
    arguments before dispatching unchecked-subprocess, missing-timeout,
    blocking-async and dynamic-execution detectors. Nested callable bodies
    are not automatically attributed to the enclosing callable.

    Args:
        facts: Module facts and bound import identities.
        node: Function whose direct lexical calls are audited.
        qualname: Qualified source symbol owning the calls.

    Returns:
        Findings from call safety rules, ordered by lexical call then detector."""
    findings: list[Finding] = []
    rules = (
        UncheckedSubprocessRule(),
        UntimedNetworkCallRule(),
        BlockingAsyncCallRule(),
        DynamicExecutionRule(),
    )
    for item in direct_body_nodes(node):
        if not isinstance(item, ast.Call):
            continue
        name = call_name(item)
        bound_api = bound_external_api(facts, node, item)
        keyword_names = {keyword.arg for keyword in item.keywords}
        context = CallSafetyRuleContext(
            facts, node, qualname, item, name, bound_api, keyword_names
        )
        for detector in rules:
            findings.extend(detector.evaluate(context))
    return findings


def is_boolean_call_literal(node: ast.expr) -> bool:
    """
    判断调用实参是否为布尔值或历史兼容表示。

    Args:
        node: 调用实表达式。

    Returns:
        命中 False/True、0/1 或文本布尔值时返回 True。
    """
    if is_bool_literal(node):
        return True
    value = literal_value(node)
    return (type(value) is int and value in BOOL_INT_VALUES) or (
        type(value) is str and value in BOOL_TEXT_VALUES
    )


@dataclass(frozen=True)
class SignatureRuleContext:
    """Carry typed rule inputs: facts, node, qualname.

    Fields: facts, node, qualname."""

    facts: ModuleFacts
    node: ast.FunctionDef | ast.AsyncFunctionDef
    qualname: str


class VarPositionalSignatureRule(CoreQualityRule[SignatureRuleContext]):
    """Own QG034 detection using pre-existing function signature facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG034"

    def evaluate(self, context: SignatureRuleContext) -> list[Finding]:
        """Evaluate QG034 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, node, qualname = context.facts, context.node, context.qualname
        argument = node.args.vararg
        if argument is not None:
            return [
                facts.make_finding(
                    argument,
                    "QG034",
                    f"`{qualname}` 使用 *{argument.arg} 接受未逐项声明的可变位置参数。",
                    symbol=qualname,
                    severity="warning",
                    confidence="high",
                    suggestion=(
                        "生产接口优先使用固定显式参数；不要用 *args 吸收调用方参数漂移。"
                    ),
                    evidence={"shape": "var_positional", "parameter": argument.arg},
                )
            ]
        if not node.args.kwonlyargs:
            return []
        keyword_names = [argument.arg for argument in node.args.kwonlyargs]
        return [
            facts.make_finding(
                node,
                "QG034",
                f"`{qualname}` 使用 bare `*` 引入 keyword-only 特殊调用契约：{keyword_names}。",
                symbol=qualname,
                severity="warning",
                confidence="high",
                suggestion=(
                    "项目默认优先普通固定显式参数；参数总量过宽由 QG015 独立检查。"
                ),
                evidence={"shape": "bare_star", "keyword_only": keyword_names},
            )
        ]


class OpenKeywordBagRule(CoreQualityRule[SignatureRuleContext]):
    """Own QG042 detection using pre-existing function signature facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG042"

    def evaluate(self, context: SignatureRuleContext) -> list[Finding]:
        """Evaluate QG042 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, node, qualname = context.facts, context.node, context.qualname
        argument = node.args.kwarg
        if argument is None:
            return []
        annotation_keys = _typed_dict_contract_keys(facts, argument.annotation)
        comment_keys = _structured_kwargs_contract_keys(facts, node)
        contract_source = "typed-dict" if annotation_keys is not None else ""
        keys = annotation_keys
        if keys is None and comment_keys is not None:
            keys = comment_keys
            contract_source = "structured-comment"
        forward_only = _kwargs_is_transparent_forwarding(node, argument.arg)
        if keys is not None and forward_only:
            return []
        if keys is None:
            message = f"`{qualname}` 使用开放 **{argument.arg}，允许 key 集无法由静态契约完整枚举。"
            reason = "open-key-space"
        else:
            message = (
                f"`{qualname}` 为 **{argument.arg} 声明了有限 key {list(keys)}，"
                "但当前函数并非纯透明透传。"
            )
            reason = "bounded-but-not-forward-only"
        return [
            facts.make_finding(
                argument,
                "QG042",
                message,
                symbol=qualname,
                severity="warning",
                confidence="high",
                suggestion=(
                    "优先改为固定显式参数；确需跨多层透传大量稳定参数时，使用 "
                    "Unpack[TypedDict] 或结构化 RQG-KWARGS 有限 key 契约，并保持 kwargs 原样透传。"
                ),
                evidence={
                    "shape": "var_keyword",
                    "parameter": argument.arg,
                    "contract_source": contract_source or "none",
                    "allowed_keys": list(keys or ()),
                    "forward_only": forward_only,
                    "reason": reason,
                },
            )
        ]


class DiscardedParameterRule(CoreQualityRule[SignatureRuleContext]):
    """Own QG145 detection using pre-existing function signature facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG145"

    def evaluate(self, context: SignatureRuleContext) -> list[Finding]:
        """Evaluate QG145 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, node, qualname = context.facts, context.node, context.qualname
        parameters = {argument.arg for argument in all_parameters(node)}
        findings: list[Finding] = []
        for statement in node.body:
            if not isinstance(statement, ast.Delete):
                continue
            deleted = sorted(
                target.id
                for target in statement.targets
                if isinstance(target, ast.Name) and target.id in parameters
            )
            if not deleted:
                continue
            findings.append(
                facts.make_finding(
                    statement,
                    "QG145",
                    f"`{qualname}` 通过 del 丢弃接口参数：{deleted}。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="删除无效参数并同步调用方；若由外部协议强制要求，明确标注 override 并解释。",
                    evidence={"parameters": deleted},
                )
            )
        return findings


class InternalBooleanCoercionRule(CoreQualityRule[SignatureRuleContext]):
    """Own QG033 detection using pre-existing function signature facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG033"

    def evaluate(self, context: SignatureRuleContext) -> list[Finding]:
        """Evaluate QG033 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, node, qualname = context.facts, context.node, context.qualname
        parameters = {argument.arg for argument in all_parameters(node)}
        bool_parameters = {
            argument.arg
            for argument in all_parameters(node)
            if "bool" in annotation_names(argument.annotation)
        }
        findings: list[Finding] = []
        for item in direct_body_nodes(node):
            if isinstance(item, ast.Call) and call_name(item) == "bool" and item.args:
                target = dotted_name(item.args[0])
                if target in parameters:
                    findings.append(
                        facts.make_finding(
                            item,
                            "QG033",
                            f"内部函数对参数 `{target}` 调用 bool()；字符串 'False' 仍会得到 True。",
                            symbol=qualname,
                            severity="warning",
                            suggestion="边界层显式解析外部布尔表示，内部只接收 bool。",
                        )
                    )
            if (
                isinstance(item, ast.Compare)
                and dotted_name(item.left) in bool_parameters
                and compare_contains_bool_representations(item)
            ):
                findings.append(
                    facts.make_finding(
                        item,
                        "QG033",
                        "内部函数同时识别 0/1 或 True/False 字符串等多种布尔表示。",
                        symbol=qualname,
                        severity="warning",
                        suggestion="把多表示解析集中到外部输入边界，内部调用链统一使用 bool。",
                    )
                )
        return findings


@dataclass(frozen=True)
class ParameterDefaultRuleContext:
    """Carry typed rule inputs: facts, argument, default, qualname, names.

    Fields: facts, argument, default, qualname, names, value, bool_like."""

    facts: ModuleFacts
    argument: ast.arg
    default: ast.expr | None
    qualname: str
    names: set[str]
    value: object
    bool_like: bool


@dataclass(frozen=True)
class CallSafetyRuleContext:
    """Carry typed rule inputs: facts, node, qualname, item, name.

    Fields: facts, node, qualname, item, name, bound_api, keyword_names."""

    facts: ModuleFacts
    node: ast.FunctionDef | ast.AsyncFunctionDef
    qualname: str
    item: ast.Call
    name: str
    bound_api: str | None
    keyword_names: set[str | None]


class BoolDefaultValueRule(CoreQualityRule[ParameterDefaultRuleContext]):
    """Own the production QG031 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG031"

    def evaluate(self, context: ParameterDefaultRuleContext) -> list[Finding]:
        """Evaluate QG031 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, argument, _default, qualname = (
            context.facts,
            context.argument,
            context.default,
            context.qualname,
        )
        _names, value, bool_like = context.names, context.value, context.bool_like
        findings: list[Finding] = []
        if bool_like and (
            (type(value) is str and value in BOOL_TEXT_VALUES)
            or (type(value) is int and value in BOOL_INT_VALUES)
        ):
            findings.append(
                facts.make_finding(
                    argument,
                    "QG031",
                    f"布尔参数 `{argument.arg}` 使用非 bool 默认值 {value!r}。",
                    symbol=qualname,
                    severity="error",
                    suggestion="内部只使用 False/True；0/1 和文本转换放在输入边界。",
                    evidence={"parameter": argument.arg, "default": value},
                )
            )
        return findings


class MixedBooleanAnnotationRule(CoreQualityRule[ParameterDefaultRuleContext]):
    """Own the production QG032 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG032"

    def evaluate(self, context: ParameterDefaultRuleContext) -> list[Finding]:
        """Evaluate QG032 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, argument, _default, qualname = (
            context.facts,
            context.argument,
            context.default,
            context.qualname,
        )
        names, _value = context.names, context.value
        findings: list[Finding] = []
        if "bool" in names and names & {"str", "int"}:
            findings.append(
                facts.make_finding(
                    argument,
                    "QG032",
                    f"布尔参数 `{argument.arg}` 同时允许 {sorted(names & {'str', 'int'})}。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="内部签名固定为 bool；多种外部表示只解析一次。",
                    evidence={"parameter": argument.arg, "annotations": sorted(names)},
                )
            )
        return findings


class MutableParameterDefaultRule(CoreQualityRule[ParameterDefaultRuleContext]):
    """Own the production QG039 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG039"

    def evaluate(self, context: ParameterDefaultRuleContext) -> list[Finding]:
        """Evaluate QG039 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, argument, default, qualname = (
            context.facts,
            context.argument,
            context.default,
            context.qualname,
        )
        findings: list[Finding] = []
        if default is not None and isinstance(default, (ast.List, ast.Dict, ast.Set)):
            findings.append(
                facts.make_finding(
                    default,
                    "QG039",
                    f"参数 `{argument.arg}` 使用可变默认值。",
                    symbol=qualname,
                    severity="error",
                    suggestion="使用 None 后显式创建，或使用不可变默认值。",
                    evidence={"parameter": argument.arg},
                )
            )
        return findings


class NullableParameterDefaultRule(CoreQualityRule[ParameterDefaultRuleContext]):
    """Own the production QG040 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG040"

    def evaluate(self, context: ParameterDefaultRuleContext) -> list[Finding]:
        """Evaluate QG040 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, argument, default, qualname = (
            context.facts,
            context.argument,
            context.default,
            context.qualname,
        )
        findings: list[Finding] = []
        if (
            isinstance(default, ast.Constant)
            and default.value is None
            and argument.annotation is not None
            and not annotation_allows_none(argument.annotation)
        ):
            findings.append(
                facts.make_finding(
                    argument,
                    "QG040",
                    f"参数 `{argument.arg}` 默认 None，但类型标注未声明可空。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="必填参数取消默认值；真正可空时显式标注 None。",
                    evidence={"parameter": argument.arg},
                )
            )
        return findings


class UncheckedSubprocessRule(CoreQualityRule[CallSafetyRuleContext]):
    """Own the production QG053 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG053"

    def evaluate(self, context: CallSafetyRuleContext) -> list[Finding]:
        """Evaluate QG053 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, _node, qualname, item = (
            context.facts,
            context.node,
            context.qualname,
            context.item,
        )
        _name, bound_api, keyword_names = (
            context.name,
            context.bound_api,
            context.keyword_names,
        )
        findings: list[Finding] = []
        if (
            bound_api == "subprocess.run"
            and "check" not in keyword_names
            and None not in keyword_names
        ):
            findings.append(
                facts.make_finding(
                    item,
                    "QG053",
                    "subprocess.run() 未显式设置 check=True。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="检查返回码或使用 check=True，禁止把命令失败伪装成成功。",
                )
            )
        return findings


class UntimedNetworkCallRule(CoreQualityRule[CallSafetyRuleContext]):
    """Own the production QG054 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG054"

    def evaluate(self, context: CallSafetyRuleContext) -> list[Finding]:
        """Evaluate QG054 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, _node, qualname, item = (
            context.facts,
            context.node,
            context.qualname,
            context.item,
        )
        name, bound_api, keyword_names = (
            context.name,
            context.bound_api,
            context.keyword_names,
        )
        findings: list[Finding] = []
        if (
            bound_api in NETWORK_APIS
            and "timeout" not in keyword_names
            and None not in keyword_names
        ):
            findings.append(
                facts.make_finding(
                    item,
                    "QG054",
                    f"网络调用 `{name}` 未显式设置 timeout。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="网络边界必须设置明确超时并传播失败。",
                )
            )
        return findings


class BlockingAsyncCallRule(CoreQualityRule[CallSafetyRuleContext]):
    """Own the production QG055 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG055"

    def evaluate(self, context: CallSafetyRuleContext) -> list[Finding]:
        """Evaluate QG055 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, node, qualname, item = (
            context.facts,
            context.node,
            context.qualname,
            context.item,
        )
        name, bound_api, _keyword_names = (
            context.name,
            context.bound_api,
            context.keyword_names,
        )
        findings: list[Finding] = []
        if isinstance(node, ast.AsyncFunctionDef) and bound_api in BLOCKING_APIS:
            findings.append(
                facts.make_finding(
                    item,
                    "QG055",
                    f"异步函数中直接调用同步阻塞 API `{name}`。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="使用异步客户端，或把阻塞操作显式移入线程/进程执行器。",
                )
            )
        return findings


class DynamicExecutionRule(CoreQualityRule[CallSafetyRuleContext]):
    """Own the production QG056 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG056"

    def evaluate(self, context: CallSafetyRuleContext) -> list[Finding]:
        """Evaluate QG056 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, _node, qualname, item = (
            context.facts,
            context.node,
            context.qualname,
            context.item,
        )
        name, _bound_api, _keyword_names = (
            context.name,
            context.bound_api,
            context.keyword_names,
        )
        findings: list[Finding] = []
        if name in {"eval", "exec", "compile"}:
            findings.append(
                facts.make_finding(
                    item,
                    "QG056",
                    f"使用动态代码执行 `{name}()`。",
                    symbol=qualname,
                    severity="error",
                    suggestion="使用显式解析器、分发表或受限 DSL，不要执行动态 Python。",
                )
            )
        return findings
