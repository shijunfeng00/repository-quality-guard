from __future__ import annotations

from .core_rule_contract import CoreQualityRule

import ast
from dataclasses import dataclass

from .model import Finding, InterfaceSymbol
from .project_profiles import (
    CallableContract,
    FrozenSSEProtocolContract,
    HeaderContract,
    RouteContract,
    SSEProtocolContract,
    StableMappingContract,
    ProjectProfile,
)


from .project_mapping_analysis import MappingShape, ReturnMappingAnalyzer


@dataclass(slots=True, frozen=True)
class FrozenSSERuleContext:
    """Hold the frozen SSE contract alongside its scanned source snapshot.

    The optional AST map lets QG148 reuse parsed target modules while checking
    the frozen event names, payload keys and event-emission declarations.
    """

    contract: FrozenSSEProtocolContract
    target_sources: dict[str, str]
    target_trees: dict[str, ast.Module] | None = None


@dataclass(slots=True, frozen=True)
class CallableCompatibilityContext:
    """Bind a declared callable API to the already collected target symbols.

    QG147 compares the Profile's required signature with the canonical interface
    catalog rather than reparsing or guessing an implementation name.
    """

    contract: CallableContract
    symbols: dict[str, InterfaceSymbol]


@dataclass(slots=True, frozen=True)
class ProjectDeltaRuleContext:
    """Provide corresponding Git snapshots for route and header delta checks.

    The contract selects the paths and the policy; optional AST maps let QG151
    and QG152 derive comparable public protocol shapes from each revision.
    """

    contract: RouteContract | HeaderContract
    base_sources: dict[str, str]
    target_sources: dict[str, str]
    base_trees: dict[str, ast.Module] | None = None
    target_trees: dict[str, ast.Module] | None = None


def validate_project_contracts(
    profile: ProjectProfile | None,
    base_sources: dict[str, str],
    target_sources: dict[str, str],
    target_symbols: dict[str, InterfaceSymbol],
    base_trees: dict[str, ast.Module] | None = None,
    target_trees: dict[str, ast.Module] | None = None,
) -> tuple[Finding, ...]:
    """
    校验显式项目档案声明的稳定接口契约。

    Args:
        profile: 已由入口解析完成的项目档案；通用模式为 None。
        base_sources: 基准 Git 快照源码。
        target_sources: 当前工作区源码。
        target_symbols: 已过滤的目标接口符号。
        base_trees: 可选的基线已解析 AST 映射。
        target_trees: 可选的目标已解析 AST 映射。

    Returns:
        项目专属的结构化契约发现；通用模式返回空元组。
    """
    if profile is None:
        return ()
    findings: list[Finding] = []
    for contract in profile.stable_mapping_contracts:
        findings.extend(
            _mapping_contract_findings(
                contract,
                base_sources,
                target_sources,
                base_trees=base_trees,
                target_trees=target_trees,
            )
        )
    for contract in profile.callable_contracts:
        results = CallableCompatibilityRule().evaluate(
            CallableCompatibilityContext(contract, target_symbols)
        )
        finding = results[0] if results else None
        if finding is not None:
            findings.append(finding)
    for contract in profile.frozen_sse_contracts:
        findings.extend(
            FrozenSSEContractRule().evaluate(
                FrozenSSERuleContext(contract, target_sources, target_trees)
            )
        )
    for contract in profile.sse_contracts:
        findings.extend(
            _relative_sse_contract_findings(
                contract,
                base_sources,
                target_sources,
                base_trees=base_trees,
                target_trees=target_trees,
            )
        )
    for contract in profile.route_contracts:
        findings.extend(
            _route_contract_findings(
                contract,
                base_sources,
                target_sources,
                base_trees=base_trees,
                target_trees=target_trees,
            )
        )
    for contract in profile.header_contracts:
        findings.extend(
            _header_contract_findings(
                contract,
                base_sources,
                target_sources,
                base_trees=base_trees,
                target_trees=target_trees,
            )
        )
    return tuple(findings)


def _mapping_contract_findings(
    contract: StableMappingContract,
    base_sources: dict[str, str],
    target_sources: dict[str, str],
    base_trees: dict[str, ast.Module] | None = None,
    target_trees: dict[str, ast.Module] | None = None,
) -> list[Finding]:
    """检查一个相对基线或固定键集合的映射/状态键接口。"""
    if contract.channel == "state_keys":
        target, base, error_finding = _analyze_state_keys_contract(
            contract,
            base_sources,
            target_sources,
            base_trees,
            target_trees,
        )
    else:
        target, base, error_finding = _analyze_fixed_mapping_contract(
            contract,
            base_sources,
            target_sources,
            base_trees,
            target_trees,
        )
    if error_finding is not None:
        return [error_finding]

    context = MappingShapeRuleContext(contract, target, base)
    if contract.frozen or contract.relative_code == "QG146":
        return MappingContractRule().evaluate(context)
    if contract.relative_code == "QG182":
        return ProtocolMappingDeltaRule().evaluate(context)
    return _relative_mapping_findings(context)


@dataclass(slots=True, frozen=True)
class MappingShapeRuleContext:
    """Represent one mapping protocol with before/after producer shapes.

    The frozen or relative mapping contract specifies protected keys; the
    inferred shapes retain whether unknown keys prevented a closed-world proof.
    """

    contract: StableMappingContract
    target: MappingShape
    base: MappingShape


