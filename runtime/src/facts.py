from __future__ import annotations

import ast
import re
from itertools import chain
from dataclasses import dataclass, field
from pathlib import Path

from .ast_utils import (
    MAPPING_NAME_HINTS,
    ComplexityVisitor,
    annotation_is_mapping,
    assigned_names,
    decorator_names,
    dotted_name,
    is_constant_default,
    is_lookup_mapping_name,
    is_trivial_wrapper,
    normalized_function_body,
)
from .config import GuardConfig, is_test_path
from .docstrings import definition_code_lines, function_parameter_names, parse_docstring
from .model import Confidence, Definition, Finding, Severity, Usage
from .topology_facts import (
    ContractFact,
    ContractOwnership,
    RepositoryTopology,
    UsageKind,
)

DICT_DEFAULT_ARG_COUNT = 2
GETATTR_DEFAULT_ARG_COUNT = 3
MANUAL_DICT_PROJECTION_MIN_FIELDS = 3
TINY_METHOD_MAX_LINES = 5
BEST_EFFORT_FUNCTION_MARKERS = (
    "best_effort",
    "health",
    "ping",
    "probe",
    "safe_",
    "try_",
)
BEST_EFFORT_SPECIAL_METHODS = {"__aexit__", "__del__", "__exit__"}
TERMINATING_STATEMENTS = (ast.Return, ast.Raise, ast.Break, ast.Continue)
SETDEFAULT_AGGREGATION_METHODS = {"add", "append", "extend", "update"}
UNCHANGED_CN = "保持" + "不变"
PREVIOUSLY_EQUAL_CN = "与此前" + "一致"
LEGACY_COMPATIBLE_CN = "兼容" + "旧版"
BACKWARD_COMPATIBLE_CN = "向后" + "兼容"
UNCHANGED_EN = "un" + "changed"
BACKWARD_COMPATIBLE_EN = "backward" + " compatible"
SAME_AS_BEFORE_EN = "same as" + " before"
CONTRACT_COMPARISON_PATTERNS = (
    re.compile(
        rf"(?:返回|接口|字段|结构|格式|契约|行为).{{0,16}}"
        rf"(?:{UNCHANGED_CN}|{PREVIOUSLY_EQUAL_CN}|{LEGACY_COMPATIBLE_CN}|{BACKWARD_COMPATIBLE_CN})"
    ),
    re.compile(
        rf"(?:{UNCHANGED_CN}|{PREVIOUSLY_EQUAL_CN}|{LEGACY_COMPATIBLE_CN}|{BACKWARD_COMPATIBLE_CN})"
        rf".{{0,16}}(?:返回|接口|字段|结构|格式|契约|行为)"
    ),
    re.compile(
        rf"\b(?:return|interface|schema|contract|behavior).{{0,24}}"
        rf"(?:{UNCHANGED_EN}|{BACKWARD_COMPATIBLE_EN}|{SAME_AS_BEFORE_EN})\b",
        re.IGNORECASE,
    ),
)


def mapping_subscript(node: ast.expr) -> tuple[str, str] | None:
    """提取固定字符串键的映射下标访问。

    Args:
        node: 待分析表达式。

    Returns:
        命中时返回映射接收者和字段名，否则返回 None。
    """
    if not isinstance(node, ast.Subscript):
        return None
    key_node = node.slice
    if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
        return None
    receiver_name = dotted_name(node.value)
    if not receiver_name:
        return None
    return receiver_name, key_node.value


def mapping_membership_guard(node: ast.expr) -> tuple[str, str, bool] | None:
    """提取固定字符串键的映射成员判断。

    Args:
        node: 待分析条件表达式。

    Returns:
        命中时返回映射接收者、字段名和条件为存在判断与否。
    """
    if not (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and len(node.comparators) == 1
        and isinstance(node.left, ast.Constant)
        and isinstance(node.left.value, str)
        and isinstance(node.ops[0], (ast.In, ast.NotIn))
    ):
        return None
    receiver_node: ast.expr = node.comparators[0]
    if (
        isinstance(receiver_node, ast.Call)
        and isinstance(receiver_node.func, ast.Attribute)
        and receiver_node.func.attr == "keys"
        and not receiver_node.args
        and not receiver_node.keywords
    ):
        receiver_node = receiver_node.func.value
    receiver_name = dotted_name(receiver_node)
    if not receiver_name:
        return None
    return receiver_name, node.left.value, isinstance(node.ops[0], ast.In)


