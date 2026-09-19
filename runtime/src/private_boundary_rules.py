from __future__ import annotations

import ast
import re

from .ast_utils import dotted_name, enclosing_class_name
from .config import is_test_path
from .model import Finding
from .policy_common import ParsedModule, direct_body_nodes, make_finding

UPPERCASE_CONSTANT_PATTERN = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
MIN_DUNDER_NAME_LENGTH = 5
MIN_REFLECTION_MEMBER_ARGS = 2
MIN_UNBOUND_DUNDER_ATTRIBUTE_ARGS = 2
REFLECTION_CALLS = frozenset(
    {
        "delattr",
        "getattr",
        "hasattr",
        "setattr",
        "vars",
        "builtins.delattr",
        "builtins.getattr",
        "builtins.hasattr",
        "builtins.setattr",
        "builtins.vars",
        "inspect.getattr_static",
        "inspect.getmembers",
        "inspect.getmembers_static",
        "operator.attrgetter",
        "operator.methodcaller",
    }
)
REFLECTION_BROAD_CALLS = frozenset(
    {
        "vars",
        "builtins.vars",
        "inspect.getmembers",
        "inspect.getmembers_static",
        "operator.methodcaller",
    }
)
REFLECTION_DUNDER_CALLS = frozenset({"__delattr__", "__getattribute__", "__setattr__"})
REFLECTION_DUNDER_ATTRIBUTES = frozenset(
    {
        "__bases__",
        "__delattr__",
        "__dict__",
        "__getattribute__",
        "__mro__",
        "__setattr__",
        "__subclasses__",
    }
)
PATCH_CALLS = frozenset(
    {
        "mock.patch",
        "mock.patch.object",
        "unittest.mock.patch",
        "unittest.mock.patch.object",
    }
)
REFLECTION_SUBSCRIPT_NAMES = frozenset({"delattr", "getattr", "hasattr", "setattr", "vars"})
REFLECTION_CALLABLE_KEYWORDS = frozenset(
    {"callback", "factory", "func", "function", "handler", "key", "target"}
)
REFLECTION_CALLABLE_ARGUMENT_LOOKUP = {
    "filter": frozenset({0}),
    "functools.partial": frozenset({0}),
    "map": frozenset({0}),
    "threading.Thread": frozenset({1}),
    "concurrent.futures.Executor.submit": frozenset({0}),
}
SCALAR_CONSTANT_TYPES = (str, bytes, int, float, complex, bool)


