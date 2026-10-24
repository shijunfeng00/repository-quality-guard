"""Independent call-contract Core rules consuming one pre-collected AST fact context.

No rule traverses the repository or rebuilds topology. All context evidence comes
from the single FactsCollector AST visit and its upstream ownership resolution.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .core_rule_contract import CoreQualityRule
from .config import is_test_path
from .model import Finding, Confidence, Severity
from .topology_facts import ContractOwnership

if TYPE_CHECKING:
    from .facts import ModuleFacts
    from .tool_return_graph_rules import ToolResultBinding


@dataclass(frozen=True)
class SourceRuleContext:
    """Shared source location and lexical scope for visitor-owned structural rules.

    Fields: facts, node, scope."""

    facts: ModuleFacts
    node: ast.AST
    scope: str

    def finding(
        self,
        code: str,
        message: str,
        *,
        severity: Severity = "warning",
        confidence: Confidence = "high",
        suggestion: str = "",
        evidence: dict[str, object] | None = None,
    ) -> Finding:
        """Emit one source-positioned Finding through the owning ModuleFacts collector.

        Args:
            code: Registered QG identifier of the reporting rule.
            message: Source-bound explanation of the observed defect.
            severity: Gate category to assign to the concrete observation.
            confidence: Confidence of the supplied source and ownership evidence.
            suggestion: Actionable remediation without guessing source types.
            evidence: Optional structured provenance from the actual call or binding.

        Returns:
            Finding with the current AST position, lexical scope and module path;
            the rule evaluator does not create a second source location.
        """
        return self.facts.make_finding(
            self.node,
            code,
            message,
            self.scope,
            severity=severity,
            confidence=confidence,
            suggestion=suggestion,
            evidence=evidence,
        )


@dataclass(frozen=True)
class CallRuleContext(SourceRuleContext):
    """One call and resolved owner, with exact original symbol scope and provenance.

    Fields: node, full_name, receiver_name, confirmed_mapping, strict_get, is_aggregation, ownership, ownership_confidence."""

    node: ast.Call
    full_name: str = ""
    receiver_name: str = ""
    confirmed_mapping: bool = False
    strict_get: bool = False
    is_aggregation: bool = False
    ownership: ContractOwnership = ContractOwnership.UNKNOWN
    ownership_confidence: Confidence = "high"
    ownership_evidence: tuple[str, ...] = ()
    declared_empty: bool = False
    required_schema_key: bool = False
    optional_schema_key: bool = False
    established_key_guard: bool = False
    selector: str = ""
    statically_typed: bool = False

    def mapping_evidence(self) -> dict[str, object]:
        """Describe the owner and key-requiredness evidence for a Mapping.get call.

        Returns:
           Receiver/selector identity, explicit-default fact, mapping confidence,
           and provenance of internal/external contract ownership. UNKNOWN adds a
           Q4/Q6 semantic-review obligation instead of asserting an invalid read.
        """
        result: dict[str, object] = {
            "requiredness": (
                "OPTIONAL"
                if self.optional_schema_key
                else "REQUIRED"
                if self.required_schema_key
                else "UNKNOWN"
            ),
            "receiver": self.receiver_name,
            "selector": self.selector,
            "explicit_default": len(self.node.args) >= 2,
            "confirmed_mapping": self.confirmed_mapping,
            "contract_ownership": self.ownership.value,
            "ownership_evidence": list(self.ownership_evidence),
            "declared_empty_return": self.declared_empty,
        }
        if self.ownership is ContractOwnership.UNKNOWN:
            result.update(
                {
                    "semantic_review_required": True,
                    "semantic_review_question": "Q4,Q6",
                    "semantic_review_kind": "contract-ownership",
                }
            )
        return result

    def dynamic_evidence(self) -> dict[str, object]:
        """Record call receiver ownership for dynamic-looking method use.

        Returns:
            Receiver, selector, static type confidence, test-path status and owner
            provenance; unknown ownership carries its unresolved review obligation.
        """
        result: dict[str, object] = {
            "receiver": self.receiver_name,
            "selector": self.selector,
            "statically_typed": self.statically_typed,
            "test_context": is_test_path(self.facts.path),
            "contract_ownership": self.ownership.value,
            "ownership_evidence": list(self.ownership_evidence),
        }
        if self.ownership is ContractOwnership.UNKNOWN:
            result.update(
                {
                    "semantic_review_required": True,
                    "semantic_review_question": "Q4,Q6",
                    "semantic_review_kind": "contract-ownership",
                }
            )
        return result


class MissingMappingKeyRule(CoreQualityRule[CallRuleContext]):
    """QG003: a proven Mapping.get missing its required key or a defaultless contract read.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG003"

    def evaluate_tool_bindings(
        self, bindings: tuple[ToolResultBinding, ...]
    ) -> list[Finding]:
        """Classify graph-proven Tool reads under the sole QG003 owner."""
        from .tool_return_graph_rules import required_tool_result_get_findings

        return required_tool_result_get_findings(bindings, self.code)

    def evaluate(self, context: CallRuleContext) -> list[Finding]:
        """Evaluate QG003 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.full_name != "get":
            return []
        if (
            not context.node.args
            and not context.node.keywords
            and context.confirmed_mapping
        ):
            return [
                context.finding(
                    self.code,
                    "已确认 Mapping.get() 调用缺少必需的 key 参数。",
                    severity="error",
                    confidence="high",
                    suggestion="按真实映射契约传入 key；若是自定义方法，请先证明实际 owner。",
                    evidence={"receiver": context.receiver_name, "arity": 0},
                )
            ]
        if len(context.node.args) >= 2:
            return []
        return MappingAccessRule.normal_finding(context, self.code)


class MappingAccessRule(CoreQualityRule[CallRuleContext]):
    """QG004: explicit default for a mapping read under the resolved contract owner.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG004"

    @staticmethod
    def normal_finding(context: CallRuleContext, code: str) -> list[Finding]:
        """Report only a positive required-key contract contradicted by fallback.

        Unknown requiredness is not proven optionality and not evidence of a
        defect. A source-level resolution failure is tested as an analyzer bug,
        never hidden in the user's semantic review queue.
        """
        if (
            context.ownership is not ContractOwnership.INTERNAL_FORMAL
            or not context.required_schema_key
            or context.optional_schema_key
            or context.established_key_guard
        ):
            return []
        return [
            context.finding(
                code,
                "已证明内部映射 Schema 的字段为必需，读取却使用 .get() 进行兜底。",
                severity="warning",
                confidence=context.ownership_confidence,
                suggestion="既有必需字段应直接索引；如实际可缺失，应修正正式 Schema 契约。",
                evidence=context.mapping_evidence(),
            )
        ]

    def evaluate(self, context: CallRuleContext) -> list[Finding]:
        """Evaluate QG004 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.full_name != "get" or len(context.node.args) < 2:
            return []
        return self.normal_finding(context, self.code)


class UnknownMappingOwnerRule(CoreQualityRule[CallRuleContext]):
    """QG026: unresolved mapping ownership is preserved rather than guessed.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG026"

    def evaluate(self, context: CallRuleContext) -> list[Finding]:
        """Evaluate QG026 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if (
            context.full_name != "get"
            or context.ownership is not ContractOwnership.UNKNOWN
        ):
            return []
        if (
            not context.node.args
            and not context.node.keywords
            and context.confirmed_mapping
        ):
            return []
        return [
            context.finding(
                self.code,
                "发现无法静态确认 owner 的 .get() 契约访问。",
                severity="warning" if is_test_path(context.facts.path) else "info",
                confidence=context.ownership_confidence,
                suggestion=(
                    "先确认 receiver 是内部正式 mapping、外部可选 API 还是动态边界；"
                    "证据不足时保持 unknown，不得仅凭变量名或 strict_get 猜成内部 schema。"
                ),
                evidence=context.mapping_evidence(),
            )
        ]


class MappingSetdefaultRule(CoreQualityRule[CallRuleContext]):
    """QG012: implicit read/write/default state fusion.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG012"

    def evaluate(self, context: CallRuleContext) -> list[Finding]:
        """Evaluate QG012 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.full_name != "setdefault" or context.is_aggregation:
            return []
        if not (context.confirmed_mapping or context.strict_get):
            return []
        return [
            context.finding(
                self.code,
                "setdefault() 同时承担读取、兜底和写入，容易隐藏状态归并副作用。",
                severity="warning" if context.confirmed_mapping else "info",
                suggestion=(
                    "不要把 setdefault() 机械展开成 `if key not in mapping: mapping[key] = default`。"
                    "若缺键是合法的聚合/缓存语义，保留 setdefault 或封装到唯一状态入口；"
                    "若字段按契约必需，直接索引并让缺失错误暴露。"
                ),
                confidence="high" if context.confirmed_mapping else "medium",
                evidence={"requiredness": "UNKNOWN", "receiver": context.receiver_name},
            )
        ]


class MappingPopDefaultRule(CoreQualityRule[CallRuleContext]):
    """QG025: implicit missing-key suppression while deleting a field.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG025"

    def evaluate(self, context: CallRuleContext) -> list[Finding]:
        """Evaluate QG025 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.full_name != "pop" or len(context.node.args) < 2:
            return []
        if not (context.confirmed_mapping or context.strict_get):
            return []
        return [
            context.finding(
                self.code,
                "映射 pop(key, default) 在删除字段时静默忽略缺失。",
                severity="warning" if context.confirmed_mapping else "info",
                confidence="high" if context.confirmed_mapping else "medium",
                suggestion="若字段按契约必须存在，使用 pop(key)；真正可选字段必须在类型中显式声明。",
                evidence={"receiver": context.receiver_name, "requiredness": "UNKNOWN"},
            )
        ]


class HasattrContractRule(CoreQualityRule[CallRuleContext]):
    """QG005: static contract downgraded to a runtime attribute-existence probe.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG005"

    def evaluate(self, context: CallRuleContext) -> list[Finding]:
        """Evaluate QG005 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.full_name != "hasattr":
            return []
        internal = context.ownership is ContractOwnership.INTERNAL_FORMAL
        severity: Severity = (
            "warning"
            if is_test_path(context.facts.path)
            else "error"
            if internal
            else "info"
        )
        return [
            context.finding(
                self.code,
                (
                    "使用 hasattr() 探测内部正式对象字段，把明确类型契约降级为运行时猜测。"
                    if internal
                    else "hasattr() 探测位于外部/动态/未解析契约边界，保留所有权证据供复核。"
                ),
                severity=severity,
                confidence=context.ownership_confidence,
                suggestion=(
                    "内部正式对象应直接访问声明属性；多态使用 Protocol/ABC/联合类型或显式 adapter。"
                    "外部可选能力只有在依赖契约静态可证明时才可保留，unknown 不得反推为内部错误。"
                ),
                evidence=context.dynamic_evidence(),
            )
        ]


class GetattrDefaultContractRule(CoreQualityRule[CallRuleContext]):
    """QG006: defaulted attribute lookup suppresses a resolved contract failure.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG006"

    def evaluate(self, context: CallRuleContext) -> list[Finding]:
        """Evaluate QG006 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.full_name != "getattr" or len(context.node.args) < 3:
            return []
        internal = context.ownership is ContractOwnership.INTERNAL_FORMAL
        severity: Severity = (
            "warning"
            if is_test_path(context.facts.path)
            else "error"
            if internal
            else "info"
        )
        return [
            context.finding(
                self.code,
                (
                    "getattr(..., default) 在内部正式对象契约缺失时静默兜底。"
                    if internal
                    else "getattr(..., default) 位于外部/动态/未解析契约边界，保留所有权证据供复核。"
                ),
                severity=severity,
                confidence=context.ownership_confidence,
                suggestion=(
                    "内部正式对象应直接访问声明字段并让契约错误暴露；真正可选的外部字段必须由"
                    "静态依赖契约、Protocol/联合类型或唯一 adapter 明确表达。"
                ),
                evidence=context.dynamic_evidence(),
            )
        ]


class SuppressedExceptionRule(CoreQualityRule[CallRuleContext]):
    """QG024: explicit suppression of an exception class.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG024"

    def evaluate(self, context: CallRuleContext) -> list[Finding]:
        """Evaluate QG024 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.full_name != "contextlib.suppress":
            return []
        return [
            context.finding(
                self.code,
                "contextlib.suppress() 会完全隐藏指定异常。",
                severity="warning",
                confidence="high",
                suggestion="确认异常确实可安全忽略；否则记录上下文或使用显式异常分支。",
            )
        ]


@dataclass(frozen=True)
class ExceptionRuleContext(SourceRuleContext):
    """One exception handler with proven syntactic exception classes and effects.

    Fields: exception_names, is_swallowed, has_fallback_import."""

    exception_names: frozenset[str]
    is_swallowed: bool
    has_fallback_import: bool


def _unshadowed_source_name(tree: ast.AST, name: str) -> bool:
    """Reject a purported builtin/module if the source rebinds its name."""
    for item in ast.walk(tree):
        if (
            isinstance(item, ast.Name)
            and item.id == name
            and isinstance(item.ctx, (ast.Store, ast.Del))
        ):
            return False
        if isinstance(item, ast.arg) and item.arg == name:
            return False
        if (
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and item.name == name
        ):
            return False
        if isinstance(item, ast.ExceptHandler) and item.name == name:
            return False
        if isinstance(item, ast.ImportFrom):
            if any((alias.asname or alias.name) == name for alias in item.names):
                return False
        if isinstance(item, ast.Import):
            if any(
                (alias.asname or alias.name.split(".")[0]) == name
                and (alias.name != name or alias.asname is not None)
                for alias in item.names
            ):
                return False
    return True


def _contains_executed_exception_reference(node: ast.AST, caught: str) -> bool:
    """Require a captured exception reference evaluated when the call occurs."""
    if isinstance(
        node, (ast.Lambda, ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)
    ):
        return False
    return (isinstance(node, ast.Name) and node.id == caught) or any(
        _contains_executed_exception_reference(child, caught)
        for child in ast.iter_child_nodes(node)
    )


def _reported_caught_exception(
    call: ast.Call, caught_name: str | None, tree: ast.AST
) -> bool:
    """Recognize an executed print of the bound exception, not arbitrary output.

    Only the direct built-in print form is recognized here. Calls through
    unknown logging adapters need ownership evidence before being exempted.
    """
    logging_imported = (
        isinstance(tree, ast.Module)
        and _unshadowed_source_name(tree, "logging")
        and any(
            isinstance(item, ast.Import)
            and any(
                alias.name == "logging" and alias.asname is None for alias in item.names
            )
            for item in tree.body
        )
    )
    if (
        logging_imported
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "logging"
        and call.func.attr == "exception"
    ):
        return True  # logging.exception includes the active exception traceback.
    if (
        logging_imported
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "logging"
        and call.func.attr in {"error", "warning", "critical"}
    ):
        return caught_name is not None and any(
            _contains_executed_exception_reference(value, caught_name)
            for value in (*call.args, *(k.value for k in call.keywords))
        )
    return (
        caught_name is not None
        and _unshadowed_source_name(tree, "print")
        and isinstance(call.func, ast.Name)
        and call.func.id == "print"
        and any(
            _contains_executed_exception_reference(value, caught_name)
            for value in (*call.args, *(k.value for k in call.keywords))
        )
    )


def _nonzero_failure_status(value: ast.expr) -> bool:
    """Accept only literal nonzero integer or nonempty failure text exit codes."""
    return isinstance(value, ast.Constant) and (
        (type(value.value) is int and value.value != 0)
        or (isinstance(value.value, str) and bool(value.value))
    )


def _nonzero_exit(call: ast.Call, tree: ast.AST) -> bool:
    """Accept only explicit, statically non-success exit codes."""
    func = call.func
    is_exit = (
        isinstance(func, ast.Name)
        and func.id == "exit"
        and _unshadowed_source_name(tree, "exit")
    )
    is_sys_exit = (
        isinstance(func, ast.Attribute)
        and func.attr == "exit"
        and isinstance(func.value, ast.Name)
        and func.value.id == "sys"
        and isinstance(tree, ast.Module)
        and _unshadowed_source_name(tree, "sys")
        and any(
            isinstance(item, ast.Import)
            and any(
                alias.name == "sys" and alias.asname is None for alias in item.names
            )
            for item in tree.body
        )
    )
    if not (is_exit or is_sys_exit) or len(call.args) != 1 or call.keywords:
        return False
    return _nonzero_failure_status(call.args[0])


def _explicit_reraise(node: ast.Raise, caught_name: str | None) -> bool:
    """Prove a direct rethrow or a specific newly raised exception class."""
    exc = node.exc
    if exc is None:
        return True
    if isinstance(exc, ast.Name):
        return exc.id == caught_name or exc.id not in {"Exception", "BaseException"}
    if isinstance(exc, ast.Call):
        if isinstance(exc.func, ast.Name) and exc.func.id == "SystemExit":
            return (
                len(exc.args) == 1
                and not exc.keywords
                and _nonzero_failure_status(exc.args[0])
            )
        return isinstance(exc.func, (ast.Name, ast.Attribute)) and (
            (exc.func.id if isinstance(exc.func, ast.Name) else exc.func.attr)
            not in {"Exception", "BaseException"}
        )
    return False


def _handler_preserves_failure(handler: ast.ExceptHandler, tree: ast.AST) -> bool:
    """Prove each reachable handler path rethrows, fails or records the error.

    Unknown branches do not establish exemption; an explicit return terminates
    the current function irrespective of its business-level result.
    """

    def walk_block(statements: list[ast.stmt], observed: bool) -> bool:
        """Prove each handler continuation path observes or propagates failure.

        Args:
            statements: Remaining statements after the current exception edge.
            observed: Whether this path already printed/logged the caught error.

        Returns:
            True if every reachable branch returns, raises, exits with failure,
            or records a diagnostic; otherwise False.
        """
        if not statements:
            return observed
        first, rest = statements[0], statements[1:]
        if isinstance(first, ast.Raise):
            return _explicit_reraise(first, handler.name)
        if isinstance(first, ast.Return):
            return True
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Call):
            if _nonzero_exit(first.value, tree):
                return True
            if (
                isinstance(first.value.func, ast.Name) and first.value.func.id == "exit"
            ) or (
                isinstance(first.value.func, ast.Attribute)
                and first.value.func.attr == "exit"
                and isinstance(first.value.func.value, ast.Name)
                and first.value.func.value.id == "sys"
            ):
                return False
            return walk_block(
                rest,
                observed or _reported_caught_exception(first.value, handler.name, tree),
            )
        if isinstance(first, ast.If):
            return walk_block(first.body + rest, observed) and walk_block(
                first.orelse + rest, observed
            )
        if isinstance(first, (ast.Pass, ast.Assign, ast.AnnAssign, ast.AugAssign)):
            return walk_block(rest, observed)
        return False

    return walk_block(handler.body, False)



def _unconsumed_exception_local(handler: ast.ExceptHandler, tree: ast.AST) -> str | None:
    """Prove an effect-free constant store has no consumer in its function.

    Avoid guessing across nested scopes, explicit external bindings, and dynamic
    namespace inspection. A real load anywhere in the function conservatively
    counts as a consumer, including loads in closures or unreachable branches.
    """
    if len(handler.body) != 1:
        return None
    statement = handler.body[0]
    if (
        not isinstance(statement, ast.Assign)
        or len(statement.targets) != 1
        or not isinstance(statement.targets[0], ast.Name)
        or not isinstance(statement.value, ast.Constant)
    ):
        return None
    name = statement.targets[0].id

    stack: list[tuple[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]] = [
        (tree, None)
    ]
    owner = None
    while stack:
        node, candidate = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidate = node
        elif isinstance(node, (ast.Lambda, ast.ClassDef)):
            candidate = None
        if node is handler:
            owner = candidate
            break
        stack.extend((child, candidate) for child in ast.iter_child_nodes(node))
    if owner is None:
        return None

    reflective_names = {"locals", "vars", "eval", "exec", "globals", "dir"}
    for node in ast.walk(owner):
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return None
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id == name or node.id in reflective_names:
                return None
        if isinstance(node, ast.Attribute) and node.attr in {
            "currentframe", "_getframe", "f_locals", "getouterframes"
        }:
            return None
    return name


class BroadExceptionRule(CoreQualityRule[ExceptionRuleContext]):
    """QG007: require observable exception handling rather than silent catches.

    Broad catches with a definite return/raise, exit, or observable diagnostic
    are exempt. Specific catches report direct pass and proven dead local stores.
    """

    code = "QG007"

    def evaluate(self, context: ExceptionRuleContext) -> list[Finding]:
        """Report proven silent catches without predicting business correctness."""
        broad = context.exception_names & {"", "BaseException", "Exception"}
        handler = context.node
        if not isinstance(handler, ast.ExceptHandler):
            return []
        if not broad:
            pass_only = bool(handler.body) and all(
                isinstance(item, ast.Pass) for item in handler.body
            )
            unused = (
                None
                if pass_only
                else _unconsumed_exception_local(handler, context.facts.tree)
            )
            if not pass_only and unused is None:
                return []
            return [
                context.finding(
                    self.code,
                    (
                        "明确捕获的异常被 pass 完全丢弃。"
                        if pass_only
                        else "异常分支仅写入未被消费的局部常量，未产生可观察的处理效果。"
                    ),
                    severity="warning",
                    evidence={
                        "exception": ", ".join(sorted(context.exception_names)),
                        "swallowed": True,
                        **({"unused_local": unused} if unused is not None else {}),
                    },
                )
            ]
        if _handler_preserves_failure(handler, context.facts.tree):
            return []
        exception_name = next(
            name for name in ("", "BaseException", "Exception") if name in broad
        )
        swallowed = context.is_swallowed
        return [
            context.finding(
                self.code,
                "宽泛异常被捕获"
                + (
                    "并转换为默认结果。"
                    if swallowed
                    else "，但缺少必然执行的错误记录或传播。"
                ),
                severity="error" if swallowed else "warning",
                suggestion=(
                    "明确返回或抛出、以非零状态终止，或记录可观察诊断。"
                    "无副作用的继续执行、条件日志或 exit(0) 不足以豁免。"
                ),
                evidence={
                    "exception": exception_name or "bare except",
                    "swallowed": swallowed,
                },
            )
        ]


class SwallowedContractExceptionRule(CoreQualityRule[ExceptionRuleContext]):
    """QG023: a caught contract exception is converted into a silent default.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG023"

    def evaluate(self, context: ExceptionRuleContext) -> list[Finding]:
        """Evaluate QG023 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        contract_exceptions = context.exception_names & {
            "AttributeError",
            "KeyError",
            "TypeError",
        }
        if not contract_exceptions or not context.is_swallowed:
            return []
        return [
            context.finding(
                self.code,
                "通过捕获契约类异常返回默认结果，可能隐藏字段、属性或类型错误。",
                severity="warning",
                confidence="high",
                suggestion="在输入边界验证结构；内部契约失败应显式暴露，不要转成 None、空容器或常量。",
                evidence={"exceptions": sorted(contract_exceptions)},
            )
        ]


class FallbackImportRule(CoreQualityRule[ExceptionRuleContext]):
    """QG009: module import exceptions select a runtime fallback import.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG009"

    def evaluate(self, context: ExceptionRuleContext) -> list[Finding]:
        """Evaluate QG009 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if (
            not (context.exception_names & {"ImportError", "ModuleNotFoundError"})
            or not context.has_fallback_import
        ):
            return []
        return [
            context.finding(
                self.code,
                "通过 ImportError 选择备用实现，形成运行时兼容分支。",
                severity="info",
                suggestion="确认是否仍需兼容旧依赖；不需要时删除备用路径并固定依赖。",
            )
        ]


@dataclass(frozen=True)
class ProjectionRuleContext(SourceRuleContext):
    """Existing canonical field projection fact; no extra AST walk by QG153.

    Fields: receiver, fields, confidence, is_argument."""

    receiver: str
    fields: list[str]
    confidence: Confidence
    is_argument: bool


class ManualMappingProjectionRule(CoreQualityRule[ProjectionRuleContext]):
    """QG153: hand-written projection of a mapping via repeated get operations.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG153"

    def evaluate(self, context: ProjectionRuleContext) -> list[Finding]:
        """Evaluate QG153 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if not context.receiver or not context.fields:
            return []
        if context.is_argument:
            message = f"从映射 `{context.receiver}` 逐字段 .get() 手写构造新 dict，属于重复字段投影。"
            suggestion = (
                "不要把 metadata/payload 等 dict 字段逐项抄写一遍；若需要完整传递，直接复用原 dict；"
                "若必须裁剪字段，集中声明字段白名单并用索引或专门模型表达契约。"
            )
        else:
            message = f"从映射 `{context.receiver}` 逐字段 .get() 手写构造新 dict，建议核查投影契约。"
            suggestion = "确认字段投影是否为明确的结构变换；只有上游与下游字段契约证实冗余时才能删除。"
        return [
            context.finding(
                self.code,
                message,
                severity="warning",
                confidence=context.confidence,
                suggestion=suggestion,
                evidence={"receiver": context.receiver, "fields": context.fields},
            )
        ]


@dataclass(frozen=True)
class NestedDefinitionRuleContext(SourceRuleContext):
    """Shared lexical nesting facts recorded by the only AST visitor.

    Fields: owner, is_class."""

    owner: str
    is_class: bool


class NestedDefinitionVisibilityRule(CoreQualityRule[NestedDefinitionRuleContext]):
    """QG154: nested definitions obscure the auditable public symbol inventory.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG154"

    def evaluate(self, context: NestedDefinitionRuleContext) -> list[Finding]:
        """Evaluate QG154 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if not context.owner:
            return []
        name = context.node.name
        if context.is_class:
            message = f"类 `{name}` 定义在 `{context.owner}` 内部，会降低接口数量和边界审查的可见性。"
            suggestion = "新增嵌套类仍须在修改说明中全量披露；只有形成稳定共享契约时才提升到模块级。"
            evidence = {"outer_scope": context.owner, "nested_class": name}
        else:
            message = f"函数 `{name}` 定义在函数 `{context.owner}` 内部，会降低接口数量审查的可见性。"
            suggestion = "新增嵌套函数仍须在修改说明中全量披露；仅在形成稳定共享契约时提升到模块级。"
            evidence = {"outer_function": context.owner, "nested_function": name}
        return [
            context.finding(
                self.code,
                message,
                severity="info",
                confidence="high",
                suggestion=suggestion,
                evidence=evidence,
            )
        ]


@dataclass(frozen=True)
class ContractClaimRuleContext(SourceRuleContext):
    """Candidate claim from existing source string classification.

    Fields: claim."""

    claim: str


class UnsupportedCompatibilityClaimRule(CoreQualityRule[ContractClaimRuleContext]):
    """QG158: runtime contract text asserts unevidenced historical compatibility.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG158"

    def evaluate(self, context: ContractClaimRuleContext) -> list[Finding]:
        """Evaluate QG158 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if not context.claim:
            return []
        return [
            context.finding(
                self.code,
                "字符串声称接口、返回结构或行为“保持不变/兼容”，但代码内没有可验证的 Git before/after 基线。",
                severity="warning",
                confidence="high",
                suggestion=(
                    "运行时代码、prompt 和字段描述应直接陈述当前固定契约；"
                    "历史兼容性结论放入修改报告，并基于 diff-base、before/after 和上下游调用证据验证。"
                ),
                evidence={"claim": context.claim[:160]},
            )
        ]


@dataclass(frozen=True)
class MappingFallbackContext(SourceRuleContext):
    """Describe a validated missing-key fallback at one source position.

    Carry the branch mode, receiver, field and confirmed mapping ownership into QG157.
    """

    receiver: str
    field: str
    confirmed_mapping: bool
    mode: str


class GuardedMappingFallbackRule(CoreQualityRule[MappingFallbackContext]):
    """Report a consumer-side fallback that guesses required mapping keys.

    QG157 separates ternary, assignment and exception branches using shared facts.
    """

    code = "QG157"

    def evaluate(self, context: MappingFallbackContext) -> list[Finding]:
        """Check a proven source fallback against the mapping contract.

        Choose severity from the precomputed mapping ownership evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.mode == "expression":
            message = (
                f"通过成员判断后读取 `{context.receiver}[{context.field!r}]` 并回退默认值，"
                "仍在消费方猜测字段契约。"
            )
            suggestion = (
                "不要只替换访问语法来隐藏缺字段；内部必需字段应由生产方和类型契约保证并直接读取。"
                "真正可选字段应在 schema/TypedDict/Pydantic 模型中显式表达，并在输入边界集中处理。"
            )
        elif context.mode == "assignment":
            message = (
                f"分支赋值通过成员判断读取 `{context.receiver}[{context.field!r}]` 并回退默认值，"
                "仍在消费方猜测字段契约。"
            )
            suggestion = (
                "把字段必需性或可选性写入正式 schema，并在唯一输入边界处理；"
                "内部消费方不要重复维护缺字段兼容分支。"
            )
        elif context.mode == "exception":
            message = (
                f"捕获 KeyError 后为 `{context.receiver}[{context.field!r}]` 回退默认值，"
                "仍在消费方猜测字段契约。"
            )
            suggestion = (
                "在输入边界验证或建模字段可选性；内部必需字段缺失应显式失败，"
                "不要用异常分支维持隐式兼容。"
            )
        else:
            raise ValueError("unknown QG157 branch kind")
        return [
            context.finding(
                self.code,
                message,
                severity=(
                    "warning"
                    if is_test_path(context.facts.path)
                    else "critical"
                    if context.confirmed_mapping
                    else "error"
                ),
                confidence="high" if context.confirmed_mapping else "medium",
                suggestion=suggestion,
                evidence={"receiver": context.receiver, "field": context.field},
            )
        ]


