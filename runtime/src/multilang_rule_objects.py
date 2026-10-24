"""Independent multi-language detector owners consuming shared parser/graph facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import GuardConfig
from .core_rule_contract import CoreQualityRule
from .model import Finding

_SOURCE = "quality-guard-multilang"


@dataclass(frozen=True, slots=True)
class ScriptDuplicateContext:
    """Already-grouped JS/TS definitions and normalized signatures.

    Fields: path, scope, name, definitions."""

    path: str
    scope: str
    name: str
    definitions: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ScriptShapeContext:
    """Pre-grouped JS/TS definition shapes from the shared symbol scan.

    Fields: definitions."""

    definitions: list[tuple[dict[str, Any], dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class CompositionContext:
    """A precomputed dynamic composition graph, reused by both architecture rules.

    Fields: module, class_name, methods, constructor_fields, modules, largest_scc, edges."""

    module: str
    class_name: str
    methods: int
    constructor_fields: int
    modules: list[str]
    largest_scc: list[str]
    edges: list[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class CssDuplicateContext:
    """Normalized CSS selectors collected by the shared CSS parser.

    Fields: declarations."""

    declarations: list[tuple[str, dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class HtmlDuplicateContext:
    """HTML ID occurrences recorded by the single HTML parser pass.

    Fields: path, value, lines."""

    path: str
    value: str
    lines: list[int]


@dataclass(frozen=True, slots=True)
class ScriptDeltaContext:
    """Existing script owner facts from the baseline and target snapshots.

    Fields: key, base, target, added_helpers."""

    key: tuple[str, str]
    base: dict[str, Any]
    target: dict[str, Any]
    added_helpers: list[str]


@dataclass(frozen=True, slots=True)
class CppDeltaContext:
    """Comparable changed C++ definition metrics from one compiler-fact pass.

    Fields: current, baseline, config."""

    current: dict[str, Any]
    baseline: dict[str, Any] | None
    config: GuardConfig


class DuplicateScriptDeclarationRule(CoreQualityRule[ScriptDuplicateContext]):
    """QG195: report same-scope declaration shadowing.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG195"

    def evaluate(self, context: ScriptDuplicateContext) -> list[Finding]:
        """Evaluate QG195 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if len(context.definitions) <= 1:
            return []
        return [
            Finding(
                code=self.code,
                severity="error",
                confidence="high",
                path=context.path,
                line=min(row["line"] for row in context.definitions),
                column=1,
                symbol=f"{context.scope}::{context.name}",
                message=(
                    f"同一作用域 `{context.scope}` 重复定义 `{context.name}`，后声明会遮蔽前声明。"
                ),
                suggestion="删除重复声明或改成唯一 owner；不要依赖 JavaScript declaration shadowing。",
                evidence={"lines": [row["line"] for row in context.definitions]},
                source=_SOURCE,
            )
        ]


class DuplicateScriptShapeRule(CoreQualityRule[ScriptShapeContext]):
    """QG200: report structurally equal normalized JS/TS function bodies.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG200"

    def evaluate(self, context: ScriptShapeContext) -> list[Finding]:
        """Evaluate QG200 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        group = context.definitions
        if len(group) < 2:
            return []
        members = sorted(
            f"{script['path']}::{definition['qualname']}"
            for script, definition in group
        )
        return [
            Finding(
                code=self.code,
                severity="info",
                confidence="medium",
                path=group[0][0]["path"],
                line=min(definition["line"] for _, definition in group),
                column=1,
                symbol=" | ".join(members),
                message=f"{len(group)} 个 JS/TS 定义具有相同规范化 AST 结构。",
                suggestion="仅作为复用候选；对称操作、类型适配和语义谓词不得机械合并。",
                evidence={"members": members},
                source=_SOURCE,
            )
        ]


class DynamicCompositionSizeRule(CoreQualityRule[CompositionContext]):
    """QG198: assess the true combined dynamic owner surface.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG198"

    def evaluate(self, context: CompositionContext) -> list[Finding]:
        """Evaluate QG198 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.methods <= 100 and context.constructor_fields <= 50:
            return []
        return [
            Finding(
                code=self.code,
                severity="warning",
                confidence="high",
                path=context.module,
                line=1,
                column=1,
                symbol=context.class_name,
                message=(
                    f"动态 owner `{context.class_name}` 组合 {context.methods} 个方法并持有 "
                    f"{context.constructor_fields} 个构造字段。"
                ),
                suggestion="检查是否仍是分文件后的 God Controller；按状态和调用闭包收窄 owner surface。",
                evidence={
                    "composed_methods": context.methods,
                    "constructor_fields": context.constructor_fields,
                    "feature_modules": len(context.modules),
                },
                source=_SOURCE,
            )
        ]


class FeatureDependencyCycleRule(CoreQualityRule[CompositionContext]):
    """QG199: evaluate SCC size on the already-built feature graph.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG199"

    def evaluate(self, context: CompositionContext) -> list[Finding]:
        """Evaluate QG199 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        largest = context.largest_scc
        if len(largest) < 5:
            return []
        return [
            Finding(
                code=self.code,
                severity="warning",
                confidence="high",
                path=context.module,
                line=1,
                column=1,
                symbol=context.class_name,
                message=f"动态 feature 依赖图存在 {len(largest)} 模块强连通分量。",
                suggestion="不要只依赖 ES import 无环；按真实 this/self 调用关系拆分闭环。",
                evidence={
                    "largest_scc_size": len(largest),
                    "modules": largest,
                    "cross_feature_calls": len(context.edges),
                    "distinct_edges": len(set(context.edges)),
                },
                source=_SOURCE,
            )
        ]


class ExplicitAnyEscapeRule(CoreQualityRule[tuple[int, str]]):
    """QG207: flag repository-wide JS/TS JSDoc type escapes.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG207"

    def evaluate(self, context: tuple[int, str]) -> list[Finding]:
        """Evaluate QG207 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        count, first_path = context
        if count < 10:
            return []
        return [
            Finding(
                code=self.code,
                severity="info",
                confidence="medium",
                path=first_path,
                line=1,
                column=1,
                symbol="javascript",
                message=f"JS/TS 中存在 {count} 个显式 any JSDoc 逃逸。",
                suggestion=(
                    "动态 composition 可保留必要 escape，但不能把 checkJs PASS "
                    "当作完整 owner contract 证明。"
                ),
                evidence={"any_escapes": count},
                source=_SOURCE,
            )
        ]


class DuplicateCssDeclarationRule(CoreQualityRule[CssDuplicateContext]):
    """QG201: flag identical normalized CSS declaration groups.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG201"

    def evaluate(self, context: CssDuplicateContext) -> list[Finding]:
        """Evaluate QG201 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        group = context.declarations
        if len(group) < 2:
            return []
        members = sorted(f"{path}::{rule['selector']}" for path, rule in group)
        return [
            Finding(
                code=self.code,
                severity="info",
                confidence="high",
                path=group[0][0],
                line=min(rule["line"] for _, rule in group),
                column=1,
                symbol=" | ".join(members),
                message=f"{len(group)} 个 CSS selector 具有完全相同的声明块。",
                suggestion=(
                    "只有语义 owner 一致时才合并 selector/class；"
                    "声明值巧合相同不得机械绑定组件。"
                ),
                evidence={"members": members},
                source=_SOURCE,
            )
        ]


class DuplicateHtmlIdRule(CoreQualityRule[HtmlDuplicateContext]):
    """QG202: flag duplicate DOM ids within one HTML document.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG202"

    def evaluate(self, context: HtmlDuplicateContext) -> list[Finding]:
        """Evaluate QG202 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if len(context.lines) <= 1:
            return []
        return [
            Finding(
                code=self.code,
                severity="error",
                confidence="high",
                path=context.path,
                line=min(context.lines),
                column=1,
                symbol=context.value,
                message=f"HTML id `{context.value}` 在同一文档重复出现 {len(context.lines)} 次。",
                suggestion="id 必须唯一；需要复用样式/行为时改用 class 或 data-*。",
                evidence={"lines": context.lines},
                source=_SOURCE,
            )
        ]


class GiantScriptOwnerGrowthRule(CoreQualityRule[ScriptDeltaContext]):
    """QG196: detect significant growth of already-giant JS owners.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG196"

    def evaluate(self, context: ScriptDeltaContext) -> list[Finding]:
        """Evaluate QG196 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        base, target, key = context.base, context.target, context.key
        line_delta = target["lines"] - base["lines"]
        nested_delta = target["nested_defs"] - base["nested_defs"]
        giant = target["lines"] > 500 or target["nested_defs"] >= 50
        if not giant or (line_delta < 20 and nested_delta < 5):
            return []
        return [
            Finding(
                code=self.code,
                severity="info",
                confidence="high",
                path=key[0],
                line=target["line"],
                column=1,
                symbol=key[1],
                message=f"既有 giant owner `{key[1]}` 仍保持超大规模且本轮继续恶化。",
                suggestion=(
                    "不得只用 child helper 指标下降宣称质量清零；"
                    "解释 owner 增长是否必要，或继续按真实职责缩减。"
                ),
                evidence={
                    "lines": [base["lines"], target["lines"]],
                    "nested_defs": [base["nested_defs"], target["nested_defs"]],
                },
                source=_SOURCE,
            )
        ]


class NestedHelperGrowthRule(CoreQualityRule[ScriptDeltaContext]):
    """QG197: detect helper proliferation under one giant JS owner.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG197"

    def evaluate(self, context: ScriptDeltaContext) -> list[Finding]:
        """Evaluate QG197 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        added = context.added_helpers
        if len(added) < 5:
            return []
        key, target = context.key, context.target
        return [
            Finding(
                code=self.code,
                severity="info",
                confidence="medium",
                path=key[0],
                line=target["line"],
                column=1,
                symbol=key[1],
                message=(
                    f"既有 giant owner 本轮新增 {len(added)} 个直接 nested helper，"
                    "存在 helper laundering 风险。"
                ),
                suggestion=(
                    "验证复杂度是否真正迁移到独立 owner；若只是同一 closure 内切碎步骤，"
                    "应继续减法或明确保留理由。"
                ),
                evidence={"new_direct_helpers": len(added), "examples": added[:20]},
                source=_SOURCE,
            )
        ]


class CppChangedComplexityRule(CoreQualityRule[CppDeltaContext]):
    """QG204: compare exact Clang metrics for changed C++ definitions.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG204"

    def evaluate(self, context: CppDeltaContext) -> list[Finding]:
        """Evaluate QG204 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        current, baseline, config = context.current, context.baseline, context.config
        crossed = (
            current["lines"] > config.max_function_lines
            or current["branches"] > config.max_branches
            or current["max_nesting"] > config.max_nesting
        )
        worsened = baseline is None or any(
            current[field] > baseline[field]
            for field in ("lines", "branches", "max_nesting")
        )
        if not crossed or not worsened:
            return []
        severity = (
            "critical" if current["lines"] > config.max_function_lines else "warning"
        )
        return [
            Finding(
                code=self.code,
                severity=severity,
                confidence="medium",
                path=current["path"],
                line=current["line"],
                column=1,
                symbol=current["symbol"],
                message=(
                    f"C++ 变更后的 `{current['symbol']}` 结构复杂度越过或恶化："
                    f"{current['lines']} 行 / {current['branches']} 分支 / "
                    f"nesting {current['max_nesting']}。"
                ),
                suggestion=(
                    "保留 -Werror/ctest，同时按真实职责降低结构复杂度；"
                    "不要只依赖编译器 warning gate。"
                ),
                evidence={"baseline": baseline, "current": current},
                source=_SOURCE,
            )
        ]