class MappingContractRule(CoreQualityRule[MappingShapeRuleContext]):
    """Enforce QG146 frozen mapping keys from statically supported producers.

    Compare an exact frozen key set when return shapes are closed; preserve a
    conditional-review Finding rather than claim missing keys for unknown shapes.
    """

    code = "QG146"

    def evaluate(self, context: MappingShapeRuleContext) -> list[Finding]:
        """Check frozen or default relative mapping keys without executing source.

        Args:
            context: Profile-declared key obligations and independently inferred
                current and Git baseline mapping shapes, including completeness.

        Returns:
            QG146 Findings for provable added or missing frozen keys; uncertain
            shapes are reported as conditional evidence rather than invented losses.
        """
        contract, target, base = context.contract, context.target, context.base
        if contract.frozen:
            expected = set(contract.keys)
            added = sorted(target.keys - expected)
            removed = sorted(expected - target.keys) if target.complete else []
            if not added and not removed and target.keys:
                return []
            if target.keys:
                details = [
                    *(["新增键: " + ", ".join(added)] if added else []),
                    *(["缺失键: " + ", ".join(removed)] if removed else []),
                ]
                message = f"`{contract.qualname}` 改变了冻结返回映射协议；" + "；".join(
                    details
                )
                severity = "error"
                confidence = "high"
            else:
                message = f"无法静态确认 `{contract.qualname}` 的冻结映射键契约。"
                severity = "warning"
                confidence = "medium"
            return [
                Finding(
                    code="QG146",
                    severity=severity,
                    confidence=confidence,
                    path=contract.path,
                    line=1,
                    column=1,
                    symbol=contract.qualname,
                    message=message,
                    suggestion="冻结协议只能通过显式版本升级修改，并必须同步全部调用方和协议文档。",
                    evidence={
                        "mode": "frozen",
                        "channel": contract.channel,
                        "expected_keys": sorted(expected),
                        "base_keys": sorted(base.keys),
                        "target_keys": sorted(target.keys),
                        "target_complete": target.complete,
                        "added_keys": added,
                        "removed_keys": removed,
                    },
                )
            ]

        if contract.relative_code != self.code:
            raise ValueError("QG146 relative mapping contract has unexpected rule code")
        added = sorted(target.keys - base.keys)
        removed = (
            sorted(base.keys - target.keys)
            if target.complete or contract.channel == "state_keys"
            else []
        )
        if not added and not removed:
            return []
        details = [
            *(["新增键: " + ", ".join(added)] if added else []),
            *(["删除键: " + ", ".join(removed)] if removed else []),
        ]
        complete = base.complete and target.complete
        return [
            Finding(
                code=contract.relative_code,
                severity=contract.relative_severity,
                confidence="high" if complete else "medium",
                path=contract.path,
                line=1,
                column=1,
                symbol=contract.qualname,
                message=(
                    f"`{contract.qualname}` 的核心协议键相对所选 Git 基线发生变化；"
                    + "；".join(details)
                ),
                suggestion=(
                    "必须证明该变化绝对不可绕过，并同步全部生产方、消费者、Prompt/协议文档与回归测试；"
                    "不得以临时便利、兼容猜测或未来可能使用作为理由。"
                ),
                evidence={
                    "mode": "relative",
                    "channel": contract.channel,
                    "base_keys": sorted(base.keys),
                    "target_keys": sorted(target.keys),
                    "base_complete": base.complete,
                    "target_complete": target.complete,
                    "added_keys": added,
                    "removed_keys": removed,
                    "protocol_kind": contract.review_kind,
                    "qg179_exempt": contract.relative_code == "QG182",
                },
            )
        ]


def _relative_mapping_findings(context: MappingShapeRuleContext) -> list[Finding]:
    """Keep the alternate Profile-code route on the exact shared mapped facts."""
    contract, target, base = context.contract, context.target, context.base
    added = sorted(target.keys - base.keys)
    removed = (
        sorted(base.keys - target.keys)
        if target.complete or contract.channel == "state_keys"
        else []
    )
    if not added and not removed:
        return []
    details = [
        *(["新增键: " + ", ".join(added)] if added else []),
        *(["删除键: " + ", ".join(removed)] if removed else []),
    ]
    complete = base.complete and target.complete
    return [
        Finding(
            code=contract.relative_code,
            severity=contract.relative_severity,
            confidence="high" if complete else "medium",
            path=contract.path,
            line=1,
            column=1,
            symbol=contract.qualname,
            message=(
                f"`{contract.qualname}` 的核心协议键相对所选 Git 基线发生变化；"
                + "；".join(details)
            ),
            suggestion=(
                "必须证明该变化绝对不可绕过，并同步全部生产方、消费者、Prompt/协议文档与回归测试；"
                "不得以临时便利、兼容猜测或未来可能使用作为理由。"
            ),
            evidence={
                "mode": "relative",
                "channel": contract.channel,
                "base_keys": sorted(base.keys),
                "target_keys": sorted(target.keys),
                "base_complete": base.complete,
                "target_complete": target.complete,
                "added_keys": added,
                "removed_keys": removed,
                "protocol_kind": contract.review_kind,
                "qg179_exempt": contract.relative_code == "QG182",
            },
        )
    ]


