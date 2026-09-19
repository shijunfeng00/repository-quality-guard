from __future__ import annotations

import ast
import re
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


class FactsCollector(ast.NodeVisitor):
    """
    遍历 Python AST 并收集定义、引用与局部规则发现。

    收集器不修改源码，只产生结构化事实和可定位的问题。
    """

    def __init__(self, facts: ModuleFacts, config: GuardConfig) -> None:
        """
        初始化模块事实收集器。

        Args:
            facts: 当前模块事实或模块事实列表。
            config: 质量检查配置。

        Returns:
            None。
        """
        self.facts = facts
        self.config = config
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.mapping_scope_stack: list[set[str]] = [set()]
        self.typed_scope_stack: list[set[str]] = [set()]
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
            for marker in (*self.config.boundary_module_markers, *self.config.config_module_markers)
        )

    def add_finding(
        self,
        node: ast.AST,
        code: str,
        message: str,
        *,
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
            module_name = ".".join([*package_parts, module_name] if module_name else package_parts)
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
        public_methods = [method for method in direct_methods if not method.name.startswith("_")]
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
        self.class_stack.append(node.name)
        for child in node.body:
            self.visit(child)
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
        if self.function_stack and not is_decorator_inner_function(node, self.function_stack[-1]):
            self.add_finding(
                node,
                "QG154",
                f"函数 `{node.name}` 定义在函数 `{self.function_stack[-1]}` 内部，会降低接口数量审查的可见性。",
                severity="info",
                confidence="high",
                suggestion=(
                    "新增嵌套函数仍须在修改说明中全量披露；仅在形成稳定共享契约时提升到模块级。"
                ),
                evidence={"outer_function": self.function_stack[-1], "nested_function": node.name},
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
                docstring_text=docstring.text,
                docstring_line_count=docstring.content_lines,
                docstring_sections=docstring.sections,
                documented_parameters=docstring.documented_parameters,
            )
        )
        arguments = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        parameter_mappings = {
            argument.arg for argument in arguments if annotation_is_mapping(argument.annotation)
        }
        typed_parameters = {
            argument.arg for argument in arguments if argument.annotation is not None
        }
        if self.class_stack:
            typed_parameters.update({"self", "cls"})
        if node.args.kwarg is not None:
            parameter_mappings.add(node.args.kwarg.arg)
            if node.args.kwarg.annotation is not None:
                typed_parameters.add(node.args.kwarg.arg)
        self.function_stack.append(node.name)
        self.mapping_scope_stack.append(parameter_mappings)
        self.typed_scope_stack.append(typed_parameters)
        for child in node.body:
            self.visit(child)
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
        arguments = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        parameter_count = sum(argument.arg not in {"self", "cls"} for argument in arguments)
        parameter_count += int(node.args.vararg is not None) + int(node.args.kwarg is not None)
        positional_defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(
            node.args.defaults
        )
        argument_defaults = [
            *zip(node.args.args, positional_defaults, strict=True),
            *zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True),
        ]
        boolean_flags = 0
        for argument, default in argument_defaults:
            annotation_name = (
                dotted_name(argument.annotation) if argument.annotation is not None else ""
            )
            default_is_bool = isinstance(default, ast.Constant) and isinstance(default.value, bool)
            boolean_flags += int(annotation_name == "bool" or default_is_bool)
        return parameter_count, boolean_flags

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
                    fallback = guarded_mapping_fallback(node.test, present_value, missing_value)
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
                evidence={"static_result": static_result, "unreachable_branch": branch_name},
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
        """
        记录调用关系并执行调用表达式级规则。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        self.call_nodes.add(id(node.func))
        target = dotted_name(node.func)
        if target in {"self.add_finding", "Finding"}:
            diagnostic_arguments = [
                *node.args,
                *(keyword.value for keyword in node.keywords),
            ]
            for argument in diagnostic_arguments:
                for child in ast.walk(argument):
                    if isinstance(child, ast.Constant) and isinstance(child.value, str):
                        self.diagnostic_string_nodes.add(id(child))
        if target:
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
                )
            )
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
        if isinstance(node.func, ast.Name) and node.func.id in {"hasattr", "getattr"}:
            test_context = is_test_path(self.facts.path)
            receiver_name = dotted_name(node.args[0]) if node.args else ""
            receiver_tail = receiver_name.rsplit(".", 1)[-1] if receiver_name else ""
            statically_typed = receiver_tail in self.typed_names or receiver_name.startswith(
                ("self", "cls")
            )
            boundary_module = self._is_contract_boundary_module()
            severity: Severity = (
                "warning" if test_context else "error" if statically_typed else "info"
            )
            if node.func.id == "hasattr":
                self.add_finding(
                    node,
                    "QG005",
                    "使用 hasattr() 探测对象字段，把明确类型契约降级为运行时猜测。",
                    severity=severity,
                    confidence="high" if statically_typed else "medium",
                    suggestion=(
                        "静态类型对象应直接访问正式属性；真正可选的能力应使用 Protocol、联合类型、"
                        "显式 Optional 字段或独立适配器表达，不得在消费方 hasattr 猜测。"
                    ),
                    evidence={
                        "receiver": receiver_name,
                        "statically_typed": statically_typed,
                        "test_context": test_context,
                        "boundary_module": boundary_module,
                    },
                )
            elif len(node.args) >= GETATTR_DEFAULT_ARG_COUNT:
                self.add_finding(
                    node,
                    "QG006",
                    "getattr(..., default) 在对象契约缺失时静默兜底。",
                    severity=severity,
                    confidence="high" if statically_typed else "medium",
                    suggestion=(
                        "静态类型对象应直接访问正式字段并让契约错误暴露；真正可选字段必须在"
                        "Protocol、联合类型或显式 Optional 中表达。未知动态对象只保留审计信息。"
                    ),
                    evidence={
                        "receiver": receiver_name,
                        "statically_typed": statically_typed,
                        "test_context": test_context,
                        "boundary_module": boundary_module,
                    },
                )
        if isinstance(node.func, ast.Attribute):
            self._check_attribute_call(node)
        self.generic_visit(node)

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
        confirmed_mapping = isinstance(receiver, ast.Dict) or receiver_tail in self.mapping_names
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
        """
        检查映射 get、setdefault 和 pop 兜底。

        Args:
            node: 待分析的 AST 节点。
            attribute: 被调用的属性名称。
            receiver_name: 接收者的点分名称。
            confirmed_mapping: 接收者是否由类型标注或赋值静态确认为映射。
            mapping_name_hint: 接收者名称是否只表现出映射倾向。

        Returns:
            None。
        """
        receiver_tail = receiver_name.rsplit(".", 1)[-1]
        lookup_mapping = is_lookup_mapping_name(receiver_tail)
        boundary_module = self._is_contract_boundary_module()
        test_context = is_test_path(self.facts.path)
        if attribute == "get" and confirmed_mapping and lookup_mapping:
            return
        if attribute == "get":
            explicit_default = len(node.args) >= DICT_DEFAULT_ARG_COUNT
            if confirmed_mapping or self.config.strict_get:
                self.add_finding(
                    node,
                    "QG004" if explicit_default else "QG003",
                    "字典 .get() 使用显式默认值，可能把缺字段错误转成静默兼容。"
                    if explicit_default
                    else "字典 .get() 将缺字段转换为 None，可能掩盖结构契约错误。",
                    severity=(
                        "warning"
                        if test_context
                        else "info"
                        if boundary_module
                        else "critical"
                        if confirmed_mapping
                        else "error"
                    ),
                    confidence="high" if confirmed_mapping else "medium",
                    suggestion=(
                        "内部稳定映射的必需字段必须使用 [] 直接读取；真正可选字段应通过 "
                        "TypedDict(total=False)、Pydantic Optional 或显式联合类型声明，并在输入边界集中处理。"
                    ),
                    evidence={
                        "receiver": receiver_name,
                        "explicit_default": explicit_default,
                        "confirmed_mapping": confirmed_mapping,
                        "lookup_mapping": lookup_mapping,
                        "boundary_module": boundary_module,
                    },
                )
            else:
                self.add_finding(
                    node,
                    "QG026",
                    "发现无法静态确认接收者类型的 .get() 调用。",
                    severity="info",
                    confidence="medium" if mapping_name_hint else "low",
                    suggestion="确认它是客户端 API 还是映射兜底；若是内部字典契约，改为直接索引或补充类型标注。",
                    evidence={"receiver": receiver_name, "explicit_default": explicit_default},
                )
        if (
            attribute == "setdefault"
            and (confirmed_mapping or self.config.strict_get)
            and not (confirmed_mapping and lookup_mapping)
            and id(node) not in self.aggregation_setdefault_calls
        ):
            self.add_finding(
                node,
                "QG012",
                "setdefault() 同时承担读取、兜底和写入，容易隐藏状态归并副作用。",
                severity=(
                    "warning"
                    if test_context
                    else "info"
                    if boundary_module
                    else "critical"
                    if confirmed_mapping and receiver_tail in MAPPING_NAME_HINTS
                    else "error"
                ),
                suggestion=(
                    "不要把 setdefault() 机械展开成 `if key not in mapping: mapping[key] = default`。"
                    "若缺键是合法的聚合/缓存语义，保留 setdefault 或封装到唯一状态入口；"
                    "若字段按契约必需，直接索引并让缺失错误暴露。"
                ),
                confidence="high" if confirmed_mapping else "medium",
            )
        if (
            attribute == "pop"
            and len(node.args) >= DICT_DEFAULT_ARG_COUNT
            and (confirmed_mapping or self.config.strict_get)
            and not (confirmed_mapping and lookup_mapping)
        ):
            self.add_finding(
                node,
                "QG025",
                "映射 pop(key, default) 在删除字段时静默忽略缺失。",
                severity=(
                    "warning"
                    if test_context
                    else "info"
                    if boundary_module
                    else "critical"
                    if confirmed_mapping and receiver_tail in MAPPING_NAME_HINTS
                    else "error"
                ),
                confidence="high" if confirmed_mapping else "medium",
                suggestion="若字段按契约必须存在，使用 pop(key)；真正可选字段必须在类型中显式声明。",
                evidence={"receiver": receiver_name},
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
                )
            )
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        """
        检查宽泛异常、契约异常和导入兼容分支。

        Args:
            node: 待分析的 AST 节点。

        Returns:
            None。
        """
        try_assignment = simple_assignment(node.body[0]) if len(node.body) == 1 else None
        for handler in node.handlers:
            if try_assignment is not None and len(handler.body) == 1:
                fallback_assignment = simple_assignment(handler.body[0])
                handler_names = (
                    {dotted_name(item) for item in handler.type.elts}
                    if isinstance(handler.type, ast.Tuple)
                    else {dotted_name(handler.type)}
                    if handler.type is not None
                    else {""}
                )
                if "KeyError" in handler_names and fallback_assignment is not None:
                    try_target, try_value = try_assignment
                    fallback_target, fallback_value = fallback_assignment
                    access = mapping_subscript(try_value)
                    if (
                        try_target == fallback_target
                        and access is not None
                        and is_contract_fallback_value(fallback_value)
                    ):
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
            if isinstance(handler.type, ast.Tuple):
                exception_names = {dotted_name(item) for item in handler.type.elts}
            else:
                exception_names = {dotted_name(handler.type)} if handler.type is not None else {""}
            if exception_names & {"", "BaseException", "Exception"}:
                exception_name = next(
                    name for name in ("", "BaseException", "Exception") if name in exception_names
                )
                swallowed = bool(handler.body) and all(
                    is_constant_default(item) for item in handler.body
                )
                explicit_best_effort = in_explicit_best_effort_scope(self.function_stack)
                self.add_finding(
                    handler,
                    "QG007",
                    "宽泛异常被捕获" + ("并转换为默认结果。" if swallowed else "。"),
                    severity=("error" if swallowed and not explicit_best_effort else "warning"),
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
            contract_exceptions = exception_names & {"AttributeError", "KeyError", "TypeError"}
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
        blocks = [("body", node.body), ("orelse", node.orelse), ("finalbody", node.finalbody)]
        blocks.extend(
            (f"handler[{index}]", handler.body) for index, handler in enumerate(node.handlers)
        )
        self._report_unreachable_blocks("try", blocks)
        self.generic_visit(node)
