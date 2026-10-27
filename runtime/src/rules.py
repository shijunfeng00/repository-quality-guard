from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .core_rule_contract import CoreQualityRule

from .config import GuardConfig
from .definition_rule_objects import (
    DefinitionRuleContext,
    RepositoryDefinitionContext,
    ModuleRuleContext,
    RepositoryModuleContext,
    SmallLowUseClassRule,
    EphemeralHelperRule,
    OvergrownClassRule,
    FragmentedOwnerRule,
    DuplicateBodyRule,
    UnusedPrivateRule,
    ImportCycleRule,
    OversizedFunctionRule,
    DeepNestingRule,
    HighBranchCountRule,
    WideFunctionInterfaceRule,
    BooleanModeMatrixRule,
    ModuleLineLimitRule,
    SuppressionMarkerRule,
    MissingDocstringRule,
    ShortDocstringRule,
    UndocumentedParametersRule,
    MissingReturnsSectionRule,
)
from .facts import ModuleFacts
from .docstring_inheritance import documented_override_ids
from .model import Definition, Finding
from .topology_facts import RepositoryTopology, UsageEdge, UsageKind, Visibility

BOOLEAN_FLAG_THRESHOLD = 3
DUPLICATE_BODY_MIN_COUNT = 2
DUPLICATE_BODY_MIN_LINES = 6


@dataclass(frozen=True)
class DelegationRuleContext:
    """Represent a classified wrapper and the shared topology evidence.

    Store the same resolved override and call metadata for QG010 and QG185.
    """

    definition: Definition
    private_facade: bool
    property_facade: bool
    critical_facade: bool
    report_wrapper: bool
    usage: dict[str, object]

    def evidence(self) -> dict[str, object]:
        """Serialize resolved delegation provenance for QG010 and QG185 review.

        Returns:
            The wrapper target, observed calls, façade classification, override
            direction, polymorphic slot and graph candidate information.
        """
        return {
            "target": self.definition.wrapper_target,
            "calls": self.definition.calls,
            "private_facade": self.private_facade,
            "property_facade": self.property_facade,
            "incoming_overrides": self.usage["incoming_overrides"],
            "outgoing_overrides": self.usage["outgoing_overrides"],
            "polymorphic_root_slot": self.usage["polymorphic_root_slot"],
            "graph_replacement_candidate": self.usage.get(
                "graph_replacement_candidate", False
            ),
            "graph_target": self.usage.get("graph_target"),
        }


class DelegationWrapperRule(CoreQualityRule[DelegationRuleContext]):
    """Detect a delegation wrapper without independent public behavior.

    Report QG010 only when the wrapper classification is not a private façade.
    """

    code = "QG010"

    def evaluate(self, context: DelegationRuleContext) -> list[Finding]:
        """Evaluate one regular single-hop delegation wrapper.

        Do not report private-facade cases owned by QG185.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if not context.report_wrapper or context.critical_facade:
            return []
        definition = context.definition
        return [
            Finding(
                code=self.code,
                severity=(
                    "critical"
                    if definition.name.startswith("_") and definition.calls <= 1
                    else "warning"
                    if definition.calls <= 1
                    else "info"
                ),
                confidence="high",
                message=(
                    f"公开接口 `{definition.qualname}` 只套壳访问 private 目标 "
                    f"`{definition.wrapper_target}`。"
                    if context.private_facade
                    else f"`{definition.qualname}` 只把参数转发给 `{definition.wrapper_target}`，属于单层委托包装。"
                ),
                suggestion="没有独立契约、校验、日志或语义转换时，删除包装并直接调用目标。",
                evidence=context.evidence(),
                path=str(definition.path),
                line=definition.line,
                column=definition.column,
                symbol=definition.symbol_id,
            )
        ]


class PrivateFacadeWrapperRule(CoreQualityRule[DelegationRuleContext]):
    """Detect a public façade directly exposing private implementation.

    Report QG185 using the same wrapper classification and topology evidence.
    """

    code = "QG185"

    def evaluate(self, context: DelegationRuleContext) -> list[Finding]:
        """Evaluate one critical private-facade wrapper.

        Preserve the same owner and caller facts seen by QG010.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if not context.report_wrapper or not context.critical_facade:
            return []
        definition = context.definition
        return [
            Finding(
                code=self.code,
                severity="critical",
                confidence="high",
                message=(
                    f"公开接口 `{definition.qualname}` 只套壳访问 private 目标 "
                    f"`{definition.wrapper_target}`。"
                    if context.private_facade
                    else f"`{definition.qualname}` 只把参数转发给 `{definition.wrapper_target}`，属于单层委托包装。"
                ),
                suggestion=(
                    "禁止用 public 方法或 @property 给 private 实现再套一层。"
                    "确需跨所有者使用时，将唯一实现正式公开并让调用方直接调用；"
                    "否则保持 private 并由其所有者内部使用。"
                ),
                evidence=context.evidence(),
                path=str(definition.path),
                line=definition.line,
                column=definition.column,
                symbol=definition.symbol_id,
            )
        ]