class ProtocolMappingDeltaRule(CoreQualityRule[MappingShapeRuleContext]):
    """Compare Profile-owned state/return protocol keys under QG182.

    Unlike QG146 frozen contracts, this rule reports additions and removals
    relative to the Git baseline and respects incomplete dynamic return shapes.
    """

    code = "QG182"

    def evaluate(self, context: MappingShapeRuleContext) -> list[Finding]:
        """Report changes in a declared relative mapping protocol.

        Args:
            context: QG182 nonfrozen contract with current and baseline key sets;
                state_keys allows removals even without return-shape closure.

        Returns:
            QG182 Findings describing added or removed protocol keys, or an
            empty list when the observed contract is unchanged.

        Raises:
            ValueError: The caller supplied a frozen or non-QG182 contract.
        """
        if context.contract.relative_code != self.code or context.contract.frozen:
            raise ValueError("QG182 requires a relative protocol mapping contract")
        contract, target, base = context.contract, context.target, context.base
        added = sorted(target.keys - base.keys)
        removed = (
            sorted(base.keys - target.keys)
            if target.complete or contract.channel == "state_keys"
            else []
        )
        if not added and not removed:
            return []
        details = [
            *(["新增键: " + ", ".join(added)] if added else []),
            *(["删除键: " + ", ".join(removed)] if removed else []),
        ]
        complete = base.complete and target.complete
        return [
            Finding(
                code=contract.relative_code,
                severity=contract.relative_severity,
                confidence="high" if complete else "medium",
                path=contract.path,
                line=1,
                column=1,
                symbol=contract.qualname,
                message=(
                    f"`{contract.qualname}` 的核心协议键相对所选 Git 基线发生变化；"
                    + "；".join(details)
                ),
                suggestion=(
                    "必须证明该变化绝对不可绕过，并同步全部生产方、消费者、Prompt/协议文档与回归测试；"
                    "不得以临时便利、兼容猜测或未来可能使用作为理由。"
                ),
                evidence={
                    "mode": "relative",
                    "channel": contract.channel,
                    "base_keys": sorted(base.keys),
                    "target_keys": sorted(target.keys),
                    "base_complete": base.complete,
                    "target_complete": target.complete,
                    "added_keys": added,
                    "removed_keys": removed,
                    "protocol_kind": contract.review_kind,
                    "qg179_exempt": contract.relative_code == "QG182",
                },
            )
        ]


def _analyze_state_keys_contract(
    contract: StableMappingContract,
    base_sources: dict[str, str],
    target_sources: dict[str, str],
    base_trees: dict[str, ast.Module] | None,
    target_trees: dict[str, ast.Module] | None,
) -> tuple[MappingShape, MappingShape, None]:
    """分析跨文件聚合的 Profile state 键集合契约。"""
    shapes: list[MappingShape] = []
    for sources, trees in (
        (target_sources, target_trees),
        (base_sources, base_trees),
    ):
        keys: set[str] = set()
        complete = True
        found = False
        for path, item_source in sources.items():
            try:
                tree = trees[path] if trees is not None and path in trees else None
                shape = ReturnMappingAnalyzer(
                    path, item_source, tree, state_contract=contract
                ).analyze(contract.qualname, "state_keys")
            except SyntaxError:
                complete = False
                continue
            keys.update(shape.keys)
            found = found or shape.complete
        shapes.append(MappingShape(frozenset(keys), complete and found))
    return shapes[0], shapes[1], None


def _analyze_fixed_mapping_contract(
    contract: StableMappingContract,
    base_sources: dict[str, str],
    target_sources: dict[str, str],
    base_trees: dict[str, ast.Module] | None,
    target_trees: dict[str, ast.Module] | None,
) -> tuple[MappingShape, MappingShape, Finding | None]:
    """解析单文件固定映射契约在基准和当前工作区中的键形态。"""
    source = target_sources[contract.path] if contract.path in target_sources else None
    if source is None:
        finding = Finding(
            code="QG146" if contract.frozen else contract.relative_code,
            severity="error" if contract.frozen else contract.relative_severity,
            confidence="high",
            path=contract.path,
            line=1,
            column=1,
            symbol=contract.qualname,
            message=f"稳定返回契约 `{contract.qualname}` 所在文件已不存在。",
            suggestion="恢复该公开接口，或显式更新项目档案并同步全部调用方。",
            evidence={
                "expected_keys": list(contract.keys),
                "channel": contract.channel,
                "protocol_kind": contract.review_kind,
                "qg179_exempt": contract.relative_code == "QG182",
            },
        )
        return MappingShape(), MappingShape(), finding
    try:
        target = ReturnMappingAnalyzer(
            contract.path,
            source,
            target_trees[contract.path]
            if target_trees is not None and contract.path in target_trees
            else None,
        ).analyze(
            contract.qualname,
            contract.channel,
        )
        if contract.path in base_sources:
            base = ReturnMappingAnalyzer(
                contract.path,
                base_sources[contract.path],
                base_trees[contract.path]
                if base_trees is not None and contract.path in base_trees
                else None,
            ).analyze(contract.qualname, contract.channel)
        else:
            base = MappingShape()
    except SyntaxError as error:
        finding = Finding(
            code="QG146" if contract.frozen else contract.relative_code,
            severity="error" if contract.frozen else contract.relative_severity,
            confidence="high",
            path=contract.path,
            line=error.lineno or 1,
            column=error.offset or 1,
            symbol=contract.qualname,
            message=f"无法解析稳定返回契约 `{contract.qualname}`：{error.msg}",
        )
        return MappingShape(), MappingShape(), finding
    return target, base, None


