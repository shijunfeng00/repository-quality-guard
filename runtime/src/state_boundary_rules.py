from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass

from .ast_utils import dotted_name, enclosing_class_name
from .config import GuardConfig
from .facts import ModuleFacts
from .model import Finding
from .policy_common import ParsedModule, all_parameters, annotation_names


def _profile_settings(config: GuardConfig) -> dict[str, object]:
    """Normalize optional profile settings into one total state-boundary contract map."""
    settings: dict[str, object] = {
        "state_boundary_type": "",
        "absolute_boundary_name": "",
        "state_boundary_suggestion": "",
        "dynamic_execution_nonblocking_hint": "",
    }
    settings.update(config.profile_settings)
    return settings


_DYNAMIC_EXECUTION_CALLS = frozenset(
    {
        "__import__",
        "builtins.__import__",
        "builtins.compile",
        "builtins.eval",
        "builtins.exec",
        "compile",
        "eval",
        "exec",
        "importlib.import_module",
    }
)


@dataclass(frozen=True, slots=True)
class _StateBoundaryContract:
    """保存唯一 Profile 输入边界已解析的状态写约束。

    同一仓库的函数分析器共享不可变契约；局部别名与发现仍由各分析器持有。
    """

    state_type: str
    boundary_name: str
    suggestion: str
    profile_name: str


class StateBoundaryEvaluator:
    """按 Profile 声明检查状态写边界与动态代码执行逃逸。

    评估器在仓库范围解析一次可选配置并收集状态持有字段，再将冻结的状态
    约束交给逐函数分析器。动态执行检查使用同一 Profile 的独立能力开关。
    """

    def __init__(
        self,
        modules: list[ParsedModule],
        config: GuardConfig,
    ) -> None:
        """初始化评估器并收集 Profile state 持有者字段。

        Args:
            modules: 已解析的仓库模块及其 AST。
            config: 当前项目质量配置。

        Returns:
            None。
        """
        settings = _profile_settings(config)
        self._state_enabled = "state-write-boundary" in config.profile_capabilities
        self._dynamic_enabled = (
            "dynamic-execution-blocker" in config.profile_capabilities
        )
        self._enabled = self._state_enabled or self._dynamic_enabled
        self.config_name = config.project_name
        state_type = str(settings["state_boundary_type"] or "")
        self._state_contract = _StateBoundaryContract(
            state_type=state_type,
            profile_name=config.project_name,
            boundary_name=str(
                settings["absolute_boundary_name"] or state_type or "state"
            ),
            suggestion=str(
                settings["state_boundary_suggestion"]
                or "Use the profile-declared canonical state transition API; do not mutate state directly or through reflection."
            ),
        )
        self._dynamic_hint = str(
            settings["dynamic_execution_nonblocking_hint"]
            or "test/nonblocking paths remain separately audited"
        )
        self._holders = (
            _collect_state_holder_fields(modules, state_type)
            if self._state_enabled and state_type
            else set()
        )

    def check_module(self, module: ParsedModule) -> list[Finding]:
        """检查模块中的状态写旁路与动态执行入口。

        Args:
            module: 已解析的当前模块。

        Returns:
            当前模块命中的 QG189/QG190 Critical 列表。
        """
        if not self._enabled:
            return []
        facts = module.facts
        findings = (
            _dynamic_execution_findings(
                facts,
                module.nodes,
                module.parents,
                self._dynamic_hint,
                self.config_name,
            )
            if self._dynamic_enabled
            else []
        )
        for node in module.nodes:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            owner = enclosing_class_name(node, module.parents)
            if not self._state_enabled or owner == self._state_contract.state_type:
                continue
            analyzer = _FunctionMutationAnalyzer(
                facts=facts,
                function=node,
                owner=owner,
                holder_fields=self._holders,
                contract=self._state_contract,
            )
            findings.extend(analyzer.run())
        return findings