def is_contract_fallback_value(node: ast.expr) -> bool:
    """判断表达式是否为常见的契约兜底值。

    Args:
        node: 待分析表达式。

    Returns:
        表达式为常量、空容器或零参数容器构造时返回 True。
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Dict):
        return not node.keys
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return not node.elts
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"dict", "list", "set", "tuple"}
        and not node.args
        and not node.keywords
    )


def simple_assignment(statement: ast.stmt) -> tuple[str, ast.expr] | None:
    """提取单目标赋值语句的目标签名和值。

    Args:
        statement: 待分析语句。

    Returns:
        命中时返回目标 AST 签名和值表达式，否则返回 None。
    """
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        return ast.dump(statement.targets[0], include_attributes=False), statement.value
    if isinstance(statement, ast.AnnAssign) and statement.value is not None:
        return ast.dump(statement.target, include_attributes=False), statement.value
    return None


def guarded_mapping_fallback(
    guard_node: ast.expr,
    present_value: ast.expr,
    missing_value: ast.expr,
) -> tuple[str, str] | None:
    """识别成员判断后读取同一字段并回退默认值的语义模式。

    Args:
        guard_node: 映射成员判断表达式。
        present_value: 字段存在分支使用的表达式。
        missing_value: 字段缺失分支使用的表达式。

    Returns:
        命中时返回映射接收者和字段名，否则返回 None。
    """
    guard = mapping_membership_guard(guard_node)
    access = mapping_subscript(present_value)
    if guard is None or access is None or not is_contract_fallback_value(missing_value):
        return None
    receiver_name, field_name, _ = guard
    return access if access == (receiver_name, field_name) else None


def contract_comparison_claim(text: str) -> str:
    """返回字符串中未绑定明确基线的契约比较性声明片段。

    Args:
        text: 待分析字符串。

    Returns:
        命中时返回匹配片段，否则返回空字符串。
    """
    for pattern in CONTRACT_COMPARISON_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return match.group(0)
    return ""


def first_unreachable_statement(statements: list[ast.stmt]) -> ast.stmt | None:
    """返回同一语句块中首个明显不可达语句。

    Args:
        statements: 同一控制流语句块内的语句序列。

    Returns:
        终止语句后的第一条普通语句；没有明显不可达语句时返回 None。
    """
    terminated = False
    for statement in statements:
        if terminated:
            return statement
        terminated = isinstance(statement, TERMINATING_STATEMENTS)
    return None


def _literal_comparison_truth(node: ast.Compare) -> bool | None:
    """计算两个纯字面量操作数的一元比较真值。

    Args:
        node: 仅包含一个比较操作符的 Compare 节点。

    Returns:
        可证明时返回比较结果，不支持或包含动态值时返回 None。
    """
    left_allowed = all(
        isinstance(child, (ast.Constant, ast.List, ast.Tuple, ast.Load))
        for child in ast.walk(node.left)
    )
    right_node = node.comparators[0]
    right_allowed = all(
        isinstance(child, (ast.Constant, ast.List, ast.Tuple, ast.Load))
        for child in ast.walk(right_node)
    )
    if not left_allowed or not right_allowed:
        return None
    left = ast.literal_eval(node.left)
    right = ast.literal_eval(right_node)
    operation = node.ops[0]
    comparators = {
        ast.Eq: lambda: left == right,
        ast.NotEq: lambda: left != right,
        ast.In: lambda: left in right,
        ast.NotIn: lambda: left not in right,
        ast.Lt: lambda: left < right,
        ast.LtE: lambda: left <= right,
        ast.Gt: lambda: left > right,
        ast.GtE: lambda: left >= right,
    }
    operation_type = type(operation)
    if operation_type in comparators:
        return bool(comparators[operation_type]())
    if not isinstance(operation, (ast.Is, ast.IsNot)):
        return None
    singleton_values = (None, True, False, Ellipsis)
    left_is_singleton = any(left is item for item in singleton_values)
    right_is_singleton = any(right is item for item in singleton_values)
    if not left_is_singleton or not right_is_singleton:
        return None
    return (left is right) == isinstance(operation, ast.Is)


def _static_truth_value(node: ast.expr) -> bool | None:
    """返回可由字面量表达式静态确定的真值。

    Args:
        node: 待判断的条件表达式。

    Returns:
        可证明时返回 True 或 False；包含名称、调用或其他动态行为时返回 None。
    """
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        elements = node.keys if isinstance(node, ast.Dict) else node.elts
        return bool(elements)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = _static_truth_value(node.operand)
        return None if operand is None else not operand
    if isinstance(node, ast.BoolOp):
        values = [_static_truth_value(value) for value in node.values]
        result: bool | None = None
        if isinstance(node.op, ast.And):
            if any(value is False for value in values):
                result = False
            elif all(value is True for value in values):
                result = True
        elif any(value is True for value in values):
            result = True
        elif all(value is False for value in values):
            result = False
        return result
    if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
        return _literal_comparison_truth(node)
    return None


def in_explicit_best_effort_scope(function_stack: list[str]) -> bool:
    """
    判断当前函数栈是否明确声明 best-effort 或清理语义。

    Args:
        function_stack: 当前词法函数名称栈。

    Returns:
        当前函数明确属于安全探测、健康检查或清理协议时返回 True。
    """
    if not function_stack:
        return False
    name = function_stack[-1].lower()
    return name in BEST_EFFORT_SPECIAL_METHODS or any(
        marker in name for marker in BEST_EFFORT_FUNCTION_MARKERS
    )


def manual_projection_field(
    key_node: ast.expr | None,
    value_node: ast.expr,
) -> tuple[str, str] | None:
    """
    提取形如 `"field": metadata.get("field")` 的投影字段。

    Args:
        key_node: 字典字面量的键节点。
        value_node: 字典字面量的值节点。

    Returns:
        命中时返回映射接收者和字段名，否则返回 None。
    """
    if not (
        isinstance(key_node, ast.Constant)
        and isinstance(key_node.value, str)
        and isinstance(value_node, ast.Call)
        and isinstance(value_node.func, ast.Attribute)
        and value_node.func.attr == "get"
        and len(value_node.args) == 1
        and not value_node.keywords
    ):
        return None
    field_node = value_node.args[0]
    if not (
        isinstance(field_node, ast.Constant)
        and isinstance(field_node.value, str)
        and key_node.value == field_node.value
    ):
        return None
    receiver_name = dotted_name(value_node.func.value)
    if not receiver_name:
        return None
    return receiver_name, key_node.value


def manual_dict_projection(
    node: ast.Dict,
    mapping_names: set[str],
) -> tuple[str, list[str], Confidence] | None:
    """
    识别从同一映射逐字段 .get() 构造新 dict 的重复投影。

    Args:
        node: 待分析的字典字面量。
        mapping_names: 当前作用域中已确认的映射变量名称。

    Returns:
        命中时返回接收者、字段列表和置信度，否则返回 None。
    """
    projected: dict[str, list[str]] = {}
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        field = manual_projection_field(key_node, value_node)
        if field is None:
            continue
        receiver_name, field_name = field
        if receiver_name not in projected:
            projected[receiver_name] = []
        projected[receiver_name].append(field_name)
    for receiver_name, fields in projected.items():
        if len(fields) < MANUAL_DICT_PROJECTION_MIN_FIELDS:
            continue
        receiver_tail = receiver_name.rsplit(".", 1)[-1]
        if receiver_tail in mapping_names:
            return receiver_name, fields, "high"
        if receiver_tail in MAPPING_NAME_HINTS:
            return receiver_name, fields, "medium"
    return None


def is_decorator_inner_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    outer_function_name: str,
) -> bool:
    """
    判断嵌套函数是否属于标准 decorator wrapper 结构。

    Args:
        node: 当前嵌套函数定义。
        outer_function_name: 直接外层函数名称。

    Returns:
        明确属于 decorator/decorate 工厂返回的 wrapper 时返回 True。
    """
    outer = outer_function_name.lower()
    if "decorator" not in outer and "decorate" not in outer:
        return False
    if node.name not in {"wrapper", "wrapped", "inner"}:
        return False
    return any(
        isinstance(statement, ast.Return)
        and isinstance(statement.value, ast.Name)
        and statement.value.id == node.name
        for statement in ast.walk(node)
    )


@dataclass(slots=True)
class ModuleFacts:
    """
    保存单个 Python 模块解析得到的静态事实。

    该对象是后续调用解析和仓库规则评估的唯一模块级输入。
    """

    path: Path
    module: str
    source: str
    tree: ast.Module | None = None
    definitions: list[Definition] = field(default_factory=list)
    usages: list[Usage] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    imports: dict[str, str] = field(default_factory=dict)
    mapping_names: set[str] = field(default_factory=set)
    contracts: list[ContractFact] = field(default_factory=list)

    def make_finding(
        self,
        node: ast.AST,
        code: str,
        message: str,
        symbol: str,
        severity: Severity = "warning",
        confidence: Confidence = "high",
        suggestion: str = "",
        evidence: dict[str, object] | None = None,
    ) -> Finding:
        """构造属于当前模块与源码节点的质量发现。

        Args:
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
            severity=severity,
            confidence=confidence,
            path=str(self.path),
            line=node.lineno,
            column=node.col_offset + 1,
            message=message,
            symbol=symbol,
            suggestion=suggestion,
            evidence={} if evidence is None else evidence,
        )


@dataclass(slots=True, frozen=True)
class _MappingCallContext:
    """封装单次映射方法调用的静态接收者与契约所有权事实。"""

    receiver_name: str
    receiver_tail: str
    confirmed_mapping: bool
    mapping_name_hint: bool
    lookup_mapping: bool
    boundary_module: bool
    test_context: bool
    ownership: ContractOwnership
    ownership_confidence: Confidence
    ownership_evidence: tuple[str, ...]


