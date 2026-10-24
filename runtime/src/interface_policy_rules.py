"""Independent QG180/QG181 interface policy owners over one Git interface diff."""

from __future__ import annotations

from dataclasses import dataclass

from .core_rule_contract import CoreQualityRule
from .model import Finding, InterfaceChange


@dataclass(frozen=True, slots=True)
class InterfaceGrowthContext:
    """Hold the Git interface definitions added and removed in this audit.

    These sets are computed by the interface diff owner before QG181 runs; the
    growth rule measures their net count without inspecting Git a second time.
    """

    added: tuple[InterfaceChange, ...]
    removed: tuple[InterfaceChange, ...]


class InterfaceChangeReviewRule(CoreQualityRule[InterfaceChange]):
    """Describe why each added or changed source interface needs review.

    QG180 emits individually attributable review findings. Deletions do not
    require this review; property shape changes and private-to-public promotions
    are separately identified using the existing interface delta details.
    """

    code = "QG180"

    def evaluate(self, change: InterfaceChange) -> list[Finding]:
        """Create a QG180 finding for one introduced or changed interface.

        Deletions and non-callable interface kinds are outside this rule.
        Added properties, parameter changes, or promoted private symbols keep
        their distinct evidence and suggested reviewer obligations.

        Args:
            change: Source-bound Git interface change with before/after facts.

        Returns:
            One attributed review finding for a supported addition or update,
            or an empty list for deletions and unsupported declaration kinds.
        """
        if change.change == "removed" or change.kind not in {
            "class",
            "function",
            "method",
        }:
            return []
        symbol = change.after or change.before
        is_addition = change.change == "added"
        policy = change.details.get("interface_policy", {})
        promoted_from = policy.get("visibility_promotion_from", "")
        property_added = bool(policy.get("property_added"))
        property_changed = bool(policy.get("property_decorator_changed"))
        changed_fields = "、".join(sorted(change.details)) or "声明"
        message = f"存量 `{change.kind}` 接口 `{change.symbol}` 的声明发生变化：{changed_fields}。"
        if "parameters" in change.details:
            message = (
                f"存量 `{change.kind}` 接口 `{change.symbol}` 的参数列表发生变化。"
            )
        if property_changed:
            message = f"存量接口 `{change.symbol}` 的 property/装饰器形态发生变化。"
        if is_addition:
            message = (
                f"新增生产 `{change.kind}` 接口 `{change.symbol}`，必须完成减法审判。"
            )
        if property_added:
            message = (
                f"新增生产 property 接口 `{change.symbol}`，必须完成接口必要性审判。"
            )
        if promoted_from:
            message = (
                f"生产接口 `{promoted_from}` 被提升为 public `{change.symbol}`，"
                "属于函数权限与接口契约变更。"
            )
        return [
            Finding(
                code="QG180",
                severity="warning",
                confidence="high",
                path=change.path,
                line=symbol.line if symbol is not None else 1,
                column=1,
                symbol=change.symbol,
                message=message,
                suggestion=(
                    "先尝试删除、内联、合并或复用；保留时必须逐项证明绝对必要性、"
                    "不可替代性、唯一职责、调用链和新鲜验证。private→public 与 @property "
                    "不能用‘方便调用’解释。"
                    if is_addition
                    else "逐项证明为何绝对不能保持原接口，核对全部调用方、兼容性、"
                    "README/Prompt/配置与测试，并给出不可替代方案和新鲜验证。"
                ),
                evidence={
                    "interface_change": change.change,
                    "interface_kind": change.kind,
                    "details": change.details,
                    "visibility_promotion_from": promoted_from,
                    "property_added": property_added,
                    "property_decorator_changed": property_changed,
                    "qg179_exempt": True,
                    "governed_by": "QG181"
                    if is_addition
                    else "report-interface-review",
                },
            )
        ]


class InterfaceDefinitionGrowthRule(CoreQualityRule[InterfaceGrowthContext]):
    """Detect a net increase in production interface declarations.

    This owner consumes the already partitioned added and removed interface
    facts; it does not infer the architectural necessity of an addition.
    """

    code = "QG181"

    def evaluate(self, context: InterfaceGrowthContext) -> list[Finding]:
        """Flag growth in the total number of declared production interfaces.

        A zero or negative net delta is not QG181; positive growth produces one
        aggregate review item with the exact added and removed symbol ledger.

        Args:
            context: Frozen added and removed declarations from the Git diff.

        Returns:
            One QG181 warning with growth counts when additions exceed
            removals; otherwise an empty list.
        """
        added = context.added
        removed = context.removed
        interface_net = len(added) - len(removed)
        if interface_net <= 0:
            return []
        first = added[0]
        first_symbol = first.after or first.before
        return [
            Finding(
                code="QG181",
                severity="warning",
                confidence="high",
                path=first.path,
                line=first_symbol.line if first_symbol is not None else 1,
                column=1,
                symbol="repository.interface_definition_balance",
                message=(
                    "生产函数/方法/类净增加，触发接口膨胀人工复核："
                    f"新增 {len(added)}，删除 {len(removed)}，净额 +{interface_net}。"
                ),
                suggestion=(
                    "优先继续删除、内联、合并或复用；确需保留时必须逐项完成必要性审判，"
                    "并由用户人工确认后再决定是否提交。删除项无需解释删除理由。"
                ),
                evidence={
                    "added": [
                        f"{item.kind}:{item.path}:{item.symbol}" for item in added
                    ],
                    "removed": [
                        f"{item.kind}:{item.path}:{item.symbol}" for item in removed
                    ],
                    "added_count": len(added),
                    "removed_count": len(removed),
                    "net": interface_net,
                    "qg179_exempt": True,
                },
            )
        ]