class _FunctionMutationAnalyzer:
    """跟踪单个函数内的 Profile state 根对象、子对象和局部别名。"""

    def __init__(
        self,
        facts: ModuleFacts,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        owner: str,
        holder_fields: set[tuple[str, str, str]],
        contract: _StateBoundaryContract,
    ) -> None:
        """初始化单函数状态写分析器。

        Args:
            facts: 当前模块静态事实。
            function: 待分析函数或异步函数。
            owner: 函数所属类名；模块函数为空字符串。
            holder_fields: 全仓可静态证明持有 Profile state 的字段。
            contract: Profile 输入边界已解析的不可变状态写约束。

        Returns:
            None。
        """
        self._facts = facts
        self._function = function
        self._owner = owner
        self._contract = contract
        self._aliases: dict[str, str] = {}
        self._findings: list[Finding] = []
        self._reported_nodes: set[int] = set()
        self._holder_paths = {
            f"self.{attribute}"
            for module, class_name, attribute in holder_fields
            if module == facts.module and class_name == owner
        }
        for argument in all_parameters(function):
            annotated = self._contract.state_type in annotation_names(
                argument.annotation
            )
            if annotated:
                self._aliases[argument.arg] = "state"

    def run(self) -> list[Finding]:
        """按源码顺序分析当前函数。

        Returns:
            当前函数命中的 QG189 Critical 列表。
        """
        self._visit_block(self._function.body)
        return self._findings

    def _visit_block(self, statements: Iterable[ast.stmt]) -> None:
        """按顺序访问一个语句块并维护局部别名状态。"""
        for statement in statements:
            self._visit_statement(statement)

    def _visit_statement(self, statement: ast.stmt) -> None:
        """访问一条语句并阻止别名穿透嵌套定义边界。"""
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if self._visit_assignment_statement(statement):
            return
        if self._visit_control_statement(statement):
            return
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt):
                self._visit_statement(child)

    def _visit_assignment_statement(self, statement: ast.stmt) -> bool:
        """处理赋值、增量赋值和删除语句并返回是否已消费。"""
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                self._check_target(target, statement)
            if len(statement.targets) == 1:
                self._bind_target(statement.targets[0], statement.value)
            return True
        if isinstance(statement, ast.AnnAssign):
            if statement.value is not None:
                self._check_target(statement.target, statement)
            annotated = self._contract.state_type in annotation_names(
                statement.annotation
            )
            if annotated and isinstance(statement.target, ast.Name):
                self._aliases[statement.target.id] = "state"
            elif statement.value is None:
                self._drop_bound_names(statement.target)
            else:
                self._bind_target(statement.target, statement.value)
            return True
        if isinstance(statement, ast.AugAssign):
            self._check_target(statement.target, statement)
            return True
        if isinstance(statement, ast.Delete):
            for target in statement.targets:
                self._check_target(target, statement)
            return True
        return False

    def _visit_control_statement(self, statement: ast.stmt) -> bool:
        """处理控制流语句并在分支汇合处保留共同别名。"""
        handled = False
        if isinstance(statement, ast.If):
            self._visit_branches(statement.body, statement.orelse)
            handled = True
        if isinstance(statement, (ast.For, ast.AsyncFor)):
            self._drop_bound_names(statement.target)
            self._visit_branches(statement.body, statement.orelse)
            handled = True
        if isinstance(statement, ast.While):
            self._visit_branches(statement.body, statement.orelse)
            handled = True
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            for item in statement.items:
                if item.optional_vars is not None:
                    self._bind_target(item.optional_vars, item.context_expr)
            self._visit_block(statement.body)
            handled = True
        if isinstance(statement, ast.Try):
            branches = [statement.body, statement.orelse, statement.finalbody]
            branches.extend(handler.body for handler in statement.handlers)
            self._visit_many_branches(branches)
            handled = True
        if isinstance(statement, ast.Match):
            self._visit_many_branches([case.body for case in statement.cases])
            handled = True
        return handled

    def _visit_branches(self, first: list[ast.stmt], second: list[ast.stmt]) -> None:
        """分析两个控制流分支。"""
        self._visit_many_branches([first, second])

    def _visit_many_branches(self, branches: list[list[ast.stmt]]) -> None:
        """分析多分支并只保留所有出口一致的别名。"""
        original = dict(self._aliases)
        branch_aliases: list[dict[str, str]] = []
        for branch in branches:
            self._aliases = dict(original)
            self._visit_block(branch)
            branch_aliases.append(dict(self._aliases))
        if not branch_aliases:
            self._aliases = original
            return
        common = set.intersection(*(set(aliases) for aliases in branch_aliases))
        self._aliases = {
            name: branch_aliases[0][name]
            for name in common
            if all(
                aliases[name] == branch_aliases[0][name] for aliases in branch_aliases
            )
        }

    def _bind_target(self, target: ast.expr, value: ast.expr) -> None:
        """把赋值目标绑定为状态、子对象或普通值。"""
        kind = self._origin(value)
        if isinstance(target, ast.Name):
            if kind is None:
                if target.id in self._aliases:
                    del self._aliases[target.id]
            else:
                self._aliases[target.id] = kind
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            self._drop_bound_names(target)

    def _drop_bound_names(self, target: ast.expr) -> None:
        """删除循环或解包目标已有的状态别名。"""
        if isinstance(target, ast.Name):
            if target.id in self._aliases:
                del self._aliases[target.id]
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._drop_bound_names(item)

    def _check_target(self, target: ast.expr, node: ast.AST) -> None:
        """检查赋值或删除目标是否属于状态及其嵌套对象。"""
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._check_target(item, node)
            return
        receiver = (
            target.value if isinstance(target, (ast.Attribute, ast.Subscript)) else None
        )
        if receiver is None:
            return
        kind = self._origin(receiver)
        if kind is not None:
            self._report(
                node,
                f"通过赋值或删除直接修改 {self._contract.boundary_name} {kind}对象 `{_node_text(target)}`",
            )

    def _origin(self, expression: ast.AST | None) -> str | None:
        """只沿显式字段、下标与局部别名追踪来源；调用结果未知。"""
        if isinstance(expression, ast.Name):
            return (
                self._aliases[expression.id] if expression.id in self._aliases else None
            )
        if isinstance(expression, ast.Attribute):
            if dotted_name(expression) in self._holder_paths:
                return "state"
            return "child" if self._origin(expression.value) is not None else None
        if isinstance(expression, ast.Subscript):
            return "child" if self._origin(expression.value) is not None else None
        return None

    def _report(self, node: ast.AST, detail: str) -> None:
        """追加一次去重后的 QG189 Critical。"""
        identity = id(node)
        if identity in self._reported_nodes:
            return
        self._reported_nodes.add(identity)
        self._findings.append(
            self._facts.make_finding(
                node,
                "QG189",
                f"{detail}；{self._contract.boundary_name} 必须保持 Profile 声明的只读/受控写边界。",
                symbol=_qualname(self._facts.module, self._owner, self._function.name),
                severity="critical",
                suggestion=self._contract.suggestion,
                evidence={
                    "absolute_blocker": True,
                    "profile": self._contract.profile_name,
                },
            )
        )