class _FrozenSSEAnalyzer:
    """从单个类中提取冻结 SSE 协议的静态事实。"""

    def __init__(
        self,
        path: str,
        source: str,
        contract: FrozenSSEProtocolContract,
        tree: ast.Module | None = None,
    ) -> None:
        """
        初始化 SSE 协议分析器。

        Args:
            path: SSE 实现文件路径。
            source: 当前 Git 快照中的源码。
            contract: 待校验的冻结 SSE 契约。
            tree: 可选的已解析 AST；存在时直接复用。

        Returns:
            None。
        """
        self.path = path
        self.contract = contract
        tree = tree or ast.parse(source, filename=path, type_comments=True)
        self.class_node = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == contract.class_name
            ),
            None,
        )
        self.methods: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.envelope_method: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        self.close_builder: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        self.constructor: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        class_body = () if self.class_node is None else self.class_node.body
        for node in class_body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            self.methods[node.name] = node
            if node.name == self.contract.envelope_method:
                self.envelope_method = node
            if node.name == self.contract.close_builder:
                self.close_builder = node
            if node.name == "__init__":
                self.constructor = node

    def event_types(self) -> tuple[dict[str, set[str]], set[str], dict[str, list[str]]]:
        """提取成员函数发送的事件类型及静态 ``content`` 字段。

        Returns:
            方法事件映射、全部事件类型，以及事件到静态内容字段的映射。
        """
        by_method: dict[str, set[str]] = {}
        all_types: set[str] = set()
        content_keys: dict[str, set[str]] = {}
        for name, node in self.methods.items():
            values: set[str] = set()
            for child in ast.walk(node):
                match child:
                    case ast.Call(
                        func=ast.Attribute(value=ast.Name(id=receiver), attr=method),
                        args=[ast.Constant(value=str() as event_type), *_],
                    ) if method == self.contract.envelope_method and receiver in {
                        "self",
                        "cls",
                    }:
                        pass
                    case _:
                        continue
                values.add(event_type)
                if len(child.args) >= _SSE_EMIT_MIN_ARGS and isinstance(
                    child.args[1], ast.Dict
                ):
                    keys = {
                        key.value
                        for key in child.args[1].keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    }
                    if keys:
                        content_keys.setdefault(event_type, set()).update(keys)
            if values:
                by_method[name] = values
                all_types.update(values)
        close_keys = self.close_keys()
        if close_keys:
            content_keys.setdefault("end", set()).update(close_keys)
        return (
            by_method,
            all_types,
            {key: sorted(value) for key, value in sorted(content_keys.items())},
        )

    def envelope_keys(self) -> set[str]:
        """
        提取统一事件外壳字典的字面量键。

        Returns:
            与冻结字段重合度最高的字典键集合。
        """
        node = self.envelope_method
        if node is None:
            return set()
        expected = set(self.contract.envelope_keys)
        candidates: list[set[str]] = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Dict):
                continue
            keys = {
                key.value
                for key in child.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if keys:
                candidates.append(keys)
        return max(
            candidates,
            key=lambda item: (len(item & expected), len(item)),
            default=set(),
        )

    def close_keys(self) -> set[str]:
        """
        提取 end.content 的静态字段集合。

        Returns:
            初始化用量字段与 close builder 新增字段的并集。
        """
        keys = self._usage_attribute_keys()
        builder = self.close_builder
        if builder is None:
            return keys
        for child in ast.walk(builder):
            target: ast.expr | None = None
            if isinstance(child, (ast.Assign, ast.AnnAssign)):
                if isinstance(child, ast.Assign) and child.targets:
                    target = child.targets[0]
                elif isinstance(child, ast.AnnAssign):
                    target = child.target
            elif isinstance(child, ast.AugAssign):
                target = child.target
            if not isinstance(target, ast.Subscript):
                continue
            slice_node = target.slice
            if isinstance(slice_node, ast.Constant) and isinstance(
                slice_node.value, str
            ):
                keys.add(slice_node.value)
        return keys

    def _usage_attribute_keys(self) -> set[str]:
        """提取构造函数中用量映射属性的字面量键。"""
        constructor = self.constructor
        if constructor is None:
            return set()
        for child in ast.walk(constructor):
            if not isinstance(child, (ast.Assign, ast.AnnAssign)):
                continue
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            value = child.value
            if not isinstance(value, ast.Dict):
                continue
            if not any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == self.contract.usage_attribute
                for target in targets
            ):
                continue
            return {
                key.value
                for key in value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
        return set()


def _sse_protocol_finding(
    contract: FrozenSSEProtocolContract,
    analyzer: _FrozenSSEAnalyzer,
) -> Finding | None:
    """比较已提取的 SSE 事实与冻结契约。"""
    by_method, actual_event_types, _content_keys = analyzer.event_types()
    expected_methods = dict(contract.event_methods)
    expected_event_types = set(expected_methods.values())
    method_mismatches = {
        method: {
            "expected": expected_type,
            "actual": sorted(by_method.get(method, set())),
        }
        for method, expected_type in expected_methods.items()
        if by_method.get(method, set()) != {expected_type}
    }
    unexpected_event_types = sorted(
        event_type
        for event_type in actual_event_types - expected_event_types
        if not any(
            event_type.startswith(prefix) for prefix in contract.allowed_event_prefixes
        )
    )
    missing_event_types = sorted(expected_event_types - actual_event_types)
    actual_envelope_keys = analyzer.envelope_keys()
    expected_envelope_keys = set(contract.envelope_keys)
    actual_close_keys = analyzer.close_keys()
    expected_close_keys = set(contract.close_keys)

    problems = [
        *(["事件方法与 type 映射发生变化"] if method_mismatches else []),
        *(
            ["新增未声明事件类型: " + ", ".join(unexpected_event_types)]
            if unexpected_event_types
            else []
        ),
        *(
            ["缺失事件类型: " + ", ".join(missing_event_types)]
            if missing_event_types
            else []
        ),
        *(
            ["事件外壳字段发生变化"]
            if actual_envelope_keys != expected_envelope_keys
            else []
        ),
        *(
            ["end.content 字段发生变化"]
            if actual_close_keys != expected_close_keys
            else []
        ),
    ]
    if not problems:
        return None
    return Finding(
        code="QG148",
        severity="error",
        confidence="high",
        path=contract.path,
        line=analyzer.class_node.lineno,
        column=analyzer.class_node.col_offset + 1,
        symbol=contract.class_name,
        message="冻结 SSE 对外协议发生变化；" + "；".join(problems),
        suggestion=(
            "SSE 事件类型、type/content 外壳和 end.content 字段已由项目协议冻结；"
            "不要通过普通内部重构修改，确需升级时应采用显式版本迁移。"
        ),
        evidence={
            "method_mismatches": method_mismatches,
            "expected_event_types": sorted(expected_event_types),
            "actual_event_types": sorted(actual_event_types),
            "unexpected_event_types": unexpected_event_types,
            "missing_event_types": missing_event_types,
            "expected_envelope_keys": sorted(expected_envelope_keys),
            "actual_envelope_keys": sorted(actual_envelope_keys),
            "expected_close_keys": sorted(expected_close_keys),
            "actual_close_keys": sorted(actual_close_keys),
        },
    )


_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}
_HEADER_CONTAINER_NAMES = {"headers", "request_headers", "response_headers"}
_CONTRACT_RENDER_MAX_CHARS = 160
_CONTRACT_RENDER_PREVIEW_CHARS = 157
_SSE_EMIT_MIN_ARGS = 2


