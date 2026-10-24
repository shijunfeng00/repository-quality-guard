"""Owner-specific structural rules over shared definitions, modules and topology.

Every class owns Finding construction. The scanner and RuleEvaluator supply facts
once and retain the original ordering of findings in their public APIs.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from io import StringIO
import tokenize

from .config import GuardConfig, is_test_path
from .core_rule_contract import CoreQualityRule
from .facts import ModuleFacts
from .graph_utils import strongly_connected_components
from .model import Definition, Finding
from .topology_facts import RepositoryTopology, Visibility
from .topology_policy import HelperUsageEvidence, ephemeral_helper_candidate

BOOLEAN_FLAG_THRESHOLD = 3
DUPLICATE_BODY_MIN_COUNT = 2
DUPLICATE_BODY_MIN_LINES = 6


@dataclass(frozen=True)
class DefinitionRuleContext:
    """Shared definition, configurable thresholds and authoritative usage facts.

    Fields: definition, config, usage_lookup, is_private."""

    definition: Definition
    config: GuardConfig
    usage_lookup: Callable[[Definition], dict[str, object]]
    is_private: bool
    inherits_documentation: bool = False


@dataclass(frozen=True)
class RepositoryDefinitionContext:
    """Shared definitions, topology and topology-indexed usage without rescanning.

    Fields: definitions, config, topology, usage_lookup."""

    definitions: list[Definition]
    config: GuardConfig
    topology: RepositoryTopology | None
    usage_lookup: Callable[[Definition], dict[str, object]]


@dataclass(frozen=True)
class ModuleRuleContext:
    """Shared parsed module data and configured module-size thresholds.

    Fields: item, config."""

    item: ModuleFacts
    config: GuardConfig


@dataclass(frozen=True)
class RepositoryModuleContext:
    """Shared module facts for repository-wide import topology analysis.

    Fields: facts."""

    facts: list[ModuleFacts]


class SmallLowUseClassRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG002 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG002"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG002 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if definition.kind != "class":
            return []
        eligible = (
            definition.name not in context.config.ignored_names
            and definition.lines < context.config.short_max_lines
            and definition.calls <= context.config.low_use_max_calls
        )
        if not eligible:
            return []
        return [
            Finding(
                code="QG002",
                severity="info",
                confidence="medium",
                path=str(definition.path),
                line=definition.line,
                column=definition.column,
                message=(
                    f"class `{definition.qualname}` 仅 {definition.lines} 行，"
                    f"静态实例化/调用 {definition.calls} 次。"
                ),
                symbol=definition.symbol_id,
                suggestion=(
                    "确认该类是否拥有独立状态或协议；纯数据容器可考虑 dataclass，"
                    "单次包装可考虑合并。"
                ),
                evidence={
                    "lines": definition.lines,
                    "calls": definition.calls,
                    "references": definition.references,
                    "kind": definition.kind,
                },
            )
        ]


class EphemeralHelperRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG001 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG001"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG001 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        topology_evidence = context.usage_lookup(definition)
        if not ephemeral_helper_candidate(
            definition.kind,
            definition.name,
            definition.lines,
            HelperUsageEvidence(
                direct_callers=int(topology_evidence["direct_callers"]),
                direct_call_sites=int(topology_evidence["direct_call_sites"]),
                callable_consumers=int(topology_evidence["callable_consumers"]),
                protocol_edges=int(topology_evidence["protocol_edges"]),
                visibility=str(topology_evidence["visibility"]),
                exported=bool(topology_evidence["exported"]),
                owner_id=str(topology_evidence["owner_id"]),
                nested=bool(topology_evidence["nested"]),
                topology_resolution=str(topology_evidence["topology_resolution"]),
            ),
            context.config,
            bool(
                definition.externally_invoked
                or topology_evidence["protocol_edges"]
                or topology_evidence["callable_consumers"]
            ),
        ):
            return []
        evidence = {
            "lines": definition.lines,
            "calls": definition.calls,
            "references": definition.references,
            "kind": definition.kind,
            "ephemeral_helper_candidate": True,
            **topology_evidence,
        }
        return [
            Finding(
                code="QG001",
                severity="warning",
                confidence=(
                    "high"
                    if topology_evidence["topology_resolution"] == "normalized"
                    else "medium"
                ),
                path=str(definition.path),
                line=definition.line,
                column=definition.column,
                message=(
                    f"{definition.kind} `{definition.qualname}` 仅 {definition.lines} 行，"
                    f"普通静态调用方 {topology_evidence['direct_callers']} 个 / "
                    f"call sites {topology_evidence['direct_call_sites']} 个，"
                    "且没有 callback/protocol/public API 消费证据。"
                ),
                symbol=definition.symbol_id,
                suggestion=(
                    "若它只是唯一调用方的一次性局部步骤，优先内联；若拥有独立事务、"
                    "资源或生命周期边界，在语义审计中给出该边界证据。"
                ),
                evidence=evidence,
            )
        ]


class OvergrownClassRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG008 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG008"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG008 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if definition.kind != "class":
            return []
        findings: list[Finding] = []
        method_count = definition.direct_method_count
        if method_count > context.config.max_class_methods:
            findings.append(
                Finding(
                    code="QG008",
                    severity="critical",
                    confidence="high",
                    path=str(definition.path),
                    line=definition.line,
                    column=definition.column,
                    message=(
                        f"class `{definition.qualname}` 直接定义 {method_count} 个方法，"
                        f"超过硬上限 {context.config.max_class_methods}。"
                    ),
                    symbol=definition.symbol_id,
                    suggestion=(
                        "识别真正独立的状态、生命周期或领域 owner 后再拆分；"
                        "禁止为了降低方法数制造无状态 wrapper、mixin 或一次性 helper。"
                    ),
                    evidence={
                        "methods": method_count,
                        "review_threshold": context.config.class_method_review_threshold,
                        "hard_limit": context.config.max_class_methods,
                        "public_methods": definition.public_method_count,
                        "tiny_methods": definition.tiny_method_count,
                    },
                )
            )
        elif method_count > context.config.class_method_review_threshold:
            findings.append(
                Finding(
                    code="QG008",
                    severity="info",
                    confidence="medium",
                    path=str(definition.path),
                    line=definition.line,
                    column=definition.column,
                    message=(
                        f"class `{definition.qualname}` 直接定义 {method_count} 个方法，"
                        "进入 owner cohesion 语义复核区间。"
                    ),
                    symbol=definition.symbol_id,
                    suggestion=(
                        "确认这些方法仍围绕同一状态、生命周期或领域对象；"
                        "只有存在独立 owner 时才拆分，禁止为压数字制造碎片化。"
                    ),
                    evidence={
                        "methods": method_count,
                        "review_threshold": context.config.class_method_review_threshold,
                        "hard_limit": context.config.max_class_methods,
                        "public_methods": definition.public_method_count,
                        "tiny_methods": definition.tiny_method_count,
                        "semantic_review_required": True,
                        "semantic_review_question": "Q4,Q9",
                        "semantic_review_kind": "owner-cohesion-size",
                    },
                )
            )
        return findings


class FragmentedOwnerRule(CoreQualityRule[RepositoryDefinitionContext]):
    """Implement QG013 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG013"

    def evaluate(self, context: RepositoryDefinitionContext) -> list[Finding]:
        """Evaluate QG013 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definitions = context.definitions
        if context.topology is None:
            return []
        by_owner: defaultdict[str, list[tuple[Definition, dict[str, object]]]] = (
            defaultdict(list)
        )
        for definition in definitions:
            if definition.kind not in {"function", "method"}:
                continue
            evidence = context.usage_lookup(definition)
            if not evidence["nested"] and evidence["visibility"] not in {
                Visibility.INTERNAL.value,
                Visibility.PRIVATE.value,
            }:
                continue
            by_owner[str(evidence["owner_id"])].append((definition, evidence))

        findings: list[Finding] = []
        for owner_id, owned in sorted(by_owner.items()):
            helpers = [
                definition
                for definition, evidence in owned
                if ephemeral_helper_candidate(
                    definition.kind,
                    definition.name,
                    definition.lines,
                    HelperUsageEvidence(
                        direct_callers=int(evidence["direct_callers"]),
                        direct_call_sites=int(evidence["direct_call_sites"]),
                        callable_consumers=int(evidence["callable_consumers"]),
                        protocol_edges=int(evidence["protocol_edges"]),
                        visibility=str(evidence["visibility"]),
                        exported=bool(evidence["exported"]),
                        owner_id=str(evidence["owner_id"]),
                        nested=bool(evidence["nested"]),
                        topology_resolution=str(evidence["topology_resolution"]),
                    ),
                    context.config,
                    bool(
                        definition.decorators
                        or evidence["protocol_edges"]
                        or evidence["callable_consumers"]
                        or evidence["topology_resolution"] == "legacy"
                    ),
                )
            ]
            helper_count = len(helpers)
            internal_count = len(owned)
            if helper_count < context.config.fragmented_owner_min_helpers:
                continue
            ratio = helper_count / internal_count
            if ratio < context.config.fragmented_owner_ratio:
                continue
            first = min(helpers, key=lambda item: (str(item.path), item.line))
            findings.append(
                Finding(
                    code="QG013",
                    severity="info",
                    confidence="medium",
                    path=str(first.path),
                    line=first.line,
                    column=first.column,
                    message=(
                        f"owner `{owner_id}` 的 {internal_count} 个内部实现函数/方法中，"
                        f"有 {helper_count} 个属于低复用 ephemeral helper 候选。"
                    ),
                    symbol=owner_id,
                    suggestion=(
                        "检查这些 helper 是否只是唯一调用方的流程切片；保留共享 primitive、"
                        "callback/protocol hook 与真实事务/资源/生命周期边界。"
                    ),
                    evidence={
                        "owner": owner_id,
                        "internal_functions": internal_count,
                        "ephemeral_helpers": helper_count,
                        "ephemeral_ratio": round(ratio, 4),
                        "min_helpers": context.config.fragmented_owner_min_helpers,
                        "ratio_threshold": context.config.fragmented_owner_ratio,
                        "helpers": [item.symbol_id for item in helpers],
                        "semantic_review_required": True,
                        "semantic_review_question": "Q4,Q9",
                        "semantic_review_kind": "ephemeral-helper-swarm",
                    },
                )
            )
        return findings


class DuplicateBodyRule(CoreQualityRule[RepositoryDefinitionContext]):
    """Implement QG021 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG021"

    def evaluate(self, context: RepositoryDefinitionContext) -> list[Finding]:
        """Evaluate QG021 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definitions = context.definitions
        groups: dict[str, list[Definition]] = defaultdict(list)
        for definition in definitions:
            if (
                definition.kind in {"function", "method"}
                and definition.lines >= DUPLICATE_BODY_MIN_LINES
            ):
                groups[definition.body_fingerprint].append(definition)
        findings: list[Finding] = []
        for duplicates in groups.values():
            if len(duplicates) < DUPLICATE_BODY_MIN_COUNT:
                continue
            locations = [f"{item.path}:{item.line}" for item in duplicates]
            for definition in duplicates[1:]:
                findings.append(
                    Finding(
                        code="QG021",
                        severity="critical",
                        confidence="high",
                        path=str(definition.path),
                        line=definition.line,
                        column=definition.column,
                        message=f"`{definition.qualname}` 与其他函数具有完全相同的 AST 函数体，疑似重复实现。",
                        symbol=definition.symbol_id,
                        suggestion="优先复用既有函数；若因协议桩代码必须重复，需在修改说明报告中写明正当理由。",
                        evidence={"duplicates": locations},
                    )
                )
        return findings


class UnusedPrivateRule(CoreQualityRule[RepositoryDefinitionContext]):
    """Implement QG156 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG156"

    def evaluate(self, context: RepositoryDefinitionContext) -> list[Finding]:
        """Evaluate QG156 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definitions = context.definitions
        findings: list[Finding] = []
        for definition in definitions:
            private_callable = (
                definition.kind in {"function", "method"}
                and definition.name.startswith("_")
                and not (
                    definition.name.startswith("__") and definition.name.endswith("__")
                )
            )
            if not private_callable:
                continue
            usage = context.usage_lookup(definition)
            if (
                usage["topology_resolution"] != "normalized"
                or definition.decorators
                or usage["callable_consumers"]
                or usage["protocol_edges"]
            ):
                continue
            if definition.calls > 0 or definition.references > 0:
                continue
            findings.append(
                Finding(
                    code="QG156",
                    severity="error",
                    confidence="medium",
                    path=str(definition.path),
                    line=definition.line,
                    column=definition.column,
                    message=f"私有定义 `{definition.qualname}` 没有静态调用或引用，疑似冗余代码。",
                    symbol=definition.symbol_id,
                    suggestion=(
                        "删除未使用私有函数；若由框架反射调用，改用显式注册、装饰器或公开契约让调用关系可审查。"
                    ),
                    evidence={
                        "calls": definition.calls,
                        "references": definition.references,
                        "kind": definition.kind,
                    },
                )
            )
        return findings


class ImportCycleRule(CoreQualityRule[RepositoryModuleContext]):
    """Implement QG022 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG022"

    def evaluate(self, context: RepositoryModuleContext) -> list[Finding]:
        """Evaluate QG022 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts = context.facts
        modules = {item.module for item in facts if item.module}
        facts_by_module = {item.module: item for item in facts if item.module}
        graph: dict[str, set[str]] = {module: set() for module in modules}
        for item in facts:
            if not item.module:
                continue
            for imported in item.imports.values():
                matches = [
                    module
                    for module in modules
                    if imported == module or imported.startswith(module + ".")
                ]
                if matches:
                    target = max(matches, key=len)
                    if target != item.module:
                        graph[item.module].add(target)
        components = strongly_connected_components(graph)
        findings: list[Finding] = []
        for component in sorted(components, key=sorted):
            if len(component) == 1:
                module = next(iter(component))
                if module not in graph[module]:
                    continue
            ordered = sorted(component)
            first = facts_by_module[ordered[0]]
            internal_edges = sorted(
                f"{source} -> {target}"
                for source in ordered
                for target in graph[source]
                if target in component
            )
            findings.append(
                Finding(
                    code="QG022",
                    severity="warning",
                    confidence="high",
                    path=str(first.path),
                    line=1,
                    column=1,
                    message=f"检测到包含 {len(ordered)} 个模块的循环依赖分量。",
                    suggestion=(
                        "检查模块职责和依赖方向；类型专用导入可移入 TYPE_CHECKING，"
                        "运行时循环应重构公共边界。"
                    ),
                    evidence={"modules": ordered, "edges": internal_edges},
                )
            )
        return findings


class OversizedFunctionRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG014 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG014"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG014 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if definition.kind not in {"function", "method"}:
            return []
        findings: list[Finding] = []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
        if definition.lines > context.config.max_function_lines:
            findings.append(
                Finding(
                    code="QG014",
                    severity="critical",
                    confidence="high",
                    message=f"`{definition.qualname}` 长度 {definition.lines} 行，超过阈值 {context.config.max_function_lines}。",
                    suggestion="优先按明确阶段或独立副作用拆分；不要仅为了缩短行数制造单次 helper。",
                    evidence={"lines": definition.lines},
                    **common,
                )
            )
        return findings


class DeepNestingRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG016 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG016"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG016 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if definition.kind not in {"function", "method"}:
            return []
        findings: list[Finding] = []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
        if definition.max_nesting > context.config.max_nesting:
            findings.append(
                Finding(
                    code="QG016",
                    severity="warning",
                    confidence="high",
                    message=f"`{definition.qualname}` 最大控制流嵌套 {definition.max_nesting} 层，超过阈值 {context.config.max_nesting}。",
                    suggestion="使用早返回、明确失败路径或阶段化流程降低嵌套。",
                    evidence={"max_nesting": definition.max_nesting},
                    **common,
                )
            )
        return findings


class HighBranchCountRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG017 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG017"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG017 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if definition.kind not in {"function", "method"}:
            return []
        findings: list[Finding] = []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
        if definition.branch_count >= context.config.max_branches:
            findings.append(
                Finding(
                    code="QG017",
                    severity="info",
                    confidence="medium",
                    message=f"`{definition.qualname}` 估算分支数 {definition.branch_count}，达到语义提示阈值 {context.config.max_branches}。",
                    suggestion="按上下游契约评估是否能原地简化，必要状态机不必机械拆分。",
                    evidence={
                        "branches": definition.branch_count,
                        "structural_advisory": True,
                    },
                    **common,
                )
            )
        return findings


class WideFunctionInterfaceRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG015 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG015"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG015 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if definition.kind not in {"function", "method"}:
            return []
        findings: list[Finding] = []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
        if definition.parameter_count > context.config.max_parameters:
            findings.append(
                Finding(
                    code="QG015",
                    severity="warning",
                    confidence="high",
                    message=f"`{definition.qualname}` 有 {definition.parameter_count} 个参数，超过阈值 {context.config.max_parameters}。",
                    suggestion="检查是否混合多个职责；仅在参数天然属于同一稳定概念时引入明确的数据对象。",
                    evidence={"parameters": definition.parameter_count},
                    **common,
                )
            )
        return findings


class BooleanModeMatrixRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG018 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG018"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG018 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if definition.kind not in {"function", "method"}:
            return []
        findings: list[Finding] = []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
        if definition.boolean_flag_count >= BOOLEAN_FLAG_THRESHOLD:
            findings.append(
                Finding(
                    code="QG018",
                    severity="warning",
                    confidence="medium",
                    message=f"`{definition.qualname}` 接收 {definition.boolean_flag_count} 个布尔开关参数。",
                    suggestion="多个布尔开关通常形成隐式模式矩阵；考虑拆成明确模式、枚举或不同入口。",
                    evidence={"boolean_flags": definition.boolean_flag_count},
                    **common,
                )
            )
        return findings


class ModuleLineLimitRule(CoreQualityRule[ModuleRuleContext]):
    """Implement QG019 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG019"

    def evaluate(self, context: ModuleRuleContext) -> list[Finding]:
        """Evaluate QG019 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        item = context.item
        findings: list[Finding] = []
        lines = item.source.count("\n") + 1
        if lines > context.config.max_module_lines:
            findings.append(
                Finding(
                    code="QG019",
                    severity="critical",
                    confidence="high",
                    path=str(item.path),
                    line=1,
                    column=1,
                    message=f"模块共 {lines} 行，超过硬上限 {context.config.max_module_lines}。",
                    suggestion="先按稳定领域边界、状态所有权或外部接口拆分；禁止为了降低行数机械切文件或制造转发层。",
                    evidence={
                        "lines": lines,
                        "review_threshold": context.config.module_line_review_threshold,
                        "hard_limit": context.config.max_module_lines,
                        "definitions": len(item.definitions),
                    },
                )
            )
        elif lines > context.config.module_line_review_threshold:
            findings.append(
                Finding(
                    code="QG019",
                    severity="info",
                    confidence="medium",
                    path=str(item.path),
                    line=1,
                    column=1,
                    message=f"模块共 {lines} 行，进入 owner cohesion 语义复核区间。",
                    suggestion="确认文件仍对应单一稳定 owner；如果没有独立职责、状态或生命周期边界，不要仅按行数拆分。",
                    evidence={
                        "lines": lines,
                        "review_threshold": context.config.module_line_review_threshold,
                        "hard_limit": context.config.max_module_lines,
                        "definitions": len(item.definitions),
                        "semantic_review_required": True,
                        "semantic_review_question": "Q4,Q9",
                        "semantic_review_kind": "owner-cohesion-size",
                    },
                )
            )
        return findings


class SuppressionMarkerRule(CoreQualityRule[ModuleRuleContext]):
    """Implement QG020 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG020"

    def evaluate(self, context: ModuleRuleContext) -> list[Finding]:
        """Evaluate QG020 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        item = context.item
        findings: list[Finding] = []
        for token in tokenize.generate_tokens(StringIO(item.source).readline):
            if token.type != tokenize.COMMENT:
                continue
            if "# noqa" not in token.string and "# type: ignore" not in token.string:
                continue
            findings.append(
                Finding(
                    code="QG020",
                    severity="warning",
                    confidence="high",
                    path=str(item.path),
                    line=token.start[0],
                    column=token.start[1] + 1,
                    message="发现静态检查抑制标记。",
                    suggestion="确认抑制范围足够窄、原因仍然成立；新增 suppression 必须语义裁决，能消除根因时不得保留。",
                    evidence={
                        "text": token.string,
                        "qg179_exempt": True,
                        "semantic_review_required": True,
                        "semantic_review_question": "Q6",
                        "semantic_review_kind": "suppression",
                    },
                )
            )
        return findings


class MissingDocstringRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG027 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG027"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG027 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if not context.config.require_docstrings:
            return []
        if definition.has_docstring or context.inherits_documentation:
            return []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
        if not definition.has_docstring:
            return [
                Finding(
                    code="QG027",
                    severity="warning" if is_test_path(definition.path) else "error",
                    confidence="high",
                    message=f"`{definition.qualname}` 缺少 docstring，docstring 覆盖率下降。",
                    suggestion="必须补充 docstring；公开接口写完整多行 Args/Returns，非公开结构至少写明职责边界。",
                    evidence={
                        "kind": definition.kind,
                        "public": not context.is_private,
                    },
                    **common,
                )
            ]
        return []


class ShortDocstringRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG028 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG028"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG028 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if not context.config.require_docstrings:
            return []
        if (
            not definition.has_docstring
            or context.is_private
            or definition.docstring_line_count >= context.config.docstring_min_lines
        ):
            return []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
        return [
            Finding(
                code="QG028",
                severity="warning" if is_test_path(definition.path) else "error",
                confidence="high",
                message=f"公开接口 `{definition.qualname}` 的 docstring 只有 {definition.docstring_line_count} 行有效内容。",
                suggestion="公开接口必须使用完整多行 docstring，并按需要列出 Args 与 Returns。",
                evidence={
                    "content_lines": definition.docstring_line_count,
                    "minimum": context.config.docstring_min_lines,
                },
                **common,
            )
        ]


class UndocumentedParametersRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG029 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG029"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG029 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if context.is_private:
            return []
        if not context.config.require_docstring_sections or definition.kind == "class":
            return []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
        findings: list[Finding] = []
        documented = set(definition.documented_parameters)
        missing_parameters = [
            name for name in definition.parameter_names if name not in documented
        ]
        if missing_parameters:
            findings.append(
                Finding(
                    code="QG029",
                    severity="warning" if is_test_path(definition.path) else "error",
                    confidence="high",
                    message=f"公开接口 `{definition.qualname}` 的 Args 章节未完整说明参数："
                    + ", ".join(missing_parameters),
                    suggestion="公开函数/方法必须用 Google 风格 Args: 章节逐项说明全部非 self/cls 参数。",
                    evidence={
                        "parameters": list(definition.parameter_names),
                        "documented": list(definition.documented_parameters),
                        "missing": missing_parameters,
                    },
                    **common,
                )
            )
        return findings


class MissingReturnsSectionRule(CoreQualityRule[DefinitionRuleContext]):
    """Implement QG030 using one precomputed repository analysis context.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG030"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG030 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        definition = context.definition
        if context.is_private:
            return []
        if not context.config.require_docstring_sections or definition.kind == "class":
            return []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
        findings: list[Finding] = []
        if {"Returns", "Yields"} & set(definition.docstring_sections):
            return findings
        findings.append(
            Finding(
                code="QG030",
                severity="warning" if is_test_path(definition.path) else "error",
                confidence="high",
                message=f"公开接口 `{definition.qualname}` 的 docstring 缺少 Returns 或 Yields 章节。",
                suggestion="公开函数/方法必须显式说明返回值；无返回值也写明 Returns: None。",
                evidence={"sections": list(definition.docstring_sections)},
                **common,
            )
        )
        return findings