def _collect_state_holder_fields(
    modules: list[ParsedModule],
    state_type: str,
) -> set[tuple[str, str, str]]:
    """收集类中可静态证明持有 Profile state 的字段。"""
    holders: set[tuple[str, str, str]] = set()
    for module in modules:
        facts = module.facts
        for node in module.nodes:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                owner = enclosing_class_name(node, module.parents)
                if owner and state_type in annotation_names(node.annotation):
                    holders.add((facts.module, owner, node.target.id))
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                holders.update(
                    _function_holder_fields(facts, node, module.parents, state_type)
                )
    return holders


def _function_holder_fields(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parents: dict[ast.AST, ast.AST],
    state_type: str,
) -> set[tuple[str, str, str]]:
    """收集单个类方法内赋值形成的 Profile state 持有字段。"""
    owner = enclosing_class_name(node, parents)
    if not owner:
        return set()
    local_states = {
        argument.arg
        for argument in all_parameters(node)
        if state_type in annotation_names(argument.annotation)
    }
    holders: set[tuple[str, str, str]] = set()
    for statement in ast.walk(node):
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(statement, ast.AnnAssign):
            target = statement.target
        else:
            target = statement.targets[0] if len(statement.targets) == 1 else None
        value = statement.value
        value_is_state = _is_state_value(value, local_states, state_type)
        if isinstance(target, ast.Name) and value_is_state:
            local_states.add(target.id)
            continue
        self_attribute = (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        )
        if self_attribute and value_is_state:
            holders.add((facts.module, owner, target.attr))
    return holders


def _is_state_value(
    value: ast.AST | None, local_states: set[str], state_type: str
) -> bool:
    """仅跟踪已验证的同函数局部状态别名；构造器调用仍是未知。"""
    if isinstance(value, ast.Name):
        return value.id in local_states
    return False  # Constructor identity needs a resolved TypeChecker declaration.


def _dynamic_execution_findings(
    facts: ModuleFacts,
    nodes: tuple[ast.AST, ...],
    parents: dict[ast.AST, ast.AST],
    nonblocking_hint: str,
    profile_name: str,
) -> list[Finding]:
    """报告 Profile 启用后的生产动态执行和动态导入。"""
    findings: list[Finding] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        name = dotted_name(node.func)
        if name not in _DYNAMIC_EXECUTION_CALLS:
            continue
        owner = "<module>"
        current = parents[node] if node in parents else None
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = current.name
                break
            current = parents[current] if current in parents else None
        findings.append(
            facts.make_finding(
                node,
                "QG190",
                f"检测到动态执行或动态导入入口 `{name}()`。",
                symbol=_qualname(facts.module, "", owner),
                severity="critical",
                suggestion=(
                    "改用显式解析器、固定分发表或静态 import；"
                    + nonblocking_hint
                    + " 仍单独审计但不进入生产绝对门禁。"
                ),
                evidence={"absolute_blocker": True, "profile": profile_name},
            )
        )
    return findings


def _node_text(node: ast.AST) -> str:
    """把 AST 节点转换为可读源码文本。"""
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return node.__class__.__name__


def _qualname(module: str, owner: str, function: str) -> str:
    """拼接稳定的模块、类和函数限定名。"""
    return ".".join(part for part in (module, owner, function) if part)