@dataclass(frozen=True)
class UnreachableRuleContext(SourceRuleContext):
    """Carry a function or control-flow branch from the single AST visitor.

    QG155 uses these source statements to prove dead suffixes and literal branches.
    """

    kind: str
    statements: tuple[ast.stmt, ...] = ()
    branch: str = ""
    statement_kind: str = ""
    qualname: str = ""
    static_result: bool | None = None


class UnreachableControlFlowRule(CoreQualityRule[UnreachableRuleContext]):
    """Report source regions proven unreachable by static control flow.

    QG155 distinguishes function termination, branch tails, fixed ifs and fixed loops.
    """

    code = "QG155"

    def evaluate(self, context: UnreachableRuleContext) -> list[Finding]:
        """Check one source-level control-flow scope for unreachable code.

        Use the original branch and function bodies to retain exact finding locations.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        node = context.node
        if context.kind in {"block", "function"}:
            terminated = False
            unreachable: ast.stmt | None = None
            for statement in context.statements:
                if terminated:
                    unreachable = statement
                    break
                terminated = isinstance(
                    statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)
                )
            if unreachable is None:
                return []
            if context.kind == "function":
                message = f"函数 `{context.qualname}` 中存在 return/raise/break/continue 后的不可达代码。"
                suggestion = (
                    "删除不可达分支；如果是调试残留或兼容占位，应回到真实调用路径修复。"
                )
                evidence = {"function": context.qualname}
            else:
                message = f"{context.statement_kind} 语句的 {context.branch} 分支中存在终止语句后的不可达代码。"
                suggestion = "删除不可达代码，保留唯一真实执行路径。"
                evidence = {
                    "branch": context.branch,
                    "statement_kind": context.statement_kind,
                }
            return [
                context.facts.make_finding(
                    unreachable,
                    self.code,
                    message,
                    context.scope,
                    severity="error",
                    confidence="high",
                    suggestion=suggestion,
                    evidence=evidence,
                )
            ]
        if context.kind == "if":
            if context.static_result is None or not isinstance(node, ast.If):
                return []
            unreachable_branch = node.orelse if context.static_result else node.body
            location = unreachable_branch[0] if unreachable_branch else node
            branch_name = "else" if context.static_result else "body"
            return [
                context.facts.make_finding(
                    location,
                    self.code,
                    f"if 条件可静态确定为 {context.static_result}，{branch_name} 分支不可达。",
                    context.scope,
                    severity="error",
                    confidence="high",
                    suggestion="删除死分支，只保留真实执行路径。",
                    evidence={
                        "static_result": context.static_result,
                        "unreachable_branch": branch_name,
                    },
                )
            ]
        if context.kind == "while" and isinstance(node, ast.While):
            if context.static_result is False:
                location = node.body[0] if node.body else node
                message = "while 条件可静态确定为 False，循环体永远不可达。"
                suggestion = "删除不会执行的循环体和废弃分支。"
                branch_name = "body"
            elif context.static_result is True and node.orelse:
                location = node.orelse[0]
                message = (
                    "while 条件可静态确定为 True，循环正常结束后的 else 分支不可达。"
                )
                suggestion = (
                    "删除不可达 else 分支；需要退出时使用可变化条件并明确生命周期。"
                )
                branch_name = "orelse"
            else:
                return []
            return [
                context.facts.make_finding(
                    location,
                    self.code,
                    message,
                    context.scope,
                    severity="error",
                    confidence="high",
                    suggestion=suggestion,
                    evidence={
                        "static_result": context.static_result,
                        "unreachable_branch": branch_name,
                    },
                )
            ]
        raise ValueError("unsupported QG155 control-flow context")
