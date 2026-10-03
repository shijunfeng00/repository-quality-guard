from __future__ import annotations

import tokenize
from collections import defaultdict
from io import StringIO

from .config import GuardConfig, is_test_path
from .facts import ModuleFacts
from .graph_utils import strongly_connected_components
from .model import Definition, Finding
from .topology_facts import RepositoryTopology, UsageKind, Visibility
from .topology_policy import (
    HelperUsageEvidence,
    ephemeral_helper_candidate,
)

BOOLEAN_FLAG_THRESHOLD = 3
DUPLICATE_BODY_MIN_COUNT = 2
DUPLICATE_BODY_MIN_LINES = 6


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
        findings: list[Finding] = []
        for definition in definitions:
            findings.extend(self._docstring_findings(definition))
            if definition.kind == "class":
                findings.extend(self._small_class_findings(definition))
                findings.extend(self._class_shape_findings(definition))
            elif definition.kind in {"function", "method"}:
                findings.extend(self._low_use_findings(definition))
                findings.extend(self._function_size_findings(definition))
                findings.extend(self._function_interface_findings(definition))
        findings.extend(self._fragmented_owner_findings(definitions))
        findings.extend(self._module_findings(facts))
        findings.extend(self._duplicate_body_findings(definitions))
        findings.extend(self._unused_private_findings(definitions))
        findings.extend(self._import_cycle_findings(facts))
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
        findings = self._docstring_presence_findings(definition)
        if not definition.has_docstring:
            return findings
        if self._is_private_definition(definition):
            return findings
        findings.extend(self._docstring_section_findings(definition))
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

    def _docstring_presence_findings(self, definition: Definition) -> list[Finding]:
        """
        检查 docstring 是否存在，公开接口是否达到完整多行要求。

        Args:
            definition: 待评估的代码定义。

        Returns:
            当前定义对应的 docstring 覆盖问题列表。
        """
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
                    suggestion=(
                        "必须补充 docstring；公开接口写完整多行 Args/Returns，"
                        "非公开结构至少写明职责边界。"
                    ),
                    evidence={
                        "kind": definition.kind,
                        "public": not self._is_private_definition(definition),
                    },
                    **common,
                )
            ]
        if self._is_private_definition(definition):
            return []
        if definition.docstring_line_count >= self.config.docstring_min_lines:
            return []
        return [
            Finding(
                code="QG028",
                severity="warning" if is_test_path(definition.path) else "error",
                confidence="high",
                message=(
                    f"公开接口 `{definition.qualname}` 的 docstring 只有 "
                    f"{definition.docstring_line_count} 行有效内容。"
                ),
                suggestion="公开接口必须使用完整多行 docstring，并按需要列出 Args 与 Returns。",
                evidence={
                    "content_lines": definition.docstring_line_count,
                    "minimum": self.config.docstring_min_lines,
                },
                **common,
            )
        ]

    def _docstring_section_findings(self, definition: Definition) -> list[Finding]:
        """
        检查公开函数和方法 docstring 的 Args 与 Returns/Yields 章节。

        Args:
            definition: 待评估的代码定义。

        Returns:
            当前定义对应的章节完整性问题列表。
        """
        if not self.config.require_docstring_sections or definition.kind == "class":
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
                    message=(
                        f"公开接口 `{definition.qualname}` 的 Args 章节未完整说明参数："
                        + ", ".join(missing_parameters)
                    ),
                    suggestion="公开函数/方法必须用 Google 风格 Args: 章节逐项说明全部非 self/cls 参数。",
                    evidence={
                        "parameters": list(definition.parameter_names),
                        "documented": list(definition.documented_parameters),
                        "missing": missing_parameters,
                    },
                    **common,
                )
            )
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

    def _usage_topology(self, definition: Definition) -> dict[str, object]:
        """Return normalized usage evidence for one callable definition.

        Args:
            definition: Callable definition being classified.

        Returns:
            Stable direct-call, callback/reference, protocol and visibility evidence.
        """
        if self.topology is None or definition.symbol_id not in self._symbols:
            direct_callers = definition.calls
            direct_call_sites = definition.calls
            callable_consumers = definition.references
            protocol_edges = 1 if definition.externally_invoked else 0
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
            symbol = self._symbols[definition.symbol_id]
            direct = self.topology.incoming(
                definition.symbol_id, (UsageKind.DIRECT_CALL,)
            )
            callable_edges = self.topology.incoming(
                definition.symbol_id,
                (UsageKind.CALLABLE_REFERENCE, UsageKind.CALLBACK_REGISTRATION),
            )
            protocol = self.topology.outgoing(
                definition.symbol_id, (UsageKind.PROTOCOL_HOOK, UsageKind.OVERRIDE)
            )
            direct_callers = len({edge.source_id for edge in direct})
            direct_call_sites = len(direct)
            callable_consumers = len({edge.source_id for edge in callable_edges})
            protocol_edges = len(protocol)
            visibility = symbol.visibility.value
            exported = symbol.exported
            resolution = "normalized"
            owner_id = symbol.owner_id
            nested = symbol.nested
        return {
            "direct_callers": direct_callers,
            "direct_call_sites": direct_call_sites,
            "callable_consumers": callable_consumers,
            "protocol_edges": protocol_edges,
            "visibility": visibility,
            "exported": exported,
            "owner_id": owner_id,
            "nested": nested,
            "topology_resolution": resolution,
        }

    def _small_class_findings(self, definition: Definition) -> list[Finding]:
        """检查短小且低实例化频次的类定义。

        Args:
            definition: 待评估的类定义。

        Returns:
            短小低使用类对应的 QG002 信息提示。
        """
        eligible = (
            definition.name not in self.config.ignored_names
            and definition.lines <= self.config.short_max_lines
            and definition.calls <= self.config.low_use_max_calls
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

    def _low_use_findings(self, definition: Definition) -> list[Finding]:
        """检查短小、低复用且没有协议/回调消费者的内部 helper。

        Args:
            definition: 待评估的函数或方法定义。

        Returns:
            仅真正 ephemeral helper 候选对应的 QG001。
        """
        topology_evidence = self._usage_topology(definition)
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
            self.config,
            definition.externally_invoked,
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

    def _class_shape_findings(self, definition: Definition) -> list[Finding]:
        """
        检查类方法数量与碎片化程度。

        Args:
            definition: 待评估的代码定义。

        Returns:
            当前类对应的结构问题列表。
        """
        findings: list[Finding] = []
        method_count = definition.direct_method_count
        if method_count > self.config.max_class_methods:
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
                        f"超过硬上限 {self.config.max_class_methods}。"
                    ),
                    symbol=definition.symbol_id,
                    suggestion=(
                        "识别真正独立的状态、生命周期或领域 owner 后再拆分；"
                        "禁止为了降低方法数制造无状态 wrapper、mixin 或一次性 helper。"
                    ),
                    evidence={
                        "methods": method_count,
                        "review_threshold": self.config.class_method_review_threshold,
                        "hard_limit": self.config.max_class_methods,
                        "public_methods": definition.public_method_count,
                        "tiny_methods": definition.tiny_method_count,
                    },
                )
            )
        elif method_count > self.config.class_method_review_threshold:
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
                        "review_threshold": self.config.class_method_review_threshold,
                        "hard_limit": self.config.max_class_methods,
                        "public_methods": definition.public_method_count,
                        "tiny_methods": definition.tiny_method_count,
                        "semantic_review_required": True,
                        "semantic_review_question": "Q4,Q9",
                        "semantic_review_kind": "owner-cohesion-size",
                    },
                )
            )
        return findings

    def _fragmented_owner_findings(
        self, definitions: list[Definition]
    ) -> list[Finding]:
        """Detect owners dominated by one-shot internal helpers.

        Args:
            definitions: Current repository definitions after usage resolution.

        Returns:
            QG013 semantic candidates for owners with dense ephemeral-helper swarms.
        """
        if self.topology is None:
            return []
        by_owner: defaultdict[str, list[tuple[Definition, dict[str, object]]]] = (
            defaultdict(list)
        )
        for definition in definitions:
            if definition.kind not in {"function", "method"}:
                continue
            evidence = self._usage_topology(definition)
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
                    self.config,
                    definition.externally_invoked,
                )
            ]
            helper_count = len(helpers)
            internal_count = len(owned)
            if helper_count < self.config.fragmented_owner_min_helpers:
                continue
            ratio = helper_count / internal_count
            if ratio < self.config.fragmented_owner_ratio:
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
                        "min_helpers": self.config.fragmented_owner_min_helpers,
                        "ratio_threshold": self.config.fragmented_owner_ratio,
                        "helpers": [item.symbol_id for item in helpers],
                        "semantic_review_required": True,
                        "semantic_review_question": "Q4,Q9",
                        "semantic_review_kind": "ephemeral-helper-swarm",
                    },
                )
            )
        return findings

    def _function_size_findings(self, definition: Definition) -> list[Finding]:
        """
        检查包装函数、长度、嵌套和分支数量。

        Args:
            definition: 待评估的代码定义。

        Returns:
            当前函数对应的规模问题列表。
        """
        findings: list[Finding] = []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
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
        node_visitor_hook = (
            private_facade
            and definition.name.startswith("visit_")
            and definition.externally_invoked
            and any(base.endswith("NodeVisitor") for base in definition.bases)
        )
        critical_facade = (
            private_facade and not property_facade and not node_visitor_hook
        )
        report_wrapper = (
            definition.wrapper_target
            and not property_facade
            and (critical_facade or not definition.externally_invoked)
        )
        if report_wrapper:
            if private_facade:
                message = (
                    f"公开接口 `{definition.qualname}` 只套壳访问 private 目标 "
                    f"`{definition.wrapper_target}`。"
                )
            elif property_facade:
                message = (
                    f"property 接口 `{definition.qualname}` 只委托给 "
                    f"`{definition.wrapper_target}`，没有独立契约。"
                )
            else:
                message = (
                    f"`{definition.qualname}` 只把参数转发给 `{definition.wrapper_target}`，"
                    "属于单层委托包装。"
                )
            findings.append(
                Finding(
                    code="QG185" if critical_facade else "QG010",
                    severity=(
                        "critical"
                        if critical_facade
                        or (definition.name.startswith("_") and definition.calls <= 1)
                        else "warning"
                        if definition.calls <= 1
                        else "info"
                    ),
                    confidence="high",
                    message=message,
                    suggestion=(
                        "禁止用 public 方法或 @property 给 private 实现再套一层。"
                        "确需跨所有者使用时，将唯一实现正式公开并让调用方直接调用；"
                        "否则保持 private 并由其所有者内部使用。"
                        if critical_facade
                        else "没有独立契约、校验、日志或语义转换时，删除包装并直接调用目标。"
                    ),
                    evidence={
                        "target": definition.wrapper_target,
                        "calls": definition.calls,
                        "private_facade": private_facade,
                        "property_facade": property_facade,
                    },
                    **common,
                )
            )
        if definition.lines > self.config.max_function_lines:
            findings.append(
                Finding(
                    code="QG014",
                    severity="critical",
                    confidence="high",
                    message=(
                        f"`{definition.qualname}` 长度 {definition.lines} 行，"
                        f"超过阈值 {self.config.max_function_lines}。"
                    ),
                    suggestion="优先按明确阶段或独立副作用拆分；不要仅为了缩短行数制造单次 helper。",
                    evidence={"lines": definition.lines},
                    **common,
                )
            )
        if definition.max_nesting > self.config.max_nesting:
            findings.append(
                Finding(
                    code="QG016",
                    severity="warning",
                    confidence="high",
                    message=(
                        f"`{definition.qualname}` 最大控制流嵌套 {definition.max_nesting} 层，"
                        f"超过阈值 {self.config.max_nesting}。"
                    ),
                    suggestion="使用早返回、明确失败路径或阶段化流程降低嵌套。",
                    evidence={"max_nesting": definition.max_nesting},
                    **common,
                )
            )
        if definition.branch_count > self.config.max_branches:
            findings.append(
                Finding(
                    code="QG017",
                    severity="warning",
                    confidence="medium",
                    message=(
                        f"`{definition.qualname}` 估算分支数 {definition.branch_count}，"
                        f"超过阈值 {self.config.max_branches}。"
                    ),
                    suggestion="检查是否混入模式选择、兼容路径或多阶段职责；优先收窄状态和输入契约。",
                    evidence={"branches": definition.branch_count},
                    **common,
                )
            )
        return findings

    def _function_interface_findings(self, definition: Definition) -> list[Finding]:
        """
        检查参数数量和布尔模式开关。

        Args:
            definition: 待评估的代码定义。

        Returns:
            当前函数对应的接口问题列表。
        """
        findings: list[Finding] = []
        common = {
            "path": str(definition.path),
            "line": definition.line,
            "column": definition.column,
            "symbol": definition.symbol_id,
        }
        if definition.parameter_count > self.config.max_parameters:
            findings.append(
                Finding(
                    code="QG015",
                    severity="warning",
                    confidence="high",
                    message=(
                        f"`{definition.qualname}` 有 {definition.parameter_count} 个参数，"
                        f"超过阈值 {self.config.max_parameters}。"
                    ),
                    suggestion="检查是否混合多个职责；仅在参数天然属于同一稳定概念时引入明确的数据对象。",
                    evidence={"parameters": definition.parameter_count},
                    **common,
                )
            )
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

    def _module_findings(self, facts: list[ModuleFacts]) -> list[Finding]:
        """
        检查超大模块与静态检查抑制标记。

        Args:
            facts: 当前模块事实或模块事实列表。

        Returns:
            模块级问题列表。
        """
        findings: list[Finding] = []
        for item in facts:
            lines = item.source.count("\n") + 1
            if lines > self.config.max_module_lines:
                findings.append(
                    Finding(
                        code="QG019",
                        severity="critical",
                        confidence="high",
                        path=str(item.path),
                        line=1,
                        column=1,
                        message=f"模块共 {lines} 行，超过硬上限 {self.config.max_module_lines}。",
                        suggestion=(
                            "先按稳定领域边界、状态所有权或外部接口拆分；"
                            "禁止为了降低行数机械切文件或制造转发层。"
                        ),
                        evidence={
                            "lines": lines,
                            "review_threshold": self.config.module_line_review_threshold,
                            "hard_limit": self.config.max_module_lines,
                            "definitions": len(item.definitions),
                        },
                    )
                )
            elif lines > self.config.module_line_review_threshold:
                findings.append(
                    Finding(
                        code="QG019",
                        severity="info",
                        confidence="medium",
                        path=str(item.path),
                        line=1,
                        column=1,
                        message=f"模块共 {lines} 行，进入 owner cohesion 语义复核区间。",
                        suggestion=(
                            "确认文件仍对应单一稳定 owner；如果没有独立职责、状态或生命周期边界，"
                            "不要仅按行数拆分。"
                        ),
                        evidence={
                            "lines": lines,
                            "review_threshold": self.config.module_line_review_threshold,
                            "hard_limit": self.config.max_module_lines,
                            "definitions": len(item.definitions),
                            "semantic_review_required": True,
                            "semantic_review_question": "Q4,Q9",
                            "semantic_review_kind": "owner-cohesion-size",
                        },
                    )
                )
            if item.tree is None:
                continue
            for token in tokenize.generate_tokens(StringIO(item.source).readline):
                if token.type != tokenize.COMMENT:
                    continue
                if (
                    "# noqa" not in token.string
                    and "# type: ignore" not in token.string
                ):
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

    def _duplicate_body_findings(self, definitions: list[Definition]) -> list[Finding]:
        """
        检查具有相同 AST 函数体的重复实现。

        Args:
            definitions: 仓库内已收集的定义列表。

        Returns:
            重复函数体问题列表。
        """
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

    def _unused_private_findings(self, definitions: list[Definition]) -> list[Finding]:
        """检查未被静态引用的单下划线私有函数和方法。

        Args:
            definitions: 仓库内已收集的定义列表。

        Returns:
            未使用私有定义问题列表。
        """
        findings: list[Finding] = []
        for definition in definitions:
            private_callable = (
                definition.kind in {"function", "method"}
                and definition.name.startswith("_")
                and not (
                    definition.name.startswith("__") and definition.name.endswith("__")
                )
            )
            if not private_callable or definition.externally_invoked:
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

    def _import_cycle_findings(self, facts: list[ModuleFacts]) -> list[Finding]:
        """
        按强连通分量报告仓库内部模块导入环。

        Args:
            facts: 当前模块事实或模块事实列表。

        Returns:
            每个循环依赖分量一条发现，避免枚举大量等价环路。
        """
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