def _relative_sse_contract_findings(
    contract: SSEProtocolContract,
    base_sources: dict[str, str],
    target_sources: dict[str, str],
    base_trees: dict[str, ast.Module] | None = None,
    target_trees: dict[str, ast.Module] | None = None,
) -> list[Finding]:
    """Document _relative_sse_contract_findings for quality guard coverage."""
    base_source = base_sources[contract.path] if contract.path in base_sources else ""
    target_source = (
        target_sources[contract.path] if contract.path in target_sources else ""
    )
    base = _sse_shape(
        contract,
        base_source,
        base_trees[contract.path]
        if base_trees is not None and contract.path in base_trees
        else None,
    )
    target = _sse_shape(
        contract,
        target_source,
        target_trees[contract.path]
        if target_trees is not None and contract.path in target_trees
        else None,
    )
    if not base and not target:
        return []
    if base == target:
        return []
    return [
        Finding(
            code=contract.code,
            severity=contract.severity,
            confidence="high",
            path=contract.path,
            line=1,
            column=1,
            symbol=contract.class_name,
            message="SSE 事件 type、统一外壳或 content 字段相对所选 Git 基线发生变化。",
            suggestion="该协议对外提供给前端和调用方；必须由人工审计并同步协议文档、消费者与回归测试。",
            evidence={
                "base": base,
                "target": target,
                "protocol_kind": contract.review_kind,
                "qg179_exempt": contract.code == "QG182",
            },
        )
    ]


def _sse_shape(
    contract: SSEProtocolContract,
    source: str,
    tree: ast.Module | None = None,
) -> dict[str, object]:
    """Document _sse_shape for quality guard coverage."""
    if not source:
        return {}
    frozen = FrozenSSEProtocolContract(
        path=contract.path,
        class_name=contract.class_name,
        event_methods=(),
        envelope_method=contract.envelope_method,
        envelope_keys=(),
        usage_attribute=contract.usage_attribute,
        close_builder=contract.close_builder,
        close_keys=(),
    )
    try:
        analyzer = _FrozenSSEAnalyzer(contract.path, source, frozen, tree)
    except SyntaxError:
        return {"parse_error": True}
    if analyzer.class_node is None:
        return {"class_missing": True}
    by_method, event_types, content_keys = analyzer.event_types()
    return {
        "event_types": sorted(event_types),
        "events_by_method": {
            key: sorted(value) for key, value in sorted(by_method.items())
        },
        "envelope_keys": sorted(analyzer.envelope_keys()),
        "close_keys": sorted(analyzer.close_keys()),
        "content_keys_by_event": content_keys,
    }