def _reflection_aliases(
    parsed: ParsedModule,
    assignments: list[ast.Assign | ast.AnnAssign],
) -> tuple[dict[str, str], dict[str, str]]:
    """固定点解析反射函数别名和静态字符串赋值链。"""
    constant_lookup: dict[str, str] = {}
    pending = [
        (target.id, assignment.value)
        for assignment in assignments
        for target in (
            assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
        )
        if isinstance(target, ast.Name)
    ]
    while pending:
        unresolved: list[tuple[str, ast.expr]] = []
        progress = False
        for name, expression in pending:
            value = _reflection_member_name(expression, constant_lookup)
            if value:
                constant_lookup[name] = value
                progress = True
            else:
                unresolved.append((name, expression))
        if not progress:
            break
        pending = unresolved

    alias_lookup = {
        local_name: imported_name
        for local_name, imported_name in parsed.facts.imports.items()
        if imported_name in REFLECTION_CALLS
    }
    alias_lookup.update({name: name for name in REFLECTION_SUBSCRIPT_NAMES})
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            source = dotted_name(assignment.value)
            root, separator, suffix = source.partition(".")
            imported_root = parsed.facts.imports.get(root, root)
            imported_source = f"{imported_root}.{suffix}" if separator else imported_root
            resolved = alias_lookup.get(source, imported_source)
            if (
                resolved not in REFLECTION_CALLS
                and resolved.rsplit(".", 1)[-1] not in REFLECTION_DUNDER_CALLS
            ):
                continue
            targets = (
                assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and alias_lookup.get(target.id) != resolved:
                    alias_lookup[target.id] = resolved
                    changed = True
    return alias_lookup, constant_lookup


def _reflection_bindings(
    parsed: ParsedModule,
    parents: dict[ast.AST, ast.AST],
    assignments: list[ast.Assign | ast.AnnAssign],
    alias_lookup: dict[str, str],
) -> dict[str, list[tuple[ast.AST | None, int]]]:
    """把接收反射结果的名称或属性映射回原始反射调用。"""
    bindings: dict[str, list[tuple[ast.AST | None, int]]] = {}
    for assignment in assignments:
        value = assignment.value
        if not isinstance(value, ast.Call):
            continue
        raw = dotted_name(value.func)
        root, separator, suffix = raw.partition(".")
        imported_root = parsed.facts.imports.get(root, root)
        imported_target = f"{imported_root}.{suffix}" if separator else imported_root
        resolved = alias_lookup.get(raw, imported_target)
        call_attribute = value.func.attr if isinstance(value.func, ast.Attribute) else ""
        if (
            resolved not in REFLECTION_CALLS
            and resolved.rsplit(".", 1)[-1] not in REFLECTION_DUNDER_CALLS
            and call_attribute not in REFLECTION_DUNDER_CALLS
        ):
            continue
        scope: ast.AST | None = assignment
        while scope in parents and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = parents[scope]
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = None
        targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
        for target in targets:
            binding = dotted_name(target)
            if binding:
                bindings.setdefault(binding, []).append((scope, id(value)))
    return bindings


def _reflection_member_name(node: ast.AST | None, constant_lookup: dict[str, str]) -> str:
    """静态还原反射成员名，覆盖常量、拼接、f-string、format 与 join。"""
    result = ""
    match node:
        case ast.Constant(value=str() as value):
            result = value
        case ast.Name(id=name):
            result = constant_lookup.get(name, "")
        case ast.BinOp(left=left_node, op=ast.Add(), right=right_node):
            left = _reflection_member_name(left_node, constant_lookup)
            right = _reflection_member_name(right_node, constant_lookup)
            result = left + right if left and right else ""
        case ast.JoinedStr(values=parts) if all(
            isinstance(item, (ast.Constant, ast.FormattedValue)) for item in parts
        ):
            values = [
                item.value
                if isinstance(item, ast.Constant)
                else _reflection_member_name(item.value, constant_lookup)
                for item in parts
            ]
            result = (
                "".join(values) if all(isinstance(item, str) and item for item in values) else ""
            )
        case ast.Call(
            func=ast.Attribute(value=base_node, attr="join"),
            args=[ast.List(elts=items) | ast.Tuple(elts=items)],
            keywords=[],
        ):
            base = _reflection_member_name(base_node, constant_lookup)
            values = [_reflection_member_name(item, constant_lookup) for item in items]
            result = base.join(values) if base and all(values) else ""
        case ast.Call(
            func=ast.Attribute(value=base_node, attr="format"),
            args=args,
            keywords=[],
        ):
            base = _reflection_member_name(base_node, constant_lookup)
            values = [_reflection_member_name(item, constant_lookup) for item in args]
            fields = list(re.finditer(r"\{(\d*)\}", base))
            raw_indexes = [match.group(1) for match in fields]
            indexes = [
                int(raw) if raw else sum(not previous for previous in raw_indexes[:position])
                for position, raw in enumerate(raw_indexes)
            ]
            valid_template = bool(fields) and "{" not in re.sub(r"\{\d*\}", "", base)
            if (
                base
                and all(values)
                and valid_template
                and all(index < len(values) for index in indexes)
            ):
                rendered = base
                for match, index in zip(fields, indexes, strict=True):
                    rendered = rendered.replace(match.group(0), values[index], 1)
                result = rendered
        case _:
            pass
    return result


def _fixed_contract_names(
    parsed: ParsedModule,
    parents: dict[ast.AST, ast.AST],
) -> tuple[dict[int, frozenset[str]], frozenset[str]]:
    """收集显式类型、固定构造实例、导入对象和本模块类名称。"""
    module_fixed_names = set(parsed.facts.imports)
    module_fixed_names.update(
        statement.name for statement in parsed.tree.body if isinstance(statement, ast.ClassDef)
    )
    fixed_names_by_scope: dict[int, frozenset[str]] = {
        id(parsed.tree): frozenset(module_fixed_names)
    }
    for function in (
        item
        for item in parsed.nodes
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        fixed_names = {
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
            if argument.annotation is not None
        }
        if function.args.vararg is not None and function.args.vararg.annotation is not None:
            fixed_names.add(function.args.vararg.arg)
        if function.args.kwarg is not None and function.args.kwarg.annotation is not None:
            fixed_names.add(function.args.kwarg.arg)
        ancestor: ast.AST | None = function
        while ancestor in parents:
            ancestor = parents[ancestor]
            if isinstance(ancestor, ast.ClassDef):
                fixed_names.update({"self", "cls"})
                break
        for candidate in direct_body_nodes(function):
            if isinstance(candidate, ast.AnnAssign) and isinstance(candidate.target, ast.Name):
                fixed_names.add(candidate.target.id)
            elif isinstance(candidate, ast.Assign) and isinstance(candidate.value, ast.Call):
                constructor = dotted_name(candidate.value.func)
                constructor_leaf = constructor.rsplit(".", 1)[-1]
                is_cast = constructor in {"cast", "typing.cast"} and bool(candidate.value.args)
                if not constructor_leaf or not (constructor_leaf[:1].isupper() or is_cast):
                    continue
                fixed_names.update(
                    target.id for target in candidate.targets if isinstance(target, ast.Name)
                )
        fixed_names_by_scope[id(function)] = frozenset(fixed_names)
    return fixed_names_by_scope, frozenset(module_fixed_names)


def _reflection_context(
    parsed: ParsedModule,
    parents: dict[ast.AST, ast.AST],
) -> tuple[dict[str, str], set[int], dict[str, str], dict[int, frozenset[str]], frozenset[str]]:
    """收集反射别名、调用传播、静态字符串和可确认契约名称。"""
    assignments = [
        node
        for node in parsed.nodes
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
    ]
    alias_lookup, constant_lookup = _reflection_aliases(parsed, assignments)
    bindings = _reflection_bindings(parsed, parents, assignments, alias_lookup)
    propagated = True
    while propagated:
        propagated = False
        for assignment in assignments:
            source = dotted_name(assignment.value)
            if source not in bindings:
                continue
            targets = (
                assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            )
            for target in targets:
                binding = dotted_name(target)
                if not binding:
                    continue
                current = bindings.setdefault(binding, [])
                missing = [item for item in bindings[source] if item not in current]
                if missing:
                    current.extend(missing)
                    propagated = True

    fixed_names_by_scope, patch_owners = _fixed_contract_names(parsed, parents)

    invoked: set[int] = set()
    for call in (node for node in parsed.nodes if isinstance(node, ast.Call)):
        parent = parents.get(call)
        if isinstance(parent, ast.Call) and parent.func is call:
            invoked.add(id(call))

        scope_chain: list[ast.AST] = []
        scope: ast.AST | None = call
        while scope in parents:
            scope = parents[scope]
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope_chain.append(scope)

        raw_target = dotted_name(call.func)
        root, separator, suffix = raw_target.partition(".")
        imported_root = parsed.facts.imports.get(root, root)
        call_target = f"{imported_root}.{suffix}" if separator else imported_root
        callable_bindings = [raw_target]
        callable_bindings.extend(
            dotted_name(argument)
            for index, argument in enumerate(call.args)
            if index in REFLECTION_CALLABLE_ARGUMENT_LOOKUP.get(call_target, ())
        )
        callable_bindings.extend(
            dotted_name(keyword.value)
            for keyword in call.keywords
            if keyword.arg in REFLECTION_CALLABLE_KEYWORDS
        )
        for binding in dict.fromkeys(
            item for item in callable_bindings if item and item in bindings
        ):
            origins = bindings[binding]
            owner_origins = {
                owner: [origin for candidate_owner, origin in origins if candidate_owner is owner]
                for owner in (*scope_chain, None)
            }
            visible_owner = next(
                (owner for owner in scope_chain if owner_origins.get(owner)),
                None,
            )
            invoked.update(owner_origins.get(visible_owner, ()))
    return alias_lookup, invoked, constant_lookup, fixed_names_by_scope, patch_owners


def _reflection_call_facts(
    parsed: ParsedModule,
    node: ast.Call,
    alias_lookup: dict[str, str],
    invoked_reflections: set[int],
    constant_lookup: dict[str, str],
) -> tuple[str, str, str, bool, bool, bool, ast.AST | None] | None:
    """解析一次反射调用的目标、接收者、成员名和严重性事实。"""
    raw_target = dotted_name(node.func)
    root, separator, suffix = raw_target.partition(".")
    imported_root = parsed.facts.imports.get(root, root)
    imported_target = f"{imported_root}.{suffix}" if separator else imported_root
    resolved_target = alias_lookup.get(raw_target, imported_target)
    reflected_subscript = False
    if isinstance(node.func, ast.Subscript):
        reflected_name = _reflection_member_name(node.func.slice, constant_lookup)
        base = dotted_name(node.func.value)
        reflected_subscript = bool(
            reflected_name in REFLECTION_SUBSCRIPT_NAMES
            and (
                base in {"__builtins__", "builtins.__dict__"}
                or isinstance(node.func.value, ast.Call)
            )
        )
        if reflected_subscript:
            resolved_target = f"dynamic[{reflected_name}]"

    call_attribute = node.func.attr if isinstance(node.func, ast.Attribute) else ""
    reflection_call = (
        resolved_target in REFLECTION_CALLS
        or resolved_target.rsplit(".", 1)[-1] in REFLECTION_DUNDER_CALLS
        or call_attribute in REFLECTION_DUNDER_CALLS
        or reflected_subscript
    )
    if not reflection_call:
        return None

    member_node: ast.AST | None = None
    receiver_node: ast.AST | None = node.args[0] if node.args else None
    canonical_target = alias_lookup.get(raw_target, resolved_target)
    if call_attribute in REFLECTION_DUNDER_CALLS and not canonical_target:
        canonical_target = call_attribute
    if call_attribute in REFLECTION_DUNDER_CALLS:
        bound_receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        if (
            dotted_name(bound_receiver) in {"object", "type"}
            and len(node.args) >= MIN_UNBOUND_DUNDER_ATTRIBUTE_ARGS
        ):
            receiver_node, member_node = node.args[0], node.args[1]
        else:
            receiver_node = bound_receiver
            member_node = node.args[0] if node.args else None
    elif canonical_target in {"operator.attrgetter", "operator.methodcaller"}:
        receiver_node = None
        member_node = node.args[0] if node.args else None
    elif len(node.args) >= MIN_REFLECTION_MEMBER_ARGS:
        member_node = node.args[1]

    member_name = _reflection_member_name(member_node, constant_lookup)
    private_member = bool(member_name and single_private_name(member_name))
    broad_reflection = bool(
        canonical_target in REFLECTION_BROAD_CALLS
        or reflected_subscript
        or canonical_target.rsplit(".", 1)[-1] in REFLECTION_DUNDER_CALLS
        or call_attribute in REFLECTION_DUNDER_CALLS
    )
    invoked = id(node) in invoked_reflections or canonical_target == "operator.methodcaller"
    receiver = dotted_name(receiver_node) if receiver_node is not None else ""
    return (
        canonical_target,
        receiver,
        member_name,
        private_member,
        broad_reflection,
        invoked,
        receiver_node,
    )


def _module_constant_findings(parsed: ParsedModule) -> list[Finding]:
    """检查模块级字面量常量是否使用全大写命名。"""
    findings: list[Finding] = []
    for statement in parsed.tree.body:
        if isinstance(statement, ast.Assign):
            assignments = [(target, statement.value) for target in statement.targets]
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            assignments = [(statement.target, statement.value)]
        else:
            assignments = []
        for target, value in assignments:
            if not isinstance(target, ast.Name) or UPPERCASE_CONSTANT_PATTERN.match(target.id):
                continue
            if target.id.startswith("__") and target.id.endswith("__"):
                continue
            pending = [value]
            constant_value = True
            while pending:
                candidate = pending.pop()
                if isinstance(candidate, ast.Tuple):
                    pending.extend(candidate.elts)
                elif not (
                    isinstance(candidate, ast.Constant)
                    and isinstance(candidate.value, SCALAR_CONSTANT_TYPES)
                ):
                    constant_value = False
                    break
            if not constant_value:
                continue
            findings.append(
                make_finding(
                    parsed.facts,
                    target,
                    "QG150",
                    f"模块级常量 `{target.id}` 应使用全大写命名。",
                    symbol=(
                        f"{parsed.facts.module}.{target.id}" if parsed.facts.module else target.id
                    ),
                    severity="warning",
                    confidence="high",
                    suggestion=(
                        "文件级字符串、数值或元组常量使用 REACT_SKILLS_PROMPT "
                        "这类全大写名称；局部变量放入函数内部。"
                    ),
                    evidence={"name": target.id, "value_kind": type(value).__name__},
                )
            )
    return findings


def _runtime_contract_patch_findings(
    parsed: ParsedModule,
    node: ast.AST,
    patch_owners: frozenset[str],
) -> list[Finding]:
    """检查 production monkey patch、类/模块属性替换和 patch API。"""
    if isinstance(node, ast.Call):
        raw_target = dotted_name(node.func)
        root, separator, suffix = raw_target.partition(".")
        imported_root = parsed.facts.imports.get(root, root)
        call_target = f"{imported_root}.{suffix}" if separator else imported_root
        if call_target not in PATCH_CALLS and not raw_target.endswith(
            ("monkeypatch.setattr", "monkeypatch.delattr")
        ):
            return []
        return [
            make_finding(
                parsed.facts,
                node,
                "QG186",
                f"生产代码通过 `{call_target}` 安装运行时补丁。",
                symbol=parsed.facts.module,
                severity="critical",
                confidence="high",
                suggestion=(
                    "mock.patch/monkeypatch 仅允许 tests；生产代码必须修改权威实现、"
                    "使用显式依赖注入或正式插件注册接口。"
                ),
                evidence={"patch_api": call_target, "test_context": False},
            )
        ]
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    elif isinstance(node, ast.Delete):
        targets = node.targets
    else:
        return []
    findings: list[Finding] = []
    for target_node in targets:
        if not isinstance(target_node, ast.Attribute):
            continue
        receiver = dotted_name(target_node.value)
        receiver_root = receiver.partition(".")[0]
        runtime_patch = bool(
            receiver_root in patch_owners
            or receiver.endswith(".__class__")
            or (
                isinstance(target_node.value, ast.Call)
                and dotted_name(target_node.value.func) == "type"
            )
        )
        if not runtime_patch:
            continue
        findings.append(
            make_finding(
                parsed.facts,
                target_node,
                "QG186",
                f"生产代码在运行时修改类或模块契约 `{receiver}.{target_node.attr}`。",
                symbol=parsed.facts.module,
                severity="critical",
                confidence="high",
                suggestion=(
                    "禁止 monkey patch、运行时替换方法或修改导入模块/类属性；"
                    "应修改权威定义、显式注入依赖或使用正式扩展接口。"
                ),
                evidence={
                    "receiver": receiver,
                    "member_name": target_node.attr,
                    "operation": type(node).__name__,
                    "test_context": False,
                },
            )
        )
    return findings


def check_private_boundary_rules(parsed: ParsedModule) -> list[Finding]:
    """检查 private 所有权、生产反射和模块常量命名。

    Args:
        parsed: 当前已解析模块及其静态事实。

    Returns:
        private 越界、生产反射和模块常量命名相关发现。
    """
    findings: list[Finding] = []
    parents = parsed.parents
    (
        alias_lookup,
        invoked_reflections,
        constant_lookup,
        fixed_names_by_scope,
        patch_owners,
    ) = _reflection_context(parsed, parents)
    production_module = not is_test_path(parsed.facts.path)
    for node in parsed.nodes:
        if production_module:
            findings.extend(_runtime_contract_patch_findings(parsed, node, patch_owners))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            findings.extend(check_private_import(parsed, node))
        elif isinstance(node, ast.Attribute):
            finding = check_private_attribute_access(parsed, node, parents)
            if finding is not None:
                findings.append(finding)
        elif isinstance(node, ast.Call):
            finding = check_dynamic_private_access(
                parsed,
                node,
                parents,
                alias_lookup,
                invoked_reflections,
                constant_lookup,
                fixed_names_by_scope,
                patch_owners,
            )
            if finding is not None:
                findings.append(finding)

    findings.extend(_module_constant_findings(parsed))
    return findings


def check_private_import(parsed: ParsedModule, node: ast.Import | ast.ImportFrom) -> list[Finding]:
    """检查导入语句是否跨模块依赖单下划线 private 符号。

    Args:
        parsed: 当前已解析模块及其静态事实。
        node: 待检查的 import 或 from-import 语句。

    Returns:
        当前导入语句产生的 private 边界发现。
    """
    findings: list[Finding] = []
    for alias in node.names:
        local_name = alias.asname or alias.name.split(".", 1)[0]
        public_reexport = parsed.facts.path.name == "__init__.py" and not single_private_name(
            local_name
        )
        if public_reexport:
            continue
        private_parts = []
        if isinstance(node, ast.ImportFrom):
            private_parts.extend(
                part for part in (node.module or "").split(".") if single_private_name(part)
            )
        private_parts.extend(part for part in alias.name.split(".") if single_private_name(part))
        if not private_parts:
            continue
        test_context = is_test_path(parsed.facts.path)
        findings.append(
            make_finding(
                parsed.facts,
                node,
                "QG149",
                f"跨模块导入私有符号 `{alias.name}`。",
                symbol=parsed.facts.module,
                severity="warning" if test_context else "critical",
                confidence="high",
                suggestion=(
                    "测试代码仅可为验证内部不变量或回归行为有限访问私有符号，"
                    "不要为测试新增生产 public API；运行时代码需要正式公开接口。"
                    if test_context
                    else "需要被其他模块使用的函数、变量或模块应改为公开名称；"
                    "包级 __init__.py 可将内部实现显式重导出为公开别名。"
                ),
                evidence={
                    "private_parts": private_parts,
                    "local_name": local_name,
                    "test_context": test_context,
                },
            )
        )
    return findings


def check_private_attribute_access(
    parsed: ParsedModule,
    node: ast.Attribute,
    parents: dict[ast.AST, ast.AST],
) -> Finding | None:
    """检查 private 属性越界和 dunder 反射。

    Args:
        parsed: 当前已解析模块及其静态事实。
        node: 待检查的属性访问节点。
        parents: AST 子节点到直接父节点的映射。

    Returns:
        命中 private 越界或 dunder 反射时返回 Finding，否则返回 None。
    """
    test_context = is_test_path(parsed.facts.path)
    receiver = dotted_name(node.value)
    if receiver == "os" and node.attr == "_exit":
        return None
    current_class = enclosing_class_name(node, parents)
    if node.attr in REFLECTION_DUNDER_ATTRIBUTES:
        parent = parents.get(node)
        if isinstance(parent, ast.Call) and parent.func is node:
            return None
        if test_context:
            return None
        return make_finding(
            parsed.facts,
            node,
            "QG183",
            f"生产代码通过 `{node.attr}` 绕过静态成员契约。",
            symbol=parsed.facts.module,
            severity="critical",
            confidence="high",
            suggestion=(
                "禁止通过 __dict__/__getattribute__/MRO 等反射路径发现或调用成员；"
                "改为显式 public 接口、Protocol 或由正确所有者直接调用。"
            ),
            evidence={
                "receiver": receiver,
                "attribute": node.attr,
                "reflection": True,
                "test_context": False,
            },
        )
    if not single_private_name(node.attr):
        return None
    allowed = receiver in {"self", "cls"} or bool(current_class and receiver == current_class)
    if allowed:
        return None
    return make_finding(
        parsed.facts,
        node,
        "QG149",
        f"外部访问私有成员 `{receiver}.{node.attr}`。",
        symbol=parsed.facts.module,
        severity="warning" if test_context else "critical",
        confidence="high" if receiver else "medium",
        suggestion=(
            "测试代码仅可为验证内部不变量或回归行为有限访问私有成员，"
            "优先集中在 Test 类、fixture 或测试模块中，不要为测试新增生产 public API。"
            if test_context
            else "单下划线方法和变量只能在定义类或模块内部使用；"
            "若需要被其他类或模块调用，应移除下划线并把它作为正式公开接口维护。"
        ),
        evidence={
            "receiver": receiver,
            "attribute": node.attr,
            "current_class": current_class,
            "test_context": test_context,
        },
    )


def _is_frozen_dataclass_init_assignment(
    node: ast.Call,
    parents: dict[ast.AST, ast.AST],
    target: str,
    receiver: str,
    member_name: str,
) -> bool:
    """识别 frozen dataclass 在 ``__init__`` 中的标准字段初始化。

    Args:
        node: 当前 ``object.__setattr__`` 调用节点。
        parents: AST 子节点到直接父节点的映射。
        target: 已解析的反射调用名称。
        receiver: 调用接收者的静态名称。
        member_name: 可静态恢复的目标字段名。

    Returns:
        仅当调用属于 frozen dataclass 的固定字段初始化时返回 True。
    """
    if target != "object.__setattr__" or receiver != "self" or not member_name:
        return False
    current: ast.AST | None = node
    function: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    owner: ast.ClassDef | None = None
    while current in parents:
        current = parents[current]
        if function is None and isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = current
            continue
        if function is not None and isinstance(current, ast.ClassDef):
            owner = current
            break
    if function is None or function.name != "__init__" or owner is None:
        return False
    for decorator in owner.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if dotted_name(decorator.func).rsplit(".", 1)[-1] != "dataclass":
            continue
        return any(
            keyword.arg == "frozen"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in decorator.keywords
        )
    return False


def check_dynamic_private_access(
    parsed: ParsedModule,
    node: ast.Call,
    parents: dict[ast.AST, ast.AST],
    alias_lookup: dict[str, str],
    invoked_reflections: set[int],
    constant_lookup: dict[str, str],
    fixed_names_by_scope: dict[int, frozenset[str]],
    patch_owners: frozenset[str],
) -> Finding | None:
    """检查生产反射、反射调用和 tests 中的动态 private 访问。

    Args:
        parsed: 当前已解析模块及其静态事实。
        node: 待检查的调用节点。
        parents: AST 子节点到直接父节点的映射。
        alias_lookup: 反射 API 局部别名到标准名称的查找表。
        invoked_reflections: 已确认被调用或注册为回调的反射调用节点标识。
        constant_lookup: 可静态还原的字符串名称查找表。
        fixed_names_by_scope: 各词法作用域内可确认静态契约的名称。
        patch_owners: 导入模块、导入类和本模块类形成的运行时契约所有者。

    Returns:
        命中生产反射或 tests 动态 private 访问时返回 Finding，否则返回 None。
    """
    facts = _reflection_call_facts(parsed, node, alias_lookup, invoked_reflections, constant_lookup)
    if facts is None:
        return None
    (
        target,
        receiver,
        member_name,
        private_member,
        broad_reflection,
        invoked,
        receiver_node,
    ) = facts
    if _is_frozen_dataclass_init_assignment(
        node,
        parents,
        target,
        receiver,
        member_name,
    ):
        return None
    test_context = is_test_path(parsed.facts.path)
    current_class = enclosing_class_name(node, parents)
    allowed_private = receiver in {"self", "cls"} or bool(
        current_class and receiver == current_class
    )
    if test_context:
        if not private_member or allowed_private:
            return None
        return make_finding(
            parsed.facts,
            node,
            "QG149",
            f"测试代码通过 `{target}` 动态访问私有符号 `{receiver}.{member_name}`。",
            symbol=parsed.facts.module,
            severity="warning",
            confidence="high" if receiver else "medium",
            suggestion="测试代码只可在明确回归场景中有限访问 private；不要新增测试专用 public API。",
            evidence={
                "accessor": target,
                "receiver": receiver,
                "private_name": member_name,
                "current_class": current_class,
                "test_context": True,
                "reflection": True,
            },
        )

    receiver_root = receiver.partition(".")[0]
    scope_names: set[str] = set()
    scope: ast.AST | None = node
    while scope in parents:
        scope = parents[scope]
        scope_id = id(scope)
        if scope_id in fixed_names_by_scope:
            scope_names.update(fixed_names_by_scope[scope_id])
    module_scope_id = id(parsed.tree)
    if module_scope_id in fixed_names_by_scope:
        scope_names.update(fixed_names_by_scope[module_scope_id])
    statically_typed = bool(receiver_root and receiver_root in scope_names)
    patch_owner = bool(
        target.rsplit(".", 1)[-1] in {"setattr", "delattr"}
        and (
            receiver_root in patch_owners
            or receiver.endswith(".__class__")
            or (isinstance(receiver_node, ast.Call) and dotted_name(receiver_node.func) == "type")
        )
    )
    critical = private_member or broad_reflection or invoked
    variants = (
        (patch_owner, "QG186", f"生产代码通过 `{target}` 在运行时修改类或模块契约。"),
        (
            private_member,
            "QG183",
            f"生产代码通过反射访问 private 成员 `{receiver}.{member_name}`。",
        ),
        (invoked, "QG183", f"生产代码通过 `{target}` 动态取得并调用成员。"),
        (broad_reflection, "QG183", f"生产代码使用 `{target}` 枚举或绕过静态成员契约。"),
        (
            statically_typed,
            "QG184",
            f"生产代码对静态契约对象使用 `{target}` 动态探测或修改成员。",
        ),
    )
    selected = next(((code, message) for enabled, code, message in variants if enabled), None)
    if selected is None:
        return None
    code, message = selected
    return make_finding(
        parsed.facts,
        node,
        code,
        message,
        symbol=parsed.facts.module,
        severity="critical" if critical or patch_owner else "error",
        confidence="high"
        if member_name or invoked or broad_reflection or statically_typed
        else "medium",
        suggestion=(
            "运行时改类/模块契约、反射调用和 private 反射不可解释保留；"
            "必须改为直接调用正式 public 接口，或将能力迁回正确所有者。"
            if critical or patch_owner
            else "静态类型对象不得用反射猜测固定成员；请直接访问公开字段/方法，"
            "或用 Protocol、联合类型和显式 Optional 契约表达可选性。"
        ),
        evidence={
            "accessor": target,
            "receiver": receiver,
            "member_name": member_name,
            "private_member": private_member,
            "invoked": invoked,
            "broad_reflection": broad_reflection,
            "statically_typed": statically_typed,
            "runtime_contract_patch": patch_owner,
            "test_context": False,
        },
    )


def single_private_name(name: str) -> bool:
    """判断名称是否为单下划线 private，而不是双下划线协议名。

    Args:
        name: 待检查的属性、函数、类或模块片段名称。

    Returns:
        名称以单下划线开头且不是 dunder 协议名时返回 True。
    """
    dunder = len(name) >= MIN_DUNDER_NAME_LENGTH and name.startswith("__") and name.endswith("__")
    return name.startswith("_") and not dunder