class _FactsCollectorNodeVisitor(ast.NodeVisitor):
    """保存 AST 收集所需状态，并提供统一 finding 写入边界。"""

    def __init__(
        self,
        facts: ModuleFacts,
        config: GuardConfig,
        topology: RepositoryTopology | None = None,
    ) -> None:
        """
        初始化模块事实收集器。

        Args:
            facts: 当前模块事实或模块事实列表。
            config: 质量检查配置。
            topology: 可选的仓库级归一化拓扑，用于静态证明外部协议所有权。

        Returns:
            None。
        """
        self.facts = facts
        self.config = config
        self.topology = topology
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.mapping_scope_stack: list[set[str]] = [set()]
        self.typed_scope_stack: list[set[str]] = [set()]
        self.annotation_scope_stack: list[dict[str, str]] = [{}]
        self.external_class_bases: list[tuple[str, ...]] = []
        self.class_local_fields: list[set[str]] = []
        self.call_nodes: set[int] = set()
        self.diagnostic_string_nodes: set[int] = set()
        self.aggregation_setdefault_calls = (
            {
                id(inner)
                for candidate in ast.walk(facts.tree)
                if isinstance(candidate, ast.Call)
                and isinstance(candidate.func, ast.Attribute)
                and candidate.func.attr in SETDEFAULT_AGGREGATION_METHODS
                and isinstance((inner := candidate.func.value), ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "setdefault"
            }
            if facts.tree is not None
            else set()
        )

    @property
    def mapping_names(self) -> set[str]:
        """
        返回当前词法作用域可确认的映射变量名称。

        Returns:
            模块级与当前函数级映射变量名称集合。
        """
        names: set[str] = set()
        for scope_names in self.mapping_scope_stack:
            names.update(scope_names)
        return names

    @property
    def typed_names(self) -> set[str]:
        """返回当前词法作用域内具有显式静态类型的名称集合。

        Returns:
            模块级与当前函数级显式标注名称。
        """
        names: set[str] = set()
        for scope_names in self.typed_scope_stack:
            names.update(scope_names)
        return names

    @property
    def annotations(self) -> dict[str, str]:
        """返回当前词法作用域可见的显式类型标注。"""
        result: dict[str, str] = {}
        for scope_annotations in self.annotation_scope_stack:
            result.update(scope_annotations)
        return result

    def _resolve_imported_name(self, name: str) -> str:
        """把当前模块中的 import alias 展开为静态限定名。"""
        if not name:
            return ""
        root, dot, tail = name.partition(".")
        imported = self.facts.imports[root] if root in self.facts.imports else root
        return f"{imported}.{tail}" if dot else imported

    def _is_external_symbol(self, name: str) -> bool:
        """判断限定名是否由显式 import 指向当前项目包之外的依赖。"""
        if not name:
            return False
        local_root = name.split(".", 1)[0]
        if local_root not in self.facts.imports:
            return False
        resolved = self._resolve_imported_name(name)
        root = resolved.split(".", 1)[0]
        project_root = self.facts.module.split(".", 1)[0] if self.facts.module else ""
        if root in {"builtins", "collections", "typing"}:
            return False
        return bool(root and root != project_root)

    @staticmethod
    def _assigned_instance_fields(node: ast.ClassDef) -> set[str]:
        """返回类体方法中明确写入的 self/cls 属性名称。"""
        methods = (
            method
            for method in node.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        fields: set[str] = set()
        for child in chain.from_iterable(ast.walk(method) for method in methods):
            targets = (
                child.targets
                if isinstance(child, ast.Assign)
                else (child.target,)
                if isinstance(child, (ast.AnnAssign, ast.AugAssign))
                else ()
            )
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in {"self", "cls"}
                ):
                    fields.add(target.attr)
        return fields

    def _external_contract_candidate(
        self, receiver_name: str, selector: str
    ) -> tuple[str, ...]:
        """返回可能属于外部契约但尚未获得依赖解析证明的线索。"""
        receiver_root = receiver_name.split(".", 1)[0] if receiver_name else ""
        annotation = self.annotations.get(receiver_root, "")
        if annotation and self._is_external_symbol(annotation):
            return (
                f"external_annotation_candidate:{self._resolve_imported_name(annotation)}",
            )
        if receiver_root not in {"self", "cls"} or not self.external_class_bases:
            return ()
        if not self.external_class_bases[-1]:
            return ()
        field = selector
        if receiver_name.startswith(("self.", "cls.")):
            field = receiver_name.split(".", 1)[1].split(".", 1)[0]
        if self.class_local_fields and field in self.class_local_fields[-1]:
            return ()
        return tuple(
            f"external_base_candidate:{base}" for base in self.external_class_bases[-1]
        )

    def _resolved_external_contract_evidence(self) -> tuple[str, ...]:
        """从 normalized topology 返回当前类已解析的外部继承契约。"""
        if self.topology is None or not self.class_stack:
            return ()
        class_qualname = ".".join(self.class_stack)
        class_id = (
            f"{self.facts.module}.{class_qualname}"
            if self.facts.module
            else class_qualname
        )
        return tuple(
            f"resolved_external_base:{edge.target_id.removeprefix('external:')}"
            for edge in self.topology.outgoing(class_id, (UsageKind.INHERITANCE,))
            if edge.target_id.startswith("external:")
        )

    def _contract_ownership(
        self,
        receiver_name: str,
        selector: str = "",
        confirmed_mapping: bool = False,
        statically_typed: bool = False,
    ) -> tuple[ContractOwnership, Confidence, tuple[str, ...]]:
        """按静态 owner 证据分类一次运行时契约访问。"""
        if self._is_contract_boundary_module():
            return ContractOwnership.DYNAMIC_BOUNDARY, "high", ("boundary_module",)
        external_candidate = self._external_contract_candidate(receiver_name, selector)
        if external_candidate:
            resolved_external = self._resolved_external_contract_evidence()
            if resolved_external:
                return (
                    ContractOwnership.EXTERNAL_OPTIONAL,
                    "high",
                    (*external_candidate, *resolved_external),
                )
            return ContractOwnership.UNKNOWN, "low", external_candidate
        if confirmed_mapping or statically_typed:
            evidence = (
                ("confirmed_mapping",) if confirmed_mapping else ("statically_typed",)
            )
            return ContractOwnership.INTERNAL_FORMAL, "high", evidence
        return ContractOwnership.UNKNOWN, "low", ()

    def _record_contract(
        self,
        node: ast.AST,
        receiver: str,
        operation: str,
        selector: str,
        ownership: ContractOwnership,
        confidence: Confidence,
        evidence: tuple[str, ...],
    ) -> None:
        """记录统一 ContractFact，供后续语言中立策略与报告使用。"""
        self.facts.contracts.append(
            ContractFact(
                path=self.facts.path,
                line=node.lineno,
                receiver=receiver,
                ownership=ownership,
                confidence=confidence,
                operation=operation,
                selector=selector,
                owner_id=(
                    f"{self.facts.module}.{self.scope}"
                    if self.facts.module and self.scope
                    else self.scope or self.facts.module
                ),
                evidence=evidence,
            )
        )

    @property
    def scope(self) -> str:
        """
        返回当前类与函数栈形成的限定作用域。

        Returns:
            当前限定作用域字符串。
        """
        return ".".join(self.class_stack + self.function_stack)

    def _is_contract_boundary_module(self) -> bool:
        """判断当前模块是否承担外部输入、配置或协议适配边界。

        Returns:
            模块名称包含配置的边界标记时返回 True。
        """
        module_parts = self.facts.module.lower().replace("-", "_").split(".")
        return any(
            marker.lower() in module_parts
            for marker in (
                *self.config.boundary_module_markers,
                *self.config.config_module_markers,
            )
        )

    def add_finding(
        self,
        node: ast.AST,
        code: str,
        message: str,
        severity: Severity = "warning",
        confidence: Confidence = "high",
        suggestion: str = "",
        evidence: dict[str, object] | None = None,
        symbol: str = "",
    ) -> None:
        """
        向当前模块追加一条源码定位明确的质量发现。

        Args:
            node: 待分析的 AST 节点。
            code: 规则代码。
            message: 面向使用者的问题说明。
            severity: 问题严重级别。
            confidence: 静态判断置信度。
            suggestion: 建议的人工审查或修复方向。
            evidence: 附加的结构化证据。
            symbol: 覆盖默认作用域的符号标识。

        Returns:
            None。
        """
        self.facts.findings.append(
            Finding(
                code=code,
                severity=severity,
                confidence=confidence,
                path=str(self.facts.path),
                line=node.lineno,
                column=node.col_offset + 1,
                message=message,
                symbol=symbol or self.scope,
                suggestion=suggestion,
                evidence={} if evidence is None else evidence,
            )
        )

    def _report_unreachable_blocks(
        self,
        statement_kind: str,
        blocks: list[tuple[str, list[ast.stmt]]],
    ) -> None:
        """报告控制流语句各分支中终止语句之后的不可达代码。

        Args:
            statement_kind: 控制流语句类型名称。
            blocks: 分支名称与语句列表。

        Returns:
            None。
        """
        for block_name, block in blocks:
            unreachable = first_unreachable_statement(block)
            if unreachable is None:
                continue
            self.add_finding(
                unreachable,
                "QG155",
                f"{statement_kind} 语句的 {block_name} 分支中存在终止语句后的不可达代码。",
                severity="error",
                confidence="high",
                suggestion="删除不可达代码，保留唯一真实执行路径。",
                evidence={"branch": block_name, "statement_kind": statement_kind},
            )


class _DefinitionFactsVisitor(_FactsCollectorNodeVisitor):
    """收集导入、定义、作用域与函数签名事实。"""

    def visit_Import(self, node: ast.Import) -> None:
        """
        记录普通 import 语句建立的本地名称映射。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            self.facts.imports[local_name] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """
        记录 from import 语句及相对导入目标。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        module_name = node.module or ""
        if node.level:
            package_parts = self.facts.module.split(".")
            if self.facts.path.name != "__init__.py":
                package_parts = package_parts[:-1]
            if node.level > 1:
                package_parts = package_parts[: -(node.level - 1)]
            module_name = ".".join(
                [*package_parts, module_name] if module_name else package_parts
            )
        if not module_name:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            self.facts.imports[local_name] = f"{module_name}.{alias.name}"

    def visit_Assign(self, node: ast.Assign) -> None:
        """
        记录通过普通赋值创建的映射变量。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        if isinstance(node.value, ast.Dict) or (
            isinstance(node.value, ast.Call)
            and dotted_name(node.value.func) in {"dict", "defaultdict"}
        ):
            for target in node.targets:
                self.mapping_scope_stack[-1].update(assigned_names(target))
        if isinstance(node.value, ast.Dict):
            projection = manual_dict_projection(node.value, self.mapping_names)
            if projection is not None:
                receiver_name, fields, confidence = projection
                self.add_finding(
                    node.value,
                    "QG153",
                    f"从映射 `{receiver_name}` 逐字段 .get() 手写构造新 dict，属于重复字段投影。",
                    severity="warning" if is_test_path(self.facts.path) else "critical",
                    confidence=confidence,
                    suggestion=(
                        "不要把 metadata/payload 等 dict 字段逐项抄写一遍；若需要完整传递，直接复用原 dict；"
                        "若必须裁剪字段，集中声明字段白名单并用索引或专门模型表达契约。"
                    ),
                    evidence={"receiver": receiver_name, "fields": fields},
                )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """
        记录具有映射类型标注的变量。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        target_names = assigned_names(node.target)
        if annotation_is_mapping(node.annotation):
            self.mapping_scope_stack[-1].update(target_names)
        self.typed_scope_stack[-1].update(target_names)
        annotation_name = dotted_name(node.annotation)
        if annotation_name:
            for target_name in target_names:
                self.annotation_scope_stack[-1][target_name] = annotation_name
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """
        收集类定义、方法数量和 docstring 信息。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        if self.class_stack or self.function_stack:
            owner = ".".join(self.class_stack + self.function_stack)
            self.add_finding(
                node,
                "QG154",
                f"类 `{node.name}` 定义在 `{owner}` 内部，会降低接口数量和边界审查的可见性。",
                severity="info",
                confidence="high",
                suggestion="新增嵌套类仍须在修改说明中全量披露；只有形成稳定共享契约时才提升到模块级。",
                evidence={"outer_scope": owner, "nested_class": node.name},
            )
        direct_methods = [
            child
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        public_methods = [
            method for method in direct_methods if not method.name.startswith("_")
        ]
        tiny_methods = [
            method
            for method in direct_methods
            if definition_code_lines(method) <= TINY_METHOD_MAX_LINES
        ]
        qualname = ".".join(self.class_stack + self.function_stack + [node.name])
        docstring = parse_docstring(node)
        self.facts.definitions.append(
            Definition(
                module=self.facts.module,
                qualname=qualname,
                name=node.name,
                kind="class",
                path=self.facts.path,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                column=node.col_offset + 1,
                code_line_count=definition_code_lines(node),
                decorators=decorator_names(node.decorator_list),
                bases=tuple(name for base in node.bases if (name := dotted_name(base))),
                direct_method_count=len(direct_methods),
                public_method_count=len(public_methods),
                tiny_method_count=len(tiny_methods),
                docstring_text=docstring.text,
                docstring_line_count=docstring.content_lines,
                docstring_sections=docstring.sections,
                documented_parameters=docstring.documented_parameters,
            )
        )
        external_bases = tuple(
            self._resolve_imported_name(base_name)
            for base in node.bases
            if (base_name := dotted_name(base)) and self._is_external_symbol(base_name)
        )
        self.class_stack.append(node.name)
        self.external_class_bases.append(external_bases)
        self.class_local_fields.append(self._assigned_instance_fields(node))
        for child in node.body:
            self.visit(child)
        self.class_local_fields.pop()
        self.external_class_bases.pop()
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """
        收集同步函数定义。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """
        收集异步函数定义。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """
        统一收集同步或异步函数的结构事实。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        qualname = ".".join(self.class_stack + self.function_stack + [node.name])
        parameter_count, boolean_flags = self._function_signature_metrics(node)
        parameter_names = function_parameter_names(node)
        docstring = parse_docstring(node)
        if self.function_stack and not is_decorator_inner_function(
            node, self.function_stack[-1]
        ):
            self.add_finding(
                node,
                "QG154",
                f"函数 `{node.name}` 定义在函数 `{self.function_stack[-1]}` 内部，会降低接口数量审查的可见性。",
                severity="info",
                confidence="high",
                suggestion=(
                    "新增嵌套函数仍须在修改说明中全量披露；仅在形成稳定共享契约时提升到模块级。"
                ),
                evidence={
                    "outer_function": self.function_stack[-1],
                    "nested_function": node.name,
                },
            )
        unreachable = first_unreachable_statement(node.body)
        if unreachable is not None:
            self.add_finding(
                unreachable,
                "QG155",
                f"函数 `{qualname}` 中存在 return/raise/break/continue 后的不可达代码。",
                severity="error",
                confidence="high",
                suggestion="删除不可达分支；如果是调试残留或兼容占位，应回到真实调用路径修复。",
                evidence={"function": qualname},
            )
        complexity = ComplexityVisitor()
        for statement in node.body:
            complexity.visit(statement)
        kind = "method" if self.class_stack else "function"
        class_qualname = ".".join(self.class_stack)
        class_bases = tuple(
            base
            for definition in reversed(self.facts.definitions)
            if definition.kind == "class" and definition.qualname == class_qualname
            for base in definition.bases
        )
        self.facts.definitions.append(
            Definition(
                module=self.facts.module,
                qualname=qualname,
                name=node.name,
                kind=kind,
                path=self.facts.path,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                column=node.col_offset + 1,
                code_line_count=definition_code_lines(node),
                decorators=decorator_names(node.decorator_list),
                bases=class_bases,
                parameter_count=parameter_count,
                parameter_names=parameter_names,
                boolean_flag_count=boolean_flags,
                branch_count=complexity.branches,
                max_nesting=complexity.max_nesting,
                wrapper_target=is_trivial_wrapper(node),
                body_fingerprint=normalized_function_body(node),
                nested=bool(self.function_stack),
                docstring_text=docstring.text,
                docstring_line_count=docstring.content_lines,
                docstring_sections=docstring.sections,
                documented_parameters=docstring.documented_parameters,
            )
        )
        arguments = (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        parameter_mappings = {
            argument.arg
            for argument in arguments
            if annotation_is_mapping(argument.annotation)
        }
        parameter_annotations = {
            argument.arg: annotation_name
            for argument in arguments
            if argument.annotation is not None
            and (annotation_name := dotted_name(argument.annotation))
        }
        typed_parameters = set(parameter_annotations)
        if self.class_stack:
            typed_parameters.update({"self", "cls"})
        if node.args.kwarg is not None:
            parameter_mappings.add(node.args.kwarg.arg)
            if node.args.kwarg.annotation is not None:
                typed_parameters.add(node.args.kwarg.arg)
                annotation_name = dotted_name(node.args.kwarg.annotation)
                if annotation_name:
                    parameter_annotations[node.args.kwarg.arg] = annotation_name
        self.function_stack.append(node.name)
        self.mapping_scope_stack.append(parameter_mappings)
        self.typed_scope_stack.append(typed_parameters)
        self.annotation_scope_stack.append(parameter_annotations)
        for child in node.body:
            self.visit(child)
        self.annotation_scope_stack.pop()
        self.typed_scope_stack.pop()
        self.mapping_scope_stack.pop()
        self.function_stack.pop()

    def _function_signature_metrics(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> tuple[int, int]:
        """
        统计函数参数数量、映射参数和布尔开关。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            参数总数与布尔开关数量。
        """
        arguments = (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        parameter_count = sum(
            argument.arg not in {"self", "cls"} for argument in arguments
        )
        parameter_count += int(node.args.vararg is not None) + int(
            node.args.kwarg is not None
        )
        positional_defaults = [None] * (
            len(node.args.args) - len(node.args.defaults)
        ) + list(node.args.defaults)
        argument_defaults = [
            *zip(node.args.args, positional_defaults, strict=True),
            *zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True),
        ]
        boolean_flags = 0
        for argument, default in argument_defaults:
            annotation_name = (
                dotted_name(argument.annotation)
                if argument.annotation is not None
                else ""
            )
            default_is_bool = isinstance(default, ast.Constant) and isinstance(
                default.value, bool
            )
            boolean_flags += int(annotation_name == "bool" or default_is_bool)
        return parameter_count, boolean_flags


class _ControlFlowFactsVisitor(_DefinitionFactsVisitor):
    """检查条件、循环、异常分支及不可达控制流。"""

    def visit_IfExp(self, node: ast.IfExp) -> None:
        """检查条件表达式是否以另一种语法继续猜测映射契约。

        Args:
            node: 条件表达式节点。

        Returns:
            None。
        """
        guard = mapping_membership_guard(node.test)
        if guard is not None:
            _, _, present_when_true = guard
            present_value = node.body if present_when_true else node.orelse
            missing_value = node.orelse if present_when_true else node.body
            fallback = guarded_mapping_fallback(node.test, present_value, missing_value)
            if fallback is not None:
                receiver_name, field_name = fallback
                receiver_tail = receiver_name.rsplit(".", 1)[-1]
                confirmed_mapping = receiver_tail in self.mapping_names
                self.add_finding(
                    node,
                    "QG157",
                    f"通过成员判断后读取 `{receiver_name}[{field_name!r}]` 并回退默认值，仍在消费方猜测字段契约。",
                    severity=(
                        "warning"
                        if is_test_path(self.facts.path)
                        else "info"
                        if self._is_contract_boundary_module()
                        else "critical"
                        if confirmed_mapping
                        else "error"
                    ),
                    confidence="high" if confirmed_mapping else "medium",
                    suggestion=(
                        "不要只替换访问语法来隐藏缺字段；内部必需字段应由生产方和类型契约保证并直接读取。"
                        "真正可选字段应在 schema/TypedDict/Pydantic 模型中显式表达，并在输入边界集中处理。"
                    ),
                    evidence={"receiver": receiver_name, "field": field_name},
                )
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        """检查常量条件、契约兜底和分支内部不可达语句。

        Args:
            node: if 语句节点。

        Returns:
            None。
        """
        guard = mapping_membership_guard(node.test)
        if guard is not None and len(node.body) == 1 and len(node.orelse) == 1:
            body_assignment = simple_assignment(node.body[0])
            else_assignment = simple_assignment(node.orelse[0])
            if body_assignment is not None and else_assignment is not None:
                body_target, body_value = body_assignment
                else_target, else_value = else_assignment
                if body_target == else_target:
                    _, _, present_when_true = guard
                    present_value = body_value if present_when_true else else_value
                    missing_value = else_value if present_when_true else body_value
                    fallback = guarded_mapping_fallback(
                        node.test, present_value, missing_value
                    )
                    if fallback is not None:
                        receiver_name, field_name = fallback
                        receiver_tail = receiver_name.rsplit(".", 1)[-1]
                        confirmed_mapping = receiver_tail in self.mapping_names
                        self.add_finding(
                            node,
                            "QG157",
                            f"分支赋值通过成员判断读取 `{receiver_name}[{field_name!r}]` 并回退默认值，仍在消费方猜测字段契约。",
                            severity=(
                                "warning"
                                if is_test_path(self.facts.path)
                                else "critical"
                                if confirmed_mapping
                                else "error"
                            ),
                            confidence="high" if confirmed_mapping else "medium",
                            suggestion=(
                                "把字段必需性或可选性写入正式 schema，并在唯一输入边界处理；"
                                "内部消费方不要重复维护缺字段兼容分支。"
                            ),
                            evidence={"receiver": receiver_name, "field": field_name},
                        )
        static_result = _static_truth_value(node.test)
        if static_result is not None:
            unreachable_branch = node.orelse if static_result else node.body
            location = unreachable_branch[0] if unreachable_branch else node
            branch_name = "else" if static_result else "body"
            self.add_finding(
                location,
                "QG155",
                f"if 条件可静态确定为 {static_result}，{branch_name} 分支不可达。",
                severity="error",
                confidence="high",
                suggestion="删除死分支，只保留真实执行路径。",
                evidence={
                    "static_result": static_result,
                    "unreachable_branch": branch_name,
                },
            )
        reachable_blocks = (
            [("body", node.body), ("orelse", node.orelse)]
            if static_result is None
            else [("body", node.body)]
            if static_result
            else [("orelse", node.orelse)]
        )
        self._report_unreachable_blocks("if", reachable_blocks)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        """检查静态恒真/恒假循环及循环块中的不可达代码。

        Args:
            node: while 语句节点。

        Returns:
            None。
        """
        static_result = _static_truth_value(node.test)
        if static_result is False:
            location = node.body[0] if node.body else node
            self.add_finding(
                location,
                "QG155",
                "while 条件可静态确定为 False，循环体永远不可达。",
                severity="error",
                confidence="high",
                suggestion="删除不会执行的循环体和废弃分支。",
                evidence={"static_result": False, "unreachable_branch": "body"},
            )
        elif static_result is True and node.orelse:
            self.add_finding(
                node.orelse[0],
                "QG155",
                "while 条件可静态确定为 True，循环正常结束后的 else 分支不可达。",
                severity="error",
                confidence="high",
                suggestion="删除不可达 else 分支；需要退出时使用可变化条件并明确生命周期。",
                evidence={"static_result": True, "unreachable_branch": "orelse"},
            )
        reachable_blocks = (
            [("body", node.body), ("orelse", node.orelse)]
            if static_result is None
            else [("body", node.body)]
            if static_result
            else [("orelse", node.orelse)]
        )
        self._report_unreachable_blocks("while", reachable_blocks)
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        """检查异常分支契约与 try 各块中的不可达代码。

        Args:
            node: 待分析的 try 语句节点。

        Returns:
            None。
        """
        try_assignment = (
            simple_assignment(node.body[0]) if len(node.body) == 1 else None
        )
        for handler in node.handlers:
            exception_names = (
                {dotted_name(item) for item in handler.type.elts}
                if isinstance(handler.type, ast.Tuple)
                else {dotted_name(handler.type)}
                if handler.type is not None
                else {""}
            )
            self._check_keyerror_fallback(handler, try_assignment, exception_names)
            self._check_exception_contracts(handler, exception_names)
        blocks = [
            ("body", node.body),
            ("orelse", node.orelse),
            ("finalbody", node.finalbody),
        ]
        blocks.extend(
            (f"handler[{index}]", handler.body)
            for index, handler in enumerate(node.handlers)
        )
        self._report_unreachable_blocks("try", blocks)
        self.generic_visit(node)

    def _check_keyerror_fallback(
        self,
        handler: ast.ExceptHandler,
        try_assignment: tuple[str, ast.expr] | None,
        exception_names: set[str],
    ) -> None:
        """检查 KeyError 捕获后把必需映射字段改成默认值的契约降级。"""
        if (
            try_assignment is None
            or len(handler.body) != 1
            or "KeyError" not in exception_names
        ):
            return
        fallback_assignment = simple_assignment(handler.body[0])
        if fallback_assignment is None:
            return
        try_target, try_value = try_assignment
        fallback_target, fallback_value = fallback_assignment
        access = mapping_subscript(try_value)
        if (
            try_target != fallback_target
            or access is None
            or not is_contract_fallback_value(fallback_value)
        ):
            return
        receiver_name, field_name = access
        receiver_tail = receiver_name.rsplit(".", 1)[-1]
        confirmed_mapping = receiver_tail in self.mapping_names
        self.add_finding(
            handler,
            "QG157",
            f"捕获 KeyError 后为 `{receiver_name}[{field_name!r}]` 回退默认值，仍在消费方猜测字段契约。",
            severity=(
                "warning"
                if is_test_path(self.facts.path)
                else "critical"
                if confirmed_mapping
                else "error"
            ),
            confidence="high" if confirmed_mapping else "medium",
            suggestion=(
                "在输入边界验证或建模字段可选性；内部必需字段缺失应显式失败，"
                "不要用异常分支维持隐式兼容。"
            ),
            evidence={"receiver": receiver_name, "field": field_name},
        )

    def _check_exception_contracts(
        self,
        handler: ast.ExceptHandler,
        exception_names: set[str],
    ) -> None:
        """检查宽泛异常、契约异常吞噬与 ImportError 兼容分支。"""
        broad = exception_names & {"", "BaseException", "Exception"}
        if broad:
            exception_name = next(
                name for name in ("", "BaseException", "Exception") if name in broad
            )
            swallowed = bool(handler.body) and all(
                is_constant_default(item) for item in handler.body
            )
            explicit_best_effort = in_explicit_best_effort_scope(self.function_stack)
            self.add_finding(
                handler,
                "QG007",
                "宽泛异常被捕获" + ("并转换为默认结果。" if swallowed else "。"),
                severity=(
                    "error" if swallowed and not explicit_best_effort else "warning"
                ),
                suggestion=(
                    "捕获可预期的具体异常；失败若影响契约，应记录上下文并显式传播。"
                    "显式 best-effort 接口也应限制捕获范围并保留诊断信息。"
                ),
                evidence={
                    "exception": exception_name or "bare except",
                    "swallowed": swallowed,
                    "explicit_best_effort": explicit_best_effort,
                },
            )
        contract_exceptions = exception_names & {
            "AttributeError",
            "KeyError",
            "TypeError",
        }
        if contract_exceptions:
            swallowed = bool(handler.body) and all(
                is_constant_default(item) for item in handler.body
            )
            if swallowed:
                self.add_finding(
                    handler,
                    "QG023",
                    "通过捕获契约类异常返回默认结果，可能隐藏字段、属性或类型错误。",
                    severity="warning",
                    confidence="high",
                    suggestion="在输入边界验证结构；内部契约失败应显式暴露，不要转成 None、空容器或常量。",
                    evidence={"exceptions": sorted(contract_exceptions)},
                )
        if exception_names & {"ImportError", "ModuleNotFoundError"}:
            has_fallback_import = any(
                isinstance(item, (ast.Import, ast.ImportFrom)) for item in handler.body
            )
            if has_fallback_import:
                self.add_finding(
                    handler,
                    "QG009",
                    "通过 ImportError 选择备用实现，形成运行时兼容分支。",
                    severity="info",
                    suggestion="确认是否仍需兼容旧依赖；不需要时删除备用路径并固定依赖。",
                )


class _UsageFactsVisitor(_ControlFlowFactsVisitor):
    """收集调用、名称、属性使用并检查调用契约。"""

    def visit_Constant(self, node: ast.Constant) -> None:
        """检查代码字符串中的无基线契约比较声明。

        Args:
            node: 常量节点。

        Returns:
            None。
        """
        if id(node) in self.diagnostic_string_nodes:
            return
        if isinstance(node.value, str):
            claim = contract_comparison_claim(node.value)
            if claim:
                self.add_finding(
                    node,
                    "QG158",
                    "字符串声称接口、返回结构或行为“保持不变/兼容”，但代码内没有可验证的 Git before/after 基线。",
                    severity="warning",
                    confidence="high",
                    suggestion=(
                        "运行时代码、prompt 和字段描述应直接陈述当前固定契约；"
                        "历史兼容性结论放入修改报告，并基于 diff-base、before/after 和上下游调用证据验证。"
                    ),
                    evidence={"claim": claim[:160]},
                )

    def visit_Call(self, node: ast.Call) -> None:
        """记录调用关系并执行调用表达式级规则。

        Args:
            node: 待分析的调用节点。

        Returns:
            None。
        """
        self.call_nodes.add(id(node.func))
        target = dotted_name(node.func)
        self._record_call_usage(node, target)
        self._check_manual_projection_arguments(node)
        self._check_dynamic_attribute_probe(node)
        if isinstance(node.func, ast.Attribute):
            self._check_attribute_call(node)
        self.generic_visit(node)

    def _record_call_usage(self, node: ast.Call, target: str) -> None:
        """记录调用引用，并标记诊断消息中的字符串常量。"""
        if target in {"self.add_finding", "Finding"}:
            diagnostic_arguments = [
                *node.args,
                *(keyword.value for keyword in node.keywords),
            ]
            for argument in diagnostic_arguments:
                for child in ast.walk(argument):
                    if isinstance(child, ast.Constant) and isinstance(child.value, str):
                        self.diagnostic_string_nodes.add(id(child))
        if not target:
            return
        base, _, name = target.rpartition(".")
        self.facts.usages.append(
            Usage(
                module=self.facts.module,
                path=self.facts.path,
                line=node.lineno,
                column=node.col_offset + 1,
                target=name or target,
                base=base,
                is_call=True,
                owner_class=".".join(self.class_stack),
                owner_qualname=".".join(self.class_stack + self.function_stack),
            )
        )

    def _check_manual_projection_arguments(self, node: ast.Call) -> None:
        """检查调用实参中逐字段复制映射的手写投影。"""
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            if not isinstance(argument, ast.Dict):
                continue
            projection = manual_dict_projection(argument, self.mapping_names)
            if projection is None:
                continue
            receiver_name, fields, confidence = projection
            self.add_finding(
                argument,
                "QG153",
                f"从映射 `{receiver_name}` 逐字段 .get() 手写构造新 dict，属于重复字段投影。",
                severity="warning" if is_test_path(self.facts.path) else "critical",
                confidence=confidence,
                suggestion=(
                    "不要把 metadata/payload 等 dict 字段逐项抄写一遍；若需要完整传递，直接复用原 dict；"
                    "若必须裁剪字段，集中声明字段白名单并用索引或专门模型表达契约。"
                ),
                evidence={"receiver": receiver_name, "fields": fields},
            )

    def _check_dynamic_attribute_probe(self, node: ast.Call) -> None:
        """检查 hasattr/getattr(default) 的契约所有权与运行时形状猜测。"""
        if not isinstance(node.func, ast.Name) or node.func.id not in {
            "hasattr",
            "getattr",
        }:
            return
        if node.func.id == "getattr" and len(node.args) < GETATTR_DEFAULT_ARG_COUNT:
            return
        test_context = is_test_path(self.facts.path)
        receiver_name = dotted_name(node.args[0]) if node.args else ""
        receiver_tail = receiver_name.rsplit(".", 1)[-1] if receiver_name else ""
        selector = (
            node.args[1].value
            if len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            else ""
        )
        statically_typed = (
            receiver_tail in self.typed_names
            or receiver_name.startswith(("self", "cls"))
        )
        ownership, confidence, ownership_evidence = self._contract_ownership(
            receiver_name, selector=selector, statically_typed=statically_typed
        )
        self._record_contract(
            node,
            receiver_name,
            node.func.id,
            selector,
            ownership,
            confidence,
            ownership_evidence,
        )
        if test_context:
            severity: Severity = "warning"
        elif ownership is ContractOwnership.INTERNAL_FORMAL:
            severity = "error"
        else:
            severity = "info"
        semantic_unknown = ownership is ContractOwnership.UNKNOWN
        common_evidence: dict[str, object] = {
            "receiver": receiver_name,
            "selector": selector,
            "statically_typed": statically_typed,
            "test_context": test_context,
            "contract_ownership": ownership.value,
            "ownership_evidence": list(ownership_evidence),
        }
        if semantic_unknown:
            common_evidence.update(
                {
                    "semantic_review_required": True,
                    "semantic_review_question": "Q4,Q6",
                    "semantic_review_kind": "contract-ownership",
                }
            )
        if node.func.id == "hasattr":
            self.add_finding(
                node,
                "QG005",
                (
                    "使用 hasattr() 探测内部正式对象字段，把明确类型契约降级为运行时猜测。"
                    if ownership is ContractOwnership.INTERNAL_FORMAL
                    else "hasattr() 探测位于外部/动态/未解析契约边界，保留所有权证据供复核。"
                ),
                severity=severity,
                confidence=confidence,
                suggestion=(
                    "内部正式对象应直接访问声明属性；多态使用 Protocol/ABC/联合类型或显式 adapter。"
                    "外部可选能力只有在依赖契约静态可证明时才可保留，unknown 不得反推为内部错误。"
                ),
                evidence=common_evidence,
            )
        else:
            self.add_finding(
                node,
                "QG006",
                (
                    "getattr(..., default) 在内部正式对象契约缺失时静默兜底。"
                    if ownership is ContractOwnership.INTERNAL_FORMAL
                    else "getattr(..., default) 位于外部/动态/未解析契约边界，保留所有权证据供复核。"
                ),
                severity=severity,
                confidence=confidence,
                suggestion=(
                    "内部正式对象应直接访问声明字段并让契约错误暴露；真正可选的外部字段必须由"
                    "静态依赖契约、Protocol/联合类型或唯一 adapter 明确表达。"
                ),
                evidence=common_evidence,
            )

    def _check_attribute_call(self, node: ast.Call) -> None:
        """
        分析属性调用的接收者与具体规则类型。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        attribute = node.func.attr
        receiver = node.func.value
        receiver_name = dotted_name(receiver)
        receiver_tail = receiver_name.rsplit(".", 1)[-1] if receiver_name else ""
        confirmed_mapping = (
            isinstance(receiver, ast.Dict) or receiver_tail in self.mapping_names
        )
        mapping_name_hint = receiver_tail in MAPPING_NAME_HINTS
        self._check_mapping_call(
            node,
            attribute,
            receiver_name,
            confirmed_mapping,
            mapping_name_hint,
        )
        self._check_special_attribute_call(node, dotted_name(node.func))

    def _check_mapping_call(
        self,
        node: ast.Call,
        attribute: str,
        receiver_name: str,
        confirmed_mapping: bool,
        mapping_name_hint: bool,
    ) -> None:
        """检查映射读取、聚合初始化与删除时的隐式兜底契约。"""
        receiver_tail = receiver_name.rsplit(".", 1)[-1]
        selector = (
            node.args[0].value
            if node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            else ""
        )
        ownership, ownership_confidence, ownership_evidence = self._contract_ownership(
            receiver_name,
            selector=selector,
            confirmed_mapping=confirmed_mapping,
            statically_typed=(
                receiver_tail in self.typed_names
                or receiver_name.startswith(("self.", "cls."))
            ),
        )
        context = _MappingCallContext(
            receiver_name=receiver_name,
            receiver_tail=receiver_tail,
            confirmed_mapping=confirmed_mapping,
            mapping_name_hint=mapping_name_hint,
            lookup_mapping=is_lookup_mapping_name(receiver_tail),
            boundary_module=self._is_contract_boundary_module(),
            test_context=is_test_path(self.facts.path),
            ownership=ownership,
            ownership_confidence=ownership_confidence,
            ownership_evidence=ownership_evidence,
        )
        if attribute == "get" and context.confirmed_mapping and context.lookup_mapping:
            return
        if attribute == "get":
            self._check_mapping_get(node, context)
        elif attribute == "setdefault":
            self._check_mapping_setdefault(node, context)
        elif attribute == "pop":
            self._check_mapping_pop(node, context)

    def _check_mapping_get(self, node: ast.Call, context: _MappingCallContext) -> None:
        """检查 mapping.get 是否在错误的契约 owner 上承担缺字段兜底。"""
        explicit_default = len(node.args) >= DICT_DEFAULT_ARG_COUNT
        selector = (
            node.args[0].value
            if node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            else ""
        )
        self._record_contract(
            node,
            context.receiver_name,
            "mapping_get_default" if explicit_default else "mapping_get",
            selector,
            context.ownership,
            context.ownership_confidence,
            context.ownership_evidence,
        )
        evidence: dict[str, object] = {
            "receiver": context.receiver_name,
            "selector": selector,
            "explicit_default": explicit_default,
            "confirmed_mapping": context.confirmed_mapping,
            "lookup_mapping": context.lookup_mapping,
            "boundary_module": context.boundary_module,
            "contract_ownership": context.ownership.value,
            "ownership_evidence": list(context.ownership_evidence),
        }
        if context.ownership is ContractOwnership.UNKNOWN:
            evidence.update(
                {
                    "semantic_review_required": True,
                    "semantic_review_question": "Q4,Q6",
                    "semantic_review_kind": "contract-ownership",
                }
            )
            self.add_finding(
                node,
                "QG026",
                "发现无法静态确认 owner 的 .get() 契约访问。",
                severity="warning" if context.test_context else "info",
                confidence=context.ownership_confidence,
                suggestion=(
                    "先确认 receiver 是内部正式 mapping、外部可选 API 还是动态边界；"
                    "证据不足时保持 unknown，不得仅凭变量名或 strict_get 猜成内部 schema。"
                ),
                evidence=evidence,
            )
            return
        code = "QG004" if explicit_default else "QG003"
        if context.test_context:
            severity: Severity = "warning"
        elif context.ownership is ContractOwnership.INTERNAL_FORMAL:
            severity = "critical"
        else:
            severity = "info"
        internal = context.ownership is ContractOwnership.INTERNAL_FORMAL
        self.add_finding(
            node,
            code,
            (
                "内部正式映射使用 .get() 兜底必需字段，可能把契约错误转换成正常路径。"
                if internal
                else ".get() 位于显式外部可选/动态边界；记录 owner 证据而不按内部 schema 失败处理。"
            ),
            severity=severity,
            confidence=context.ownership_confidence,
            suggestion=(
                "内部稳定映射的必需字段使用 [] 直接读取；真正可选字段在 TypedDict/Pydantic/"
                "Protocol 或外部依赖契约中显式声明，并在唯一 adapter/ingestion 边界归一化。"
            ),
            evidence=evidence,
        )

    def _check_mapping_setdefault(
        self, node: ast.Call, context: _MappingCallContext
    ) -> None:
        """检查 setdefault 是否把读取、兜底与状态写入混成一个隐式入口。"""
        if not (context.confirmed_mapping or self.config.strict_get):
            return
        if context.confirmed_mapping and context.lookup_mapping:
            return
        if id(node) in self.aggregation_setdefault_calls:
            return
        self.add_finding(
            node,
            "QG012",
            "setdefault() 同时承担读取、兜底和写入，容易隐藏状态归并副作用。",
            severity=(
                "warning"
                if context.test_context
                else "info"
                if context.boundary_module
                else "critical"
                if context.confirmed_mapping
                and context.receiver_tail in MAPPING_NAME_HINTS
                else "error"
            ),
            suggestion=(
                "不要把 setdefault() 机械展开成 `if key not in mapping: mapping[key] = default`。"
                "若缺键是合法的聚合/缓存语义，保留 setdefault 或封装到唯一状态入口；"
                "若字段按契约必需，直接索引并让缺失错误暴露。"
            ),
            confidence="high" if context.confirmed_mapping else "medium",
        )

    def _check_mapping_pop(self, node: ast.Call, context: _MappingCallContext) -> None:
        """检查 pop(key, default) 是否把必需字段缺失静默转换成正常删除。"""
        if len(node.args) < DICT_DEFAULT_ARG_COUNT:
            return
        if not (context.confirmed_mapping or self.config.strict_get):
            return
        if context.confirmed_mapping and context.lookup_mapping:
            return
        self.add_finding(
            node,
            "QG025",
            "映射 pop(key, default) 在删除字段时静默忽略缺失。",
            severity=(
                "warning"
                if context.test_context
                else "info"
                if context.boundary_module
                else "critical"
                if context.confirmed_mapping
                and context.receiver_tail in MAPPING_NAME_HINTS
                else "error"
            ),
            confidence="high" if context.confirmed_mapping else "medium",
            suggestion="若字段按契约必须存在，使用 pop(key)；真正可选字段必须在类型中显式声明。",
            evidence={"receiver": context.receiver_name},
        )

    def _check_special_attribute_call(self, node: ast.Call, full_name: str) -> None:
        """
        检查 suppress 与配置默认值等特殊调用。

        Args:
            node: 待分析的 AST 节点。
            full_name: 调用目标的完整点分名称。

        Returns:
            None。
        """
        if full_name == "contextlib.suppress":
            self.add_finding(
                node,
                "QG024",
                "contextlib.suppress() 会完全隐藏指定异常。",
                severity="warning",
                confidence="high",
                suggestion="确认异常确实可安全忽略；否则记录上下文或使用显式异常分支。",
            )
        if (
            full_name in {"os.getenv", "os.environ.get"}
            and len(node.args) >= DICT_DEFAULT_ARG_COUNT
        ):
            self.add_finding(
                node,
                "QG011",
                "配置读取提供默认值，部署缺项可能被静默掩盖。",
                suggestion="对必填配置使用强制读取并在启动阶段报错；仅为真正可选配置保留默认值。",
                evidence={"call": full_name},
            )

    def visit_Name(self, node: ast.Name) -> None:
        """
        记录不属于直接调用目标的名称引用。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        if isinstance(node.ctx, ast.Load) and id(node) not in self.call_nodes:
            self.facts.usages.append(
                Usage(
                    module=self.facts.module,
                    path=self.facts.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    target=node.id,
                    base="",
                    is_call=False,
                    owner_class=".".join(self.class_stack),
                    owner_qualname=".".join(self.class_stack + self.function_stack),
                )
            )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """
        记录不属于直接调用目标的属性引用。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        if isinstance(node.ctx, ast.Load) and id(node) not in self.call_nodes:
            base = dotted_name(node.value)
            self.facts.usages.append(
                Usage(
                    module=self.facts.module,
                    path=self.facts.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    target=node.attr,
                    base=base,
                    is_call=False,
                    owner_class=".".join(self.class_stack),
                    owner_qualname=".".join(self.class_stack + self.function_stack),
                )
            )
        self.generic_visit(node)


class FactsCollector(_UsageFactsVisitor, ast.NodeVisitor):
    """组合模块静态事实收集职责的公开 AST visitor。

    具体规则按定义/作用域、控制流和调用/使用三个内部 owner 分层实现；
    该类保持原有 ``ast.NodeVisitor`` 公共继承契约，只提供唯一公开构造入口。
    """