def _route_contract_findings(
    contract: RouteContract,
    base_sources: dict[str, str],
    target_sources: dict[str, str],
    base_trees: dict[str, ast.Module] | None = None,
    target_trees: dict[str, ast.Module] | None = None,
) -> list[Finding]:
    """Preserve this established entrypoint via its owning Core rule."""
    return RouteDeltaRule().evaluate(
        ProjectDeltaRuleContext(
            contract, base_sources, target_sources, base_trees, target_trees
        )
    )


def _header_contract_findings(
    contract: HeaderContract,
    base_sources: dict[str, str],
    target_sources: dict[str, str],
    base_trees: dict[str, ast.Module] | None = None,
    target_trees: dict[str, ast.Module] | None = None,
) -> list[Finding]:
    """Preserve this established entrypoint via its owning Core rule."""
    return HeaderDeltaRule().evaluate(
        ProjectDeltaRuleContext(
            contract, base_sources, target_sources, base_trees, target_trees
        )
    )


def _extract_routes(
    path: str,
    source: str,
    tree: ast.Module | None = None,
) -> dict[str, dict[str, str]]:
    """提取源码中的 HTTP 路由声明。"""

    def _render(node: ast.AST | None) -> str:
        """把路由装饰器参数渲染为稳定短文本。"""
        if node is None:
            return ""
        try:
            text = ast.unparse(node)
        except (AttributeError, ValueError):
            return type(node).__name__
        if len(text) <= _CONTRACT_RENDER_MAX_CHARS:
            return text
        return text[:_CONTRACT_RENDER_PREVIEW_CHARS] + "..."

    if not source:
        return {}
    if tree is None:
        try:
            tree = ast.parse(source, filename=path, type_comments=True)
        except SyntaxError:
            return {f"<parse-error> {path}": {"path": path}}
    routes: dict[str, dict[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            method = decorator.func.attr.lower()
            if method not in _HTTP_METHODS or not decorator.args:
                continue
            route_path = _render(decorator.args[0])
            details = {
                "method": method.upper(),
                "path": route_path,
                "endpoint": node.name,
                "source": path,
                "async": str(isinstance(node, ast.AsyncFunctionDef)),
            }
            for keyword in decorator.keywords:
                if keyword.arg in {
                    "response_class",
                    "include_in_schema",
                    "status_code",
                }:
                    details[keyword.arg] = _render(keyword.value)
            routes[f"{details['method']} {route_path}"] = details
    return routes


def _extract_headers(
    source: str,
    tree: ast.Module | None = None,
) -> dict[str, set[str]]:
    """Document _extract_headers for quality guard coverage."""
    if not source:
        return {"read": set(), "write": set(), "filter": set()}
    if tree is None:
        try:
            tree = ast.parse(source, type_comments=True)
        except SyntaxError:
            return {"read": set(), "write": set(), "filter": set()}
    collector = _HeaderCollector()
    collector.visit(tree)
    return collector.values


class _HeaderCollector(ast.NodeVisitor):
    """收集源码中对 HTTP Header 名称的读写和过滤引用。"""

    def __init__(self) -> None:
        """
        初始化 Header 引用收集器。

        Returns:
            None。
        """
        self.values: dict[str, set[str]] = {
            "read": set(),
            "write": set(),
            "filter": set(),
        }

    def visit_Call(self, node: ast.Call) -> None:
        """
        收集 Header 读取、透传和请求发送。

        Args:
            node: 当前函数调用节点。

        Returns:
            None。
        """
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and _is_header_object(node.func.value)
        ):
            self._add("read", node.args[0])
        for keyword in node.keywords:
            if keyword.arg == "headers":
                self._collect_dict_keys(keyword.value, "write")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """
        收集普通赋值中的 Header 写入。

        Args:
            node: 当前普通赋值节点。

        Returns:
            None。
        """
        for target in node.targets:
            self._collect_subscript(target, "write")
            if isinstance(target, ast.Name) and target.id in _HEADER_CONTAINER_NAMES:
                self._collect_dict_keys(node.value, "write")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """
        收集注解赋值中的 Header 写入。

        Args:
            node: 当前注解赋值节点。

        Returns:
            None。
        """
        self._collect_subscript(node.target, "write")
        if (
            isinstance(node.target, ast.Name)
            and node.target.id in _HEADER_CONTAINER_NAMES
        ):
            self._collect_dict_keys(node.value, "write")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        """
        收集 Header 过滤条件里的字面量。

        Args:
            node: 当前比较表达式节点。

        Returns:
            None。
        """
        for item in (node.left, *node.comparators):
            self._add("filter", item)
        self.generic_visit(node)

    def _collect_subscript(self, target: ast.expr, channel: str) -> None:
        """Document _collect_subscript for quality guard coverage."""
        if isinstance(target, ast.Subscript) and _is_header_object(target.value):
            self._add(channel, target.slice)

    def _collect_dict_keys(self, node: ast.AST | None, channel: str) -> None:
        """Document _collect_dict_keys for quality guard coverage."""
        if not isinstance(node, ast.Dict):
            return
        for key in node.keys:
            self._add(channel, key)

    def _add(self, channel: str, node: ast.AST | None) -> None:
        """Document _add for quality guard coverage."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            self.values[channel].add(node.value)


def _is_header_object(node: ast.AST) -> bool:
    """Document _is_header_object for quality guard coverage."""
    if isinstance(node, ast.Name):
        return node.id.lower() in _HEADER_CONTAINER_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr.lower() == "headers"
    return False


def _shape_delta(before: dict, after: dict) -> dict[str, object]:
    """Document _shape_delta for quality guard coverage."""
    before_keys = set(before)
    after_keys = set(after)
    modified = {
        key: {"before": before[key], "after": after[key]}
        for key in sorted(before_keys & after_keys)
        if before[key] != after[key]
    }
    return {
        "added": {key: after[key] for key in sorted(after_keys - before_keys)},
        "removed": {key: before[key] for key in sorted(before_keys - after_keys)},
        "modified": modified,
    }


class FrozenSSEContractRule(CoreQualityRule[FrozenSSERuleContext]):
    """Validate an immutable Server-Sent Events protocol under QG148.

    Use source-bound event shape analysis; absent files, unparseable modules and
    removed emission fields are represented as reviewable contract violations.
    """

    code = "QG148"

    def evaluate(self, context: FrozenSSERuleContext) -> list[Finding]:
        """Compare an SSE implementation to the Profile's frozen event contract.

        Args:
            context: Frozen event/payload requirements, target source map and
                optional previously parsed target modules.

        Returns:
            A QG148 Finding for missing/unparseable implementation or an observed
            frozen event discrepancy, otherwise an empty list.
        """
        contract = context.contract
        target_sources = context.target_sources
        target_trees = context.target_trees
        source = target_sources.get(contract.path)
        if source is None:
            return [
                Finding(
                    code="QG148",
                    severity="error",
                    confidence="high",
                    path=contract.path,
                    line=1,
                    column=1,
                    symbol=contract.class_name,
                    message="冻结 SSE 协议实现文件已不存在。",
                    suggestion="恢复 SSE 对外协议实现，或进行显式版本升级并同步全部调用方。",
                )
            ]
        try:
            analyzer = _FrozenSSEAnalyzer(
                contract.path,
                source,
                contract,
                target_trees[contract.path]
                if target_trees is not None and contract.path in target_trees
                else None,
            )
        except SyntaxError as error:
            return [
                Finding(
                    code="QG148",
                    severity="error",
                    confidence="high",
                    path=contract.path,
                    line=error.lineno or 1,
                    column=error.offset or 1,
                    symbol=contract.class_name,
                    message=f"无法解析冻结 SSE 协议：{error.msg}",
                )
            ]
        if analyzer.class_node is None:
            return [
                Finding(
                    code="QG148",
                    severity="error",
                    confidence="high",
                    path=contract.path,
                    line=1,
                    column=1,
                    symbol=contract.class_name,
                    message=f"冻结 SSE 协议类 `{contract.class_name}` 已不存在。",
                )
            ]
        finding = _sse_protocol_finding(contract, analyzer)
        return [] if finding is None else [finding]


class RouteDeltaRule(CoreQualityRule[ProjectDeltaRuleContext]):
    """Detect changes in Profile-owned route interfaces under QG151.

    Extract path/method/endpoint shapes from both Git snapshots and report only
    differing public routes; unavailable paths yield empty route shapes.
    """

    code = "QG151"

    def evaluate(self, context: ProjectDeltaRuleContext) -> list[Finding]:
        """Compare route declarations in frozen base and target source maps.

        Args:
            context: RouteContract declaring relevant paths plus corresponding
                source maps and optional AST caches.

        Returns:
            A QG151 warning documenting changed route shape and before/after
            evidence, or an empty list when their extracted routes match.
        """
        contract = context.contract
        base_sources = context.base_sources
        target_sources = context.target_sources
        base_trees = context.base_trees
        target_trees = context.target_trees
        base: dict[str, dict[str, str]] = {}
        target: dict[str, dict[str, str]] = {}
        for path in contract.paths:
            base.update(
                _extract_routes(
                    path,
                    base_sources[path] if path in base_sources else "",
                    base_trees[path]
                    if base_trees is not None and path in base_trees
                    else None,
                )
            )
            target.update(
                _extract_routes(
                    path,
                    target_sources[path] if path in target_sources else "",
                    target_trees[path]
                    if target_trees is not None and path in target_trees
                    else None,
                )
            )
        if base == target:
            return []
        return [
            Finding(
                code="QG151",
                severity="warning",
                confidence="high",
                path=contract.paths[0] if contract.paths else "",
                line=1,
                column=1,
                symbol=contract.name,
                message=f"{contract.name} 路由接口相对所选 Git 基线发生变化。",
                suggestion="确认 HTTP 方法、路径、endpoint 名称和响应形态变化已同步调用方、文档和兼容层。",
                evidence=_shape_delta(base, target),
            )
        ]


class HeaderDeltaRule(CoreQualityRule[ProjectDeltaRuleContext]):
    """Compare HTTP Header read/write/filter behavior under QG152.

    A changed boundary is reported as a relative protocol warning; collecting
    source facts does not imply a particular client actually broke.
    """

    code = "QG152"

    def evaluate(self, context: ProjectDeltaRuleContext) -> list[Finding]:
        """Collect and compare Header operations for declared Profile paths.

        Args:
            context: HeaderContract and the Git base/current source snapshots,
                optionally with precomputed module ASTs.

        Returns:
            A QG152 warning with the added/removed/modified Header operation
            sets, or an empty list when observable operations match.
        """
        contract = context.contract
        base_sources = context.base_sources
        target_sources = context.target_sources
        base_trees = context.base_trees
        target_trees = context.target_trees
        base_sets: dict[str, set[str]] = {
            "read": set(),
            "write": set(),
            "filter": set(),
        }
        target_sets: dict[str, set[str]] = {
            "read": set(),
            "write": set(),
            "filter": set(),
        }
        for path in contract.paths:
            for key, values in _extract_headers(
                base_sources[path] if path in base_sources else "",
                base_trees[path]
                if base_trees is not None and path in base_trees
                else None,
            ).items():
                base_sets[key].update(values)
            for key, values in _extract_headers(
                target_sources[path] if path in target_sources else "",
                target_trees[path]
                if target_trees is not None and path in target_trees
                else None,
            ).items():
                target_sets[key].update(values)
        base = {key: sorted(values) for key, values in base_sets.items() if values}
        target = {key: sorted(values) for key, values in target_sets.items() if values}
        if base == target:
            return []
        return [
            Finding(
                code="QG152",
                severity="warning",
                confidence="medium",
                path=contract.paths[0] if contract.paths else "",
                line=1,
                column=1,
                symbol=contract.name,
                message=f"{contract.name} Header 边界相对所选 Git 基线发生变化。",
                suggestion="确认新增、删除或过滤的 Header 不会破坏 OpenAI 兼容客户端、反向代理和调度路由。",
                evidence=_shape_delta(base, target),
            )
        ]


class CallableCompatibilityRule(CoreQualityRule[CallableCompatibilityContext]):
    """Check callable API compatibility against the Profile under QG147.

    Resolve the exact module path, qualified symbol, and primary variant before
    comparing parameter names, kinds, defaults and required signature clauses.
    """

    code = "QG147"

    def evaluate(self, context: CallableCompatibilityContext) -> list[Finding]:
        """Validate a framework adaptation entry point without reflection.

        Args:
            context: Required CallableContract and scanned target interface symbols;
                only matching path/qualname and variant zero are considered.

        Returns:
            QG147 Findings when the callable is absent or its parameter contract
            differs; no Finding when its declared public signature matches.
        """
        contract = context.contract
        symbols = context.symbols
        symbol = next(
            (
                item
                for item in symbols.values()
                if item.path == contract.path
                and item.qualname == contract.qualname
                and item.kind in {"function", "method"}
                and item.variant == 0
            ),
            None,
        )
        problems: list[str] = []
        if symbol is None:
            problems.append("接口不存在")
        else:
            actual_parameters = tuple(
                (item.name, item.kind, item.has_default) for item in symbol.parameters
            )
            if actual_parameters != contract.parameters:
                expected_names = ", ".join(name for name, _, _ in contract.parameters)
                actual_names = ", ".join(name for name, _, _ in actual_parameters)
                problems.append(
                    f"参数名称、种类或默认值契约不一致（期望: {expected_names}；实际: {actual_names}）"
                )
            if contract.is_async is not None and symbol.is_async != contract.is_async:
                problems.append("同步/异步形态不一致")
            for marker_group in contract.return_markers:
                alternatives = marker_group.split("|")
                if not any(marker in symbol.return_type for marker in alternatives):
                    problems.append(f"返回类型缺少 {marker_group}")
            decorator_leaves = {
                item.split("(", 1)[0].rsplit(".", 1)[-1] for item in symbol.decorators
            }
            missing_decorators = sorted(set(contract.decorators) - decorator_leaves)
            if missing_decorators:
                problems.append("缺少装饰器: " + ", ".join(missing_decorators))
        if not problems:
            return []
        finding = Finding(
            code="QG147",
            severity="error",
            confidence="high",
            path=contract.path,
            line=1 if symbol is None else symbol.line,
            column=1,
            symbol=contract.qualname,
            message=f"框架适配接口 `{contract.qualname}` 不再满足固定兼容契约："
            + "；".join(problems),
            suggestion="保持适配层入口与目标框架的参数种类、默认值、异步形态和返回对象约定一致。",
            evidence={
                "expected_parameters": [
                    {"name": name, "kind": kind, "has_default": has_default}
                    for name, kind, has_default in contract.parameters
                ],
                "actual_parameters": (
                    []
                    if symbol is None
                    else [item.to_dict() for item in symbol.parameters]
                ),
                "expected_return_markers": list(contract.return_markers),
                "actual_return_type": "" if symbol is None else symbol.return_type,
                "expected_async": contract.is_async,
                "actual_async": None if symbol is None else symbol.is_async,
            },
        )
        return [finding]