class RuleEvaluator:
    """
    根据仓库级事实生成结构性质量发现。

    规则只使用静态事实，不直接修改定义或自动消除告警。
    """

    def __init__(
        self, config: GuardConfig, topology: RepositoryTopology | None = None
    ) -> None:
        """
        初始化规则评估器。

        Args:
            config: 质量检查配置。
            topology: 可选的语言中立 owner/usage 拓扑；存在时优先用于复用判断。

        Returns:
            None。
        """
        self.config = config
        self.topology = topology
        self._symbols = (
            {item.symbol_id: item for item in topology.symbols}
            if topology is not None
            else {}
        )
        # Two first-party AST collectors represent nested lexical owners in
        # different orders. Bind solely by an exact source declaration identity
        # when their qualified-string keys disagree; never guess by name alone.
        self._location_symbols: dict[tuple[str, int, str], str | None] = {}
        for item in self._symbols.values():
            key = (item.path.as_posix(), item.line, item.name)
            if key in self._location_symbols:
                self._location_symbols[key] = None
            else:
                self._location_symbols[key] = item.symbol_id
        # Per-evaluation fact indices: shared by QG001, QG013, and QG168.
        # Keep the topology's edge order, since it is observable audit evidence.
        self._incoming_edges: dict[str, list[UsageEdge]] = defaultdict(list)
        self._outgoing_edges: dict[str, list[UsageEdge]] = defaultdict(list)
        if topology is not None:
            for edge in topology.edges:
                self._incoming_edges[edge.target_id].append(edge)
                self._outgoing_edges[edge.source_id].append(edge)
        self._usage_evidence_cache: dict[int, tuple[Definition, dict[str, object]]] = {}
        self._documented_overrides: frozenset[str] = frozenset()

    @property
    def documented_overrides(self) -> frozenset[str]:
        """Expose the exact override exemptions used by this evaluator's QG027 scan."""
        return self._documented_overrides

    def _definition_context(self, definition: Definition) -> DefinitionRuleContext:
        """Bind one definition to already indexed topology and config facts."""
        return DefinitionRuleContext(
            definition,
            self.config,
            self._usage_topology,
            self._is_private_definition(definition),
            definition.symbol_id in self._documented_overrides,
        )

    def _repository_definition_context(
        self, definitions: list[Definition]
    ) -> RepositoryDefinitionContext:
        """Share repository topology across owner-level rules."""
        return RepositoryDefinitionContext(
            definitions, self.config, self.topology, self._usage_topology
        )

    def evaluate(
        self, definitions: list[Definition], facts: list[ModuleFacts]
    ) -> list[Finding]:
        """
        执行全部定义级、模块级与仓库级规则。

        Args:
            definitions: 仓库内已收集的定义列表。
            facts: 当前模块事实或模块事实列表。

        Returns:
            全部自定义规则产生的发现列表。
        """
        self._documented_overrides = documented_override_ids(
            definitions, self.topology, self.config
        )
        findings: list[Finding] = []
        for definition in definitions:
            findings.extend(self._docstring_findings(definition))
            if definition.kind == "class":
                findings.extend(
                    SmallLowUseClassRule().evaluate(
                        self._definition_context(definition)
                    )
                )
                findings.extend(
                    OvergrownClassRule().evaluate(self._definition_context(definition))
                )
            elif definition.kind in {"function", "method"}:
                findings.extend(
                    EphemeralHelperRule().evaluate(self._definition_context(definition))
                )
                ctx = self._definition_context(definition)
                findings.extend(self._wrapper_findings(definition))
                findings.extend(OversizedFunctionRule().evaluate(ctx))
                findings.extend(DeepNestingRule().evaluate(ctx))
                findings.extend(HighBranchCountRule().evaluate(ctx))
                findings.extend(WideFunctionInterfaceRule().evaluate(ctx))
                findings.extend(BooleanModeMatrixRule().evaluate(ctx))
        findings.extend(
            FragmentedOwnerRule().evaluate(
                self._repository_definition_context(definitions)
            )
        )
        for module in facts:
            context = ModuleRuleContext(module, self.config)
            findings.extend(ModuleLineLimitRule().evaluate(context))
            if module.tree is not None:
                findings.extend(SuppressionMarkerRule().evaluate(context))
        findings.extend(
            DuplicateBodyRule().evaluate(
                self._repository_definition_context(definitions)
            )
        )
        findings.extend(
            UnusedPrivateRule().evaluate(
                self._repository_definition_context(definitions)
            )
        )
        findings.extend(ImportCycleRule().evaluate(RepositoryModuleContext(facts)))
        return findings

    def _docstring_findings(self, definition: Definition) -> list[Finding]:
        """
        检查定义的 docstring 覆盖与结构完整性。

        Args:
            definition: 待评估的代码定义。

        Returns:
            当前定义对应的 docstring 问题列表。
        """
        if not self.config.require_docstrings:
            return []
        if any(
            decorator.rsplit(".", 1)[-1]
            in {"property", "cached_property", "setter", "deleter"}
            for decorator in definition.decorators
        ):
            return []
        ctx = self._definition_context(definition)
        findings = (
            MissingDocstringRule().evaluate(ctx)
            if not definition.has_docstring
            else ShortDocstringRule().evaluate(ctx)
        )
        if not definition.has_docstring:
            return findings
        if self._is_private_definition(definition):
            return findings
        ctx = self._definition_context(definition)
        findings.extend(UndocumentedParametersRule().evaluate(ctx))
        findings.extend(MissingReturnsSectionRule().evaluate(ctx))
        return findings

    @staticmethod
    def _is_private_definition(definition: Definition) -> bool:
        """
        判断定义是否属于单下划线内部结构。

        Args:
            definition: 待评估的代码定义。

        Returns:
            单下划线内部名称返回 True，双下划线协议名称仍视为公共契约。
        """
        if str(definition.path).startswith("tests/"):
            return True
        return definition.name.startswith("_") and not (
            definition.name.startswith("__") and definition.name.endswith("__")
        )

    def _usage_topology(self, definition: Definition) -> dict[str, object]:
        """Return normalized usage evidence for one callable definition.

        Args:
            definition: Callable definition being classified.

        Returns:
            Stable direct-call, callback/reference, protocol and visibility evidence.
        """
        cache_key = id(definition)
        if cache_key in self._usage_evidence_cache:
            cached = self._usage_evidence_cache[cache_key]
            if cached[0] is definition:
                return cached[1]
        symbol_id = (
            definition.symbol_id
            if definition.symbol_id in self._symbols
            else self._location_symbols.get(
                (definition.path.as_posix(), definition.line, definition.name)
            )
        )
        if self.topology is None or symbol_id is None:
            direct_callers = definition.calls
            direct_call_sites = definition.calls
            callable_consumers = definition.references
            # Preserve the lexical helper candidate when a normalized Provider
            # is unavailable; mark the evidence as legacy/open-world below.
            # Do not invent a PROTOCOL_HOOK edge that was never observed.
            protocol_edges = 0
            incoming_overrides = 0
            outgoing_overrides = 0
            visibility = (
                Visibility.PUBLIC.value
                if not definition.name.startswith("_")
                else Visibility.INTERNAL.value
            )
            exported = False
            resolution = "legacy"
            owner_id = (
                definition.qualname.rpartition(".")[0]
                if definition.kind == "method"
                else definition.module
            )
            nested = definition.nested
        else:
            symbol = self._symbols[symbol_id]
            incoming_edges = self._incoming_edges[symbol_id]
            outgoing_edges = self._outgoing_edges[symbol_id]
            direct = tuple(
                edge for edge in incoming_edges if edge.kind is UsageKind.DIRECT_CALL
            )
            callable_edges = tuple(
                edge
                for edge in incoming_edges
                if edge.kind
                in {UsageKind.CALLABLE_REFERENCE, UsageKind.CALLBACK_REGISTRATION}
            )
            protocol = tuple(
                edge
                for edge in outgoing_edges
                if edge.kind in {UsageKind.PROTOCOL_HOOK, UsageKind.OVERRIDE}
            )
            direct_callers = len({edge.source_id for edge in direct})
            direct_call_sites = len(direct)
            callable_consumers = len({edge.source_id for edge in callable_edges})
            protocol_edges = len(protocol)
            incoming_overrides = sum(
                edge.kind is UsageKind.OVERRIDE for edge in incoming_edges
            )
            outgoing_overrides = sum(
                edge.kind is UsageKind.OVERRIDE for edge in outgoing_edges
            )
            visibility = symbol.visibility.value
            exported = symbol.exported
            resolution = "normalized"
            owner_id = symbol.owner_id
            nested = symbol.nested
        evidence = {
            "direct_callers": direct_callers,
            "direct_call_sites": direct_call_sites,
            "callable_consumers": callable_consumers,
            "protocol_edges": protocol_edges,
            "incoming_overrides": incoming_overrides,
            "outgoing_overrides": outgoing_overrides,
            "polymorphic_root_slot": incoming_overrides > 0 and outgoing_overrides == 0,
            "visibility": visibility,
            "exported": exported,
            "owner_id": owner_id,
            "nested": nested,
            "topology_resolution": resolution,
        }
        self._usage_evidence_cache[cache_key] = (definition, evidence)
        return evidence

    def _wrapper_contract(
        self, definition: Definition
    ) -> tuple[bool, bool, bool, bool, dict[str, object]]:
        """裁决薄包装的公开/private/protocol 边界及是否需报告。

        Args:
            definition: 包含 wrapper target、装饰器和拓扑事实的定义。

        Returns:
            private facade、property facade、critical facade、报告条件和使用证据。
        """
        target_leaf = definition.wrapper_target.rsplit(".", 1)[-1]
        private_facade = (
            bool(definition.wrapper_target)
            and not definition.name.startswith("_")
            and target_leaf.startswith("_")
            and not target_leaf.startswith("__")
        )
        property_facade = any(
            decorator.rsplit(".", 1)[-1]
            in {"property", "cached_property", "setter", "deleter"}
            for decorator in definition.decorators
        )
        usage = self._usage_topology(definition)
        # A subclass override is an implementation relation, not automatically
        # a framework entry point. Root slots with incoming overrides are
        # protected separately; a redundant override remains reviewable.
        protocol_hooks = int(usage["protocol_edges"]) - int(usage["outgoing_overrides"])
        external_edges = bool(
            definition.externally_invoked
            or protocol_hooks
            or usage["callable_consumers"]
        )
        critical_facade = private_facade and not property_facade and not external_edges
        # QG010 demands a graph-certified removable *function layer*: the
        # wrapper and its callee must each be uniquely bound to real authored
        # symbols, with observed callers and no open-world ownership evidence.
        symbol_id = definition.symbol_id
        calls = [
            edge
            for edge in self._outgoing_edges[symbol_id]
            if edge.kind is UsageKind.DIRECT_CALL
        ]
        unique_target = len({edge.target_id for edge in calls}) == 1
        known_target = unique_target and calls[0].target_id in self._symbols
        graph_replacement = (
            usage["topology_resolution"] == "normalized"
            and known_target
            and int(usage["direct_call_sites"]) > 0
            and not usage["exported"]
            and not usage["callable_consumers"]
            and not usage["protocol_edges"]
            and not usage["incoming_overrides"]
            and not usage["outgoing_overrides"]
            and not definition.externally_invoked
        )
        # Public functions may be consumed outside the observed repository;
        # without an authoritative closed API contract keep that as UNKNOWN.
        internal_layer = definition.name.startswith("_") or bool(usage["nested"])
        report_wrapper = (
            definition.wrapper_target
            and not property_facade
            and (
                critical_facade
                or (
                    graph_replacement
                    and internal_layer
                    and int(usage["direct_call_sites"]) > 0
                    and not external_edges
                )
            )
        )
        usage = {
            **usage,
            "graph_replacement_candidate": bool(graph_replacement),
            "graph_target": calls[0].target_id if known_target else None,
            "closed_visibility": bool(internal_layer),
        }
        return (
            private_facade,
            property_facade,
            critical_facade,
            bool(report_wrapper),
            usage,
        )

    def _wrapper_findings(self, definition: Definition) -> list[Finding]:
        """根据包装边界契约生成委托与 private 套壳发现。

        Args:
            definition: 待评估的函数定义。

        Returns:
            包装契约发现，保留调用次数和边界裁决证据。
        """
        private_facade, property_facade, critical_facade, report_wrapper, usage = (
            self._wrapper_contract(definition)
        )
        context = DelegationRuleContext(
            definition,
            private_facade,
            property_facade,
            critical_facade,
            report_wrapper,
            usage,
        )
        if critical_facade:
            return PrivateFacadeWrapperRule().evaluate(context)
        return DelegationWrapperRule().evaluate(context)
