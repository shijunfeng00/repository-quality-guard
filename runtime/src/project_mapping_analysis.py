"""Bounded Python return-mapping shape analysis for profile protocols.

Resolve literal return keys, local assignments, state-type construction and
self-method calls without executing repository source. Dynamic shapes remain
incomplete so callers do not report unproven required-key removals.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .project_profiles import StableMappingContract


@dataclass(slots=True, frozen=True)
class MappingShape:
    """保存静态分析得到的映射键集合及封闭性证明。

    keys 是能够从字典字面量、返回语句和局部赋值证明存在的键。
    complete 仅在所有相关返回路径都能闭合时为真；动态表达式
    或未解析的调用保留不完整状态，不得据此断言某个必需键缺失。
    """

    keys: frozenset[str] = frozenset()
    complete: bool = False

    def merge(self, other: MappingShape) -> MappingShape:
        """
        合并两个可能来自不同返回路径的映射形态。

        Args:
            other: 另一条返回或产出路径。

        Returns:
            键集合取并集、完整性取逻辑与的新形态。
        """
        return MappingShape(self.keys | other.keys, self.complete and other.complete)


class ReturnMappingAnalyzer:
    """
    对单个 Python 文件执行有限的类内返回映射键分析。

    分析器只解析字典字面量、局部变量赋值和 ``self.method()`` 调用链，
    不尝试执行源码，也不会把无法证明的动态键臆测为固定契约。
    """

    def __init__(
        self,
        path: str,
        source: str,
        tree: ast.Module | None = None,
        state_contract: StableMappingContract | None = None,
    ) -> None:
        """初始化返回映射分析器。

        Args:
            path: Python 文件路径。
            source: 对应 Git 快照源码。
            tree: 可选的已解析 AST；存在时直接复用。
            state_contract: 可选的 Profile 状态映射契约。

        Returns:
            None。
        """
        self.path = path
        self.tree = tree or ast.parse(source, filename=path, type_comments=True)
        nodes = tuple(ast.walk(self.tree))
        self.callables = self._collect_callables(self.tree)
        self.state_contract = state_contract
        if state_contract is not None and state_contract.state_type:
            defaults = self._string_defaults(nodes)
            state_names = self._state_names(
                nodes,
                state_contract.state_type,
                state_contract.state_conventional_names,
            )
            expressions = self._state_key_expressions(
                nodes, path, state_names, state_contract
            )
            keys = self._static_string_keys(expressions, defaults)
            state_defined = any(
                isinstance(node, ast.ClassDef)
                and node.name == state_contract.state_type
                for node in self.tree.body
            )
            self.state_shape = MappingShape(frozenset(keys), state_defined)
        else:
            self.state_shape = MappingShape()

    @staticmethod
    def _state_names(
        nodes: tuple[ast.AST, ...],
        state_type: str,
        conventional_names: tuple[str, ...],
    ) -> set[str]:
        """收集可静态证明为 Profile state 实例的局部名称。

        Args:
            nodes: 当前 Python 文件预展开的 AST 节点。

        Returns:
            构造赋值、类型注解及约定名称形成的状态接收者集合。
        """
        names = set(conventional_names)
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if isinstance(value, ast.Call):
                    leaf = (
                        value.func.id
                        if isinstance(value.func, ast.Name)
                        else value.func.attr
                        if isinstance(value.func, ast.Attribute)
                        else ""
                    )
                    if leaf == state_type:
                        names.update(
                            target.id
                            for target in targets
                            if isinstance(target, ast.Name)
                        )
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            arguments = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            names.update(
                argument.arg
                for argument in arguments
                if argument.annotation is not None
                and ast.unparse(argument.annotation).strip("'\"").endswith(state_type)
            )
        return names

    @staticmethod
    def _string_defaults(nodes: tuple[ast.AST, ...]) -> dict[str, str]:
        """收集函数参数中可用于解析状态键的字符串默认值。

        Args:
            nodes: 当前 Python 文件预展开的 AST 节点。

        Returns:
            参数名到字符串默认值的映射。
        """
        defaults: dict[str, str] = {}
        for node in nodes:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            positional = [*node.args.posonlyargs, *node.args.args]
            arguments = (
                positional[-len(node.args.defaults) :] if node.args.defaults else ()
            )
            defaults.update(
                {
                    argument.arg: default.value
                    for argument, default in zip(
                        arguments, node.args.defaults, strict=True
                    )
                    if isinstance(default, ast.Constant)
                    and isinstance(default.value, str)
                }
            )
            defaults.update(
                {
                    argument.arg: default.value
                    for argument, default in zip(
                        node.args.kwonlyargs,
                        node.args.kw_defaults,
                        strict=True,
                    )
                    if isinstance(default, ast.Constant)
                    and isinstance(default.value, str)
                }
            )
        return defaults

    @classmethod
    def _state_key_expressions(
        cls,
        nodes: tuple[ast.AST, ...],
        path: str,
        state_names: set[str],
        contract: StableMappingContract,
    ) -> list[ast.expr]:
        """收集 Profile state 构造、写入和可见性声明中的键表达式。

        Args:
            nodes: 当前 Python 文件预展开的 AST 节点。
            path: 当前源码相对路径。
            state_names: 已证明的状态变量名称。

        Returns:
            后续可做静态字符串求值的键表达式列表。
        """
        expressions: list[ast.expr] = []
        writer_methods = set(contract.state_writer_methods)
        key_writer_methods = set(contract.state_key_writer_methods)
        constructor_fields = set(contract.state_constructor_fields)
        excluded_keywords = {*contract.state_constructor_excluded_keywords, None}
        source_suffix = contract.state_source_suffix
        state_type = contract.state_type
        is_state_source = bool(source_suffix and path.endswith(source_suffix))
        for node in nodes:
            if not isinstance(node, ast.Call):
                match node:
                    case ast.Assign(targets=targets) if is_state_source:
                        pass
                    case (
                        ast.AnnAssign(target=target) | ast.AugAssign(target=target)
                    ) if is_state_source:
                        targets = [target]
                    case _:
                        continue
                expressions.extend(
                    target.slice
                    for target in targets
                    if isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                )
                continue
            match node.func:
                case ast.Name(id=leaf) | ast.Attribute(attr=leaf):
                    pass
                case _:
                    leaf = ""
            if leaf == state_type:
                expressions.extend(node.args[:1])
                expressions.extend(
                    keyword.value
                    if keyword.arg in constructor_fields
                    else ast.Constant(keyword.arg)
                    for keyword in node.keywords
                    if keyword.arg not in excluded_keywords
                )
            expressions.extend(
                cls._state_writer_keys(
                    node,
                    state_names,
                    writer_methods,
                    key_writer_methods,
                    is_state_source,
                )
            )
            match node.func:
                case ast.Attribute(value=ast.Call(), attr="__init__") if (
                    is_state_source
                ):
                    expressions.extend(
                        ast.Constant(keyword.arg)
                        for keyword in node.keywords
                        if keyword.arg is not None
                    )
        return expressions

    @staticmethod
    def _state_writer_keys(
        node: ast.Call,
        state_names: set[str],
        writer_methods: set[str],
        key_writer_methods: set[str],
        is_state_source: bool,
    ) -> tuple[ast.expr, ...]:
        """按 Profile 写入口的接收者与参数协议提取状态键。

        显式 key 参数只属于单键写入口；映射写入口始终使用首个位置参数。
        """
        if not isinstance(node.func, ast.Attribute):
            return ()
        leaf = node.func.attr
        if leaf not in writer_methods:
            return ()
        match node.func.value:
            case ast.Name(id=name):
                is_state = name in state_names or (name == "self" and is_state_source)
            case ast.Attribute(attr=name):
                is_state = name in state_names
            case _:
                is_state = False
        if not is_state:
            return ()
        positional_key = node.args[0] if node.args else None
        if leaf in key_writer_methods:
            positional_key = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "key"),
                positional_key,
            )
        return () if positional_key is None else (positional_key,)

    @staticmethod
    def _static_string_keys(
        expressions: list[ast.expr],
        defaults: dict[str, str],
    ) -> set[str]:
        """把状态键表达式收敛为可静态证明的字符串集合。

        Args:
            expressions: Profile state 键候选表达式。
            defaults: 参数名到字符串默认值的映射。

        Returns:
            只包含常量、容器键和可完全求值 f-string 的键集合。
        """
        keys: set[str] = set()
        pending = list(expressions)
        while pending:
            expression = pending.pop()
            match expression:
                case ast.Constant(value=str() as value):
                    keys.add(value)
                    continue
                case ast.Name(id=name) if name in defaults:
                    keys.add(defaults[name])
                    continue
                case ast.Dict(keys=dict_keys):
                    pending.extend(key for key in dict_keys if key is not None)
                    continue
                case (
                    ast.List(elts=elements)
                    | ast.Tuple(elts=elements)
                    | ast.Set(elts=elements)
                ):
                    pending.extend(elements)
                    continue
                case ast.JoinedStr():
                    pass
                case _:
                    continue
            parts: list[str] = []
            for part in expression.values:
                match part:
                    case ast.Constant(value=str() as value):
                        parts.append(value)
                    case ast.FormattedValue(value=ast.Name(id=name)) if (
                        name in defaults
                    ):
                        parts.append(defaults[name])
                    case _:
                        break
            else:
                keys.add("".join(parts))
        return keys

    @staticmethod
    def _collect_callables(
        tree: ast.Module,
    ) -> tuple[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef], ...]:
        """收集可被稳定契约查询的模块顶层函数和类直接成员。"""
        callables: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                callables.append((node.name, node))
                continue
            if not isinstance(node, ast.ClassDef):
                continue
            callables.extend(
                (f"{node.name}.{child.name}", child)
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
        return tuple(callables)

    def analyze(self, qualname: str, channel: str) -> MappingShape:
        """分析指定函数或方法的映射结构。

        Args:
            qualname: 模块内函数或类方法的限定名。
            channel: 待分析的输出通道，例如 return、yield 或 state_keys。

        Returns:
            当前调用路径可静态确定的键集合及动态形状标记。
        """
        if channel == "state_keys":
            return self.state_shape
        node = self._find_callable(qualname)
        if node is None:
            return MappingShape()
        return self._analyze_callable(qualname, node, channel, set())

    def _find_callable(
        self, qualname: str
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        """按限定名查询当前 AST 声明；未声明是稳定契约分析的合法结果。"""
        for callable_name, node in self.callables:
            if callable_name == qualname:
                return node
        return None

    def _analyze_callable(
        self,
        qualname: str,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        channel: str,
        stack: set[tuple[str, str]],
    ) -> MappingShape:
        """分析一个调用节点并阻止递归调用环。"""
        identity = (qualname, channel)
        if identity in stack:
            return MappingShape()
        stack = {*stack, identity}
        environments: dict[str, MappingShape] = {}
        outputs: list[MappingShape] = []
        self._walk_statements(
            node.body, qualname, channel, stack, environments, outputs
        )
        if not outputs:
            return MappingShape()
        shape = outputs[0]
        for item in outputs[1:]:
            shape = shape.merge(item)
        return shape

    def _walk_statements(
        self,
        statements: list[ast.stmt],
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
        environment: dict[str, MappingShape],
        outputs: list[MappingShape],
    ) -> None:
        """按源码顺序近似传播局部变量映射形态。"""
        for statement in statements:
            if isinstance(
                statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            if self._record_assignment(
                statement,
                qualname,
                channel,
                stack,
                environment,
                outputs,
            ):
                continue
            if self._record_output(
                statement,
                qualname,
                channel,
                stack,
                environment,
                outputs,
            ):
                continue
            self._walk_control_flow(
                statement,
                qualname,
                channel,
                stack,
                environment,
                outputs,
            )

    def _record_assignment(
        self,
        statement: ast.stmt,
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
        environment: dict[str, MappingShape],
        outputs: list[MappingShape],
    ) -> bool:
        """处理赋值和循环目标的映射形态传播。"""
        if isinstance(statement, ast.Assign):
            shape = self._expression_shape(
                statement.value, qualname, channel, stack, environment
            )
            for target in statement.targets:
                self._update_environment(target, shape, environment)
            return True
        if isinstance(statement, ast.AnnAssign) and statement.value is not None:
            self._update_environment(
                statement.target,
                self._expression_shape(
                    statement.value, qualname, channel, stack, environment
                ),
                environment,
            )
            return True
        if not isinstance(statement, (ast.For, ast.AsyncFor)):
            return False
        iterator_shape = self._expression_shape(
            statement.iter,
            qualname,
            "yield",
            stack,
            environment,
        )
        nested = dict(environment)
        self._update_environment(statement.target, iterator_shape, nested)
        self._walk_statements(statement.body, qualname, channel, stack, nested, outputs)
        self._walk_statements(
            statement.orelse, qualname, channel, stack, nested, outputs
        )
        return True

    def _record_output(
        self,
        statement: ast.stmt,
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
        environment: dict[str, MappingShape],
        outputs: list[MappingShape],
    ) -> bool:
        """收集当前通道的 return、yield 和 yield from。"""
        expression: ast.expr | None = None
        if isinstance(statement, ast.Return) and channel == "return":
            expression = statement.value
        elif (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, (ast.Yield, ast.YieldFrom))
            and channel == "yield"
        ):
            expression = statement.value.value
        else:
            return False
        outputs.append(
            self._expression_shape(expression, qualname, channel, stack, environment)
        )
        return True

    def _walk_control_flow(
        self,
        statement: ast.stmt,
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
        environment: dict[str, MappingShape],
        outputs: list[MappingShape],
    ) -> None:
        """递归处理可能包含返回或产出语句的控制流分支。"""
        branches: list[list[ast.stmt]] = []
        if isinstance(statement, (ast.If, ast.While)):
            branches.extend((statement.body, statement.orelse))
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            branches.append(statement.body)
        elif isinstance(statement, (ast.Try, ast.TryStar)):
            branches.extend((statement.body, statement.orelse, statement.finalbody))
            branches.extend(handler.body for handler in statement.handlers)
        elif isinstance(statement, ast.Match):
            branches.extend(case.body for case in statement.cases)
        for branch in branches:
            self._walk_statements(
                branch,
                qualname,
                channel,
                stack,
                dict(environment),
                outputs,
            )

    def _expression_shape(
        self,
        expression: ast.expr | None,
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
        environment: dict[str, MappingShape],
    ) -> MappingShape:
        """解析一个表达式可能形成的映射键。"""
        match expression:
            case ast.Dict():
                return self._dict_shape(
                    expression, qualname, channel, stack, environment
                )
            case ast.Name(id=name) if name in environment:
                return environment[name]
            case ast.IfExp(body=body, orelse=orelse):
                return self._expression_shape(
                    body, qualname, channel, stack, environment
                ).merge(
                    self._expression_shape(
                        orelse,
                        qualname,
                        channel,
                        stack,
                        environment,
                    )
                )
            case ast.Call():
                return self._call_expression_shape(expression, qualname, channel, stack)
            case ast.BinOp(left=left, op=ast.BitOr(), right=right):
                return self._expression_shape(
                    left, qualname, channel, stack, environment
                ).merge(
                    self._expression_shape(
                        right,
                        qualname,
                        channel,
                        stack,
                        environment,
                    )
                )
            case _:
                return MappingShape()

    def _call_expression_shape(
        self,
        expression: ast.Call,
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
    ) -> MappingShape:
        """解析同一类内 ``self.method()`` 或 ``cls.method()`` 的映射形态。"""
        if not isinstance(expression.func, ast.Attribute):
            return MappingShape()
        receiver = expression.func.value
        if not isinstance(receiver, ast.Name) or receiver.id not in {"self", "cls"}:
            return MappingShape()
        owner, separator, _ = qualname.rpartition(".")
        target = (
            f"{owner}.{expression.func.attr}" if separator else expression.func.attr
        )
        node = self._find_callable(target)
        if node is None:
            return MappingShape()
        return self._analyze_callable(target, node, channel, stack)

    def _dict_shape(
        self,
        expression: ast.Dict,
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
        environment: dict[str, MappingShape],
    ) -> MappingShape:
        """解析字典字面量及其 ``**mapping`` 展开键。"""
        keys: set[str] = set()
        complete = True
        for key, value in zip(expression.keys, expression.values, strict=True):
            if key is None:
                nested = self._expression_shape(
                    value, qualname, channel, stack, environment
                )
                keys.update(nested.keys)
                complete = complete and nested.complete
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
            else:
                complete = False
        return MappingShape(frozenset(keys), complete)

    @staticmethod
    def _update_environment(
        target: ast.expr,
        shape: MappingShape,
        environment: dict[str, MappingShape],
    ) -> None:
        """把推断形态绑定到简单名称或解包目标。"""
        if isinstance(target, ast.Name):
            environment[target.id] = shape
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                ReturnMappingAnalyzer._update_environment(
                    element, MappingShape(), environment
                )
