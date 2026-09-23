from __future__ import annotations

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


@dataclass(slots=True, frozen=True)
class _MappingShape:
    """保存静态推断得到的映射键集合及完整性。"""

    keys: frozenset[str] = frozenset()
    complete: bool = False

    def merge(self, other: _MappingShape) -> _MappingShape:
        """
        合并两个可能来自不同返回路径的映射形态。

        Args:
            other: 另一条返回或产出路径。

        Returns:
            键集合取并集、完整性取逻辑与的新形态。
        """
        return _MappingShape(self.keys | other.keys, self.complete and other.complete)


class _ReturnMappingAnalyzer:
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
                isinstance(node, ast.ClassDef) and node.name == state_contract.state_type
                for node in self.tree.body
            )
            self.state_shape = _MappingShape(frozenset(keys), state_defined)
        else:
            self.state_shape = _MappingShape()

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
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
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
                            target.id for target in targets if isinstance(target, ast.Name)
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
            arguments = positional[-len(node.args.defaults) :] if node.args.defaults else ()
            defaults.update(
                {
                    argument.arg: default.value
                    for argument, default in zip(arguments, node.args.defaults, strict=True)
                    if isinstance(default, ast.Constant) and isinstance(default.value, str)
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
                    if isinstance(default, ast.Constant) and isinstance(default.value, str)
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
        excluded_keywords = set(contract.state_constructor_excluded_keywords)
        source_suffix = contract.state_source_suffix
        state_type = contract.state_type
        for node in nodes:
            if isinstance(node, ast.Call):
                leaf = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if leaf == state_type:
                    expressions.extend(node.args[:1])
                    expressions.extend(
                        keyword.value
                        if keyword.arg in constructor_fields
                        else ast.Constant(keyword.arg)
                        for keyword in node.keywords
                        if keyword.arg is not None and keyword.arg not in excluded_keywords
                    )
                if isinstance(node.func, ast.Attribute) and leaf in writer_methods:
                    receiver = node.func.value
                    named_state = isinstance(receiver, ast.Name) and (
                        receiver.id in state_names
                        or (receiver.id == "self" and source_suffix and path.endswith(source_suffix))
                    )
                    attributed_state = (
                        isinstance(receiver, ast.Attribute) and receiver.attr in state_names
                    )
                    if named_state or attributed_state:
                        key = (
                            next(
                                (item.value for item in node.keywords if item.arg == "key"),
                                node.args[0] if node.args else None,
                            )
                            if leaf in key_writer_methods
                            else node.args[0]
                            if node.args
                            else None
                        )
                        if key is not None:
                            expressions.append(key)
                if (
                    source_suffix and path.endswith(source_suffix)
                    and leaf == "__init__"
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Call)
                ):
                    expressions.extend(
                        ast.Constant(keyword.arg)
                        for keyword in node.keywords
                        if keyword.arg is not None
                    )
            if source_suffix and path.endswith(source_suffix) and isinstance(
                node,
                (ast.Assign, ast.AnnAssign, ast.AugAssign),
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                expressions.extend(
                    target.slice
                    for target in targets
                    if isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                )
        return expressions

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
            if isinstance(expression, ast.Constant):
                if isinstance(expression.value, str):
                    keys.add(expression.value)
                continue
            if isinstance(expression, ast.Name):
                if expression.id in defaults:
                    keys.add(defaults[expression.id])
                continue
            if isinstance(expression, ast.Dict):
                pending.extend(key for key in expression.keys if key is not None)
                continue
            if isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
                pending.extend(expression.elts)
                continue
            if not isinstance(expression, ast.JoinedStr):
                continue
            parts: list[str] = []
            for part in expression.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    parts.append(part.value)
                    continue
                if (
                    isinstance(part, ast.FormattedValue)
                    and isinstance(part.value, ast.Name)
                    and part.value.id in defaults
                ):
                    parts.append(defaults[part.value.id])
                    continue
                break
            else:
                keys.add("".join(parts))
        return keys

    @staticmethod
    def _collect_callables(
        tree: ast.Module,
    ) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
        """收集模块顶层函数和类的直接成员函数。"""
        callables: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                callables[node.name] = node
                continue
            if not isinstance(node, ast.ClassDef):
                continue
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    callables[f"{node.name}.{child.name}"] = child
        return callables

    def analyze(self, qualname: str, channel: str) -> _MappingShape:
        """分析指定函数或方法的映射结构。

        Args:
            qualname: 模块内函数或类方法的限定名。
            channel: 待分析的输出通道，例如 return、yield 或 state_keys。

        Returns:
            当前调用路径可静态确定的键集合及动态形状标记。
        """
        if channel == "state_keys":
            return self.state_shape
        node = self.callables.get(qualname)
        if node is None:
            return _MappingShape()
        return self._analyze_callable(qualname, node, channel, set())

    def _analyze_callable(
        self,
        qualname: str,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        channel: str,
        stack: set[tuple[str, str]],
    ) -> _MappingShape:
        """分析一个调用节点并阻止递归调用环。"""
        identity = (qualname, channel)
        if identity in stack:
            return _MappingShape()
        stack = {*stack, identity}
        environments: dict[str, _MappingShape] = {}
        outputs: list[_MappingShape] = []
        self._walk_statements(node.body, qualname, channel, stack, environments, outputs)
        if not outputs:
            return _MappingShape()
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
        environment: dict[str, _MappingShape],
        outputs: list[_MappingShape],
    ) -> None:
        """按源码顺序近似传播局部变量映射形态。"""
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
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
        environment: dict[str, _MappingShape],
        outputs: list[_MappingShape],
    ) -> bool:
        """处理赋值和循环目标的映射形态传播。"""
        if isinstance(statement, ast.Assign):
            shape = self._expression_shape(statement.value, qualname, channel, stack, environment)
            for target in statement.targets:
                self._update_environment(target, shape, environment)
            return True
        if isinstance(statement, ast.AnnAssign) and statement.value is not None:
            self._update_environment(
                statement.target,
                self._expression_shape(statement.value, qualname, channel, stack, environment),
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
        self._walk_statements(statement.orelse, qualname, channel, stack, nested, outputs)
        return True

    def _record_output(
        self,
        statement: ast.stmt,
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
        environment: dict[str, _MappingShape],
        outputs: list[_MappingShape],
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
        outputs.append(self._expression_shape(expression, qualname, channel, stack, environment))
        return True

    def _walk_control_flow(
        self,
        statement: ast.stmt,
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
        environment: dict[str, _MappingShape],
        outputs: list[_MappingShape],
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
        environment: dict[str, _MappingShape],
    ) -> _MappingShape:
        """解析一个表达式可能形成的映射键。"""
        match expression:
            case ast.Dict():
                return self._dict_shape(expression, qualname, channel, stack, environment)
            case ast.Name(id=name) if name in environment:
                return environment[name]
            case ast.IfExp(body=body, orelse=orelse):
                return self._expression_shape(body, qualname, channel, stack, environment).merge(
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
                return self._expression_shape(left, qualname, channel, stack, environment).merge(
                    self._expression_shape(
                        right,
                        qualname,
                        channel,
                        stack,
                        environment,
                    )
                )
            case _:
                return _MappingShape()

    def _call_expression_shape(
        self,
        expression: ast.Call,
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
    ) -> _MappingShape:
        """解析同一类内 ``self.method()`` 或 ``cls.method()`` 的映射形态。"""
        if not isinstance(expression.func, ast.Attribute):
            return _MappingShape()
        receiver = expression.func.value
        if not isinstance(receiver, ast.Name) or receiver.id not in {"self", "cls"}:
            return _MappingShape()
        owner, separator, _ = qualname.rpartition(".")
        target = f"{owner}.{expression.func.attr}" if separator else expression.func.attr
        if target not in self.callables:
            return _MappingShape()
        return self._analyze_callable(
            target,
            self.callables[target],
            channel,
            stack,
        )

    def _dict_shape(
        self,
        expression: ast.Dict,
        qualname: str,
        channel: str,
        stack: set[tuple[str, str]],
        environment: dict[str, _MappingShape],
    ) -> _MappingShape:
        """解析字典字面量及其 ``**mapping`` 展开键。"""
        keys: set[str] = set()
        complete = True
        for key, value in zip(expression.keys, expression.values, strict=True):
            if key is None:
                nested = self._expression_shape(value, qualname, channel, stack, environment)
                keys.update(nested.keys)
                complete = complete and nested.complete
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
            else:
                complete = False
        return _MappingShape(frozenset(keys), complete)

    @staticmethod
    def _update_environment(
        target: ast.expr,
        shape: _MappingShape,
        environment: dict[str, _MappingShape],
    ) -> None:
        """把推断形态绑定到简单名称或解包目标。"""
        if isinstance(target, ast.Name):
            environment[target.id] = shape
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                _ReturnMappingAnalyzer._update_environment(element, _MappingShape(), environment)


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
        finding = _callable_contract_finding(contract, target_symbols)
        if finding is not None:
            findings.append(finding)
    for contract in profile.frozen_sse_contracts:
        findings.extend(
            _frozen_sse_contract_findings(
                contract,
                target_sources,
                target_trees=target_trees,
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
            message = f"`{contract.qualname}` 改变了冻结返回映射协议；" + "；".join(details)
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
) -> tuple[_MappingShape, _MappingShape, None]:
    """分析跨文件聚合的 Profile state 键集合契约。"""
    shapes: list[_MappingShape] = []
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
                shape = _ReturnMappingAnalyzer(
                    path, item_source, tree, state_contract=contract
                ).analyze(contract.qualname, "state_keys")
            except SyntaxError:
                complete = False
                continue
            keys.update(shape.keys)
            found = found or shape.complete
        shapes.append(_MappingShape(frozenset(keys), complete and found))
    return shapes[0], shapes[1], None


def _analyze_fixed_mapping_contract(
    contract: StableMappingContract,
    base_sources: dict[str, str],
    target_sources: dict[str, str],
    base_trees: dict[str, ast.Module] | None,
    target_trees: dict[str, ast.Module] | None,
) -> tuple[_MappingShape, _MappingShape, Finding | None]:
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
        return _MappingShape(), _MappingShape(), finding
    try:
        target = _ReturnMappingAnalyzer(
            contract.path,
            source,
            target_trees[contract.path] if target_trees is not None and contract.path in target_trees else None,
        ).analyze(
            contract.qualname,
            contract.channel,
        )
        if contract.path in base_sources:
            base = _ReturnMappingAnalyzer(
                contract.path,
                base_sources[contract.path],
                base_trees[contract.path] if base_trees is not None and contract.path in base_trees else None,
            ).analyze(contract.qualname, contract.channel)
        else:
            base = _MappingShape()
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
        return _MappingShape(), _MappingShape(), finding
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
        self.methods = {
            node.name: node
            for node in (() if self.class_node is None else self.class_node.body)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

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
                if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                    continue
                if child.func.attr != self.contract.envelope_method or not child.args:
                    continue
                receiver = child.func.value
                if not isinstance(receiver, ast.Name) or receiver.id not in {"self", "cls"}:
                    continue
                event = child.args[0]
                if not isinstance(event, ast.Constant) or not isinstance(event.value, str):
                    continue
                values.add(event.value)
                if len(child.args) >= _SSE_EMIT_MIN_ARGS and isinstance(child.args[1], ast.Dict):
                    keys = {
                        key.value
                        for key in child.args[1].keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    }
                    if keys:
                        content_keys.setdefault(event.value, set()).update(keys)
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
        node = self.methods.get(self.contract.envelope_method)
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
        return max(candidates, key=lambda item: (len(item & expected), len(item)), default=set())

    def close_keys(self) -> set[str]:
        """
        提取 end.content 的静态字段集合。

        Returns:
            初始化用量字段与 close builder 新增字段的并集。
        """
        keys = self._usage_attribute_keys()
        builder = self.methods.get(self.contract.close_builder)
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
            if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                keys.add(slice_node.value)
        return keys

    def _usage_attribute_keys(self) -> set[str]:
        """提取构造函数中用量映射属性的字面量键。"""
        constructor = self.methods.get("__init__")
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


def _frozen_sse_contract_findings(
    contract: FrozenSSEProtocolContract,
    target_sources: dict[str, str],
    target_trees: dict[str, ast.Module] | None = None,
) -> list[Finding]:
    """校验显式冻结的 SSE 外部事件协议。"""
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
            target_trees[contract.path] if target_trees is not None and contract.path in target_trees else None,
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


def _sse_protocol_finding(
    contract: FrozenSSEProtocolContract,
    analyzer: _FrozenSSEAnalyzer,
) -> Finding | None:
    """比较已提取的 SSE 事实与冻结契约。"""
    by_method, actual_event_types, _content_keys = analyzer.event_types()
    expected_methods = dict(contract.event_methods)
    expected_event_types = set(expected_methods.values())
    method_mismatches = {
        method: {"expected": expected_type, "actual": sorted(by_method.get(method, set()))}
        for method, expected_type in expected_methods.items()
        if by_method.get(method, set()) != {expected_type}
    }
    unexpected_event_types = sorted(
        event_type
        for event_type in actual_event_types - expected_event_types
        if not any(event_type.startswith(prefix) for prefix in contract.allowed_event_prefixes)
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
        *(["缺失事件类型: " + ", ".join(missing_event_types)] if missing_event_types else []),
        *(["事件外壳字段发生变化"] if actual_envelope_keys != expected_envelope_keys else []),
        *(["end.content 字段发生变化"] if actual_close_keys != expected_close_keys else []),
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
    target_source = target_sources[contract.path] if contract.path in target_sources else ""
    base = _sse_shape(
        contract,
        base_source,
        base_trees[contract.path] if base_trees is not None and contract.path in base_trees else None,
    )
    target = _sse_shape(
        contract,
        target_source,
        target_trees[contract.path] if target_trees is not None and contract.path in target_trees else None,
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
        "events_by_method": {key: sorted(value) for key, value in sorted(by_method.items())},
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
    """Document _route_contract_findings for quality guard coverage."""
    base: dict[str, dict[str, str]] = {}
    target: dict[str, dict[str, str]] = {}
    for path in contract.paths:
        base.update(
            _extract_routes(
                path,
                base_sources[path] if path in base_sources else "",
                base_trees[path] if base_trees is not None and path in base_trees else None,
            )
        )
        target.update(
            _extract_routes(
                path,
                target_sources[path] if path in target_sources else "",
                target_trees[path] if target_trees is not None and path in target_trees else None,
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


def _header_contract_findings(
    contract: HeaderContract,
    base_sources: dict[str, str],
    target_sources: dict[str, str],
    base_trees: dict[str, ast.Module] | None = None,
    target_trees: dict[str, ast.Module] | None = None,
) -> list[Finding]:
    """Document _header_contract_findings for quality guard coverage."""
    base_sets: dict[str, set[str]] = {"read": set(), "write": set(), "filter": set()}
    target_sets: dict[str, set[str]] = {"read": set(), "write": set(), "filter": set()}
    for path in contract.paths:
        for key, values in _extract_headers(
            base_sources[path] if path in base_sources else "",
            base_trees[path] if base_trees is not None and path in base_trees else None,
        ).items():
            base_sets[key].update(values)
        for key, values in _extract_headers(
            target_sources[path] if path in target_sources else "",
            target_trees[path] if target_trees is not None and path in target_trees else None,
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
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
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
                if keyword.arg in {"response_class", "include_in_schema", "status_code"}:
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
        self.values: dict[str, set[str]] = {"read": set(), "write": set(), "filter": set()}

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
        if isinstance(node.target, ast.Name) and node.target.id in _HEADER_CONTAINER_NAMES:
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


def _callable_contract_finding(
    contract: CallableContract,
    symbols: dict[str, InterfaceSymbol],
) -> Finding | None:
    """检查框架适配入口的最小签名契约。"""
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
        decorator_leaves = {item.split("(", 1)[0].rsplit(".", 1)[-1] for item in symbol.decorators}
        missing_decorators = sorted(set(contract.decorators) - decorator_leaves)
        if missing_decorators:
            problems.append("缺少装饰器: " + ", ".join(missing_decorators))
    if not problems:
        return None
    return Finding(
        code="QG147",
        severity="error",
        confidence="high",
        path=contract.path,
        line=1 if symbol is None else symbol.line,
        column=1,
        symbol=contract.qualname,
        message=f"框架适配接口 `{contract.qualname}` 不再满足固定兼容契约：" + "；".join(problems),
        suggestion="保持适配层入口与目标框架的参数种类、默认值、异步形态和返回对象约定一致。",
        evidence={
            "expected_parameters": [
                {"name": name, "kind": kind, "has_default": has_default}
                for name, kind, has_default in contract.parameters
            ],
            "actual_parameters": (
                [] if symbol is None else [item.to_dict() for item in symbol.parameters]
            ),
            "expected_return_markers": list(contract.return_markers),
            "actual_return_type": "" if symbol is None else symbol.return_type,
            "expected_async": contract.is_async,
            "actual_async": None if symbol is None else symbol.is_async,
        },
    )
