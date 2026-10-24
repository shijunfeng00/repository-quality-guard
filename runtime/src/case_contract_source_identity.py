"""Lexically proven callable-source identities for test-contract rules.

One binding oracle is shared by Case entrypoint lookup and direct-call
witness detection.  Ambiguous ownership is unresolved, not a false certainty.
"""

from __future__ import annotations

import ast

def bound_names(body: list[ast.stmt], skip_nested: bool) -> set[str]:
    """Collect binding names in the immediate syntactic scope.

    The returned set distinguishes an imported callable from a locally
    shadowed name without inspecting any runtime values.

    Args:
        body: AST statements belonging to the scope being inspected.
        skip_nested: Stop at inner function, lambda, and class definitions.

    Returns:
        Names bound by imports, assignment targets, and declarations.
    """
    names: set[str] = set()
    for statement in body:
        pending = [statement]
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                if skip_nested:
                    continue
            if isinstance(node, ast.Lambda) and skip_nested:
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, (ast.ImportFrom, ast.Import)):
                names.update(
                    alias.asname or alias.name.split(".")[0] for alias in node.names
                )
            pending.extend(ast.iter_child_nodes(node))
    return names


def entrypoint_return_bindings(
    method: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.Module
) -> set[str]:
    """Resolve source-bound callable returns without crossing lexical scopes.

    A local import shadows a module import even when both use the same name.
    Treat only a straight-line import or single assignment preceding the
    return as a proven binding; opaque branches remain unresolved.

    Args:
        method: Source of the Case's get_enterpoint implementation.
        tree: Whole test module containing imported production targets.

    Returns:
        Source-qualified callable identities established at return sites.
    """
    refs: set[str] = set()
    locals_bound: dict[str, str] = {}
    imports = straight_line_local_imports(method)
    lexical = bound_names(method.body, skip_nested=True)
    parameters = {
        arg.arg
        for arg in (
            [*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs]
        )
    }
    if method.args.vararg is not None:
        parameters.add(method.args.vararg.arg)
    if method.args.kwarg is not None:
        parameters.add(method.args.kwarg.arg)
    counts: dict[str, int] = {}
    for node in ast.walk(method):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    counts[target.id] = counts.get(target.id, 0) + 1

    def resolve(expr: ast.expr, lineno: int) -> str | None:
        """Resolve one reference using the lexical binding at the call site.

        Args:
            expr: Callable reference expression whose owner is being proved.
            lineno: Source line at which the reference is evaluated.

        Returns:
            Qualified owner identity, or None when the binding is unresolved.
        """
        parts: list[str] = []
        node = expr
        while isinstance(node, ast.Attribute):
            parts.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        name = node.id
        if name in parameters:
            return None
        if name in imports:
            identity, import_line = imports[name]
            origin = identity if import_line < lineno else None
        elif name in locals_bound:
            origin = locals_bound[name]
        elif name in lexical:
            return None
        else:
            origin = stable_reference(node, tree)
        if origin is None:
            return None
        return origin + ("." + ".".join(parts) if parts else "")

    for item in method.body:
        if isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            if item.value is None:
                continue
            origin = resolve(item.value, item.lineno)
            if origin is None:
                continue
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and counts.get(target.id) == 1
                    and target.id not in parameters
                    and target.id not in imports
                ):
                    locals_bound[target.id] = origin
        elif isinstance(item, ast.Return) and item.value is not None:
            origin = resolve(item.value, item.lineno)
            if origin is not None:
                refs.add(origin)
    return refs


def stable_reference(
    expr: ast.expr, tree: ast.Module, seen: frozenset[str] = frozenset()
) -> str | None:
    """Return a lexical source identity only when top-level bindings are stable.

    Distinguish import aliases and assignment aliases from same-spelled locals;
    reject names redefined at module scope rather than guessing a provenance.

    Args:
        expr: Name or qualified attribute that refers to a callable.
        tree: Module AST defining its unique, unshadowed binding.
        seen: Alias names already visited to prevent recursive cycles.

    Returns:
        Stable owner identity, or None for unresolved or ambiguous bindings.
    """
    parts: list[str] = []
    node = expr
    while isinstance(node, ast.Attribute):
        parts.insert(0, node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    name = node.id
    if name in seen:
        return None
    declarations: list[tuple[str, ast.expr | None]] = []
    for item in tree.body:
        if isinstance(item, ast.ImportFrom):
            for alias in item.names:
                if (alias.asname or alias.name) == name:
                    declarations.append((f"import:{item.module}.{alias.name}", None))
        elif isinstance(item, ast.Import):
            for alias in item.names:
                if (alias.asname or alias.name.split(".")[0]) == name:
                    declarations.append((f"import:{alias.name}", None))
        elif isinstance(item, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in item.targets):
                declarations.append(("alias", item.value))
        elif (
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and item.name == name
        ):
            declarations.append((f"local:{name}", None))
        elif (
            isinstance(item, (ast.AnnAssign, ast.AugAssign))
            and isinstance(item.target, ast.Name)
            and item.target.id == name
        ):
            declarations.append(("unknown", None))
    if len(declarations) != 1:
        return None
    binding, target = declarations[0]
    if binding == "alias" and isinstance(target, (ast.Name, ast.Attribute)):
        # Single-hop aliases are followed only when their source is independently
        # bound; no runtime-dependent alias state is inferred.
        found = stable_reference(target, tree, seen | {name})
        if found is None:
            return None
        binding = found
    elif binding == "alias":
        return None
    if binding == "unknown":
        return None
    return binding + ("." + ".".join(parts) if parts else "")


def straight_line_local_imports(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, tuple[str, int]]:
    """Prove direct local imports without treating conditional bindings as certain.

    A name must have exactly one lexical binding in the function.  Only a
    module-scope-in-the-method import is accepted, and its reference is usable
    after that import, never before it.

    Args:
        method: Function body supplying candidate local import statements.

    Returns:
        Qualified import owners and their source lines for unique bindings.
    """
    declarations: dict[str, tuple[str, int]] = {}
    counts: dict[str, int] = {}
    for statement in method.body:
        if isinstance(statement, ast.ImportFrom) and statement.level == 0 and statement.module:
            for alias in statement.names:
                name = alias.asname or alias.name
                declarations[name] = (f"import:{statement.module}.{alias.name}", statement.lineno)
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                name = alias.asname or alias.name.split(".")[0]
                declarations[name] = (f"import:{alias.name}", statement.lineno)
    for statement in method.body:
        for name in bound_names([statement], skip_nested=True):
            counts[name] = counts.get(name, 0) + 1
    return {name: binding for name, binding in declarations.items() if counts.get(name) == 1}


def entrypoint_call_witness(
    method: ast.FunctionDef | ast.AsyncFunctionDef, targets: set[str], tree: ast.Module
) -> ast.Call | None:
    """Locate a proven invocation of the declared production target.

    This trace covers direct calls, single-assignment aliases, and local
    imports only after their source lines. A shadowed or dynamic binding
    cannot establish production-target identity.

    Args:
        method: Case method whose invocation statements are inspected.
        targets: Independently established production-callable identities.
        tree: Module AST needed for module-level import provenance.

    Returns:
        An invocation AST node, or None when no call is proven.
    """
    local_aliases: dict[str, tuple[str, int]] = {}
    assignments: dict[str, int] = {}
    for stmt in ast.walk(method):
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            values = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for target in values:
                if isinstance(target, ast.Name):
                    assignments[target.id] = assignments.get(target.id, 0) + 1
    parameters = {
        arg.arg
        for arg in (
            [*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs]
        )
    }
    lexical_locals = bound_names(method.body, skip_nested=True)
    local_aliases.update(
        {name: binding for name, binding in straight_line_local_imports(method).items()
         if name not in parameters}
    )
    receiver = method.args.args[0].arg if method.args.args else None
    if method.args.vararg is not None:
        parameters.add(method.args.vararg.arg)
    if method.args.kwarg is not None:
        parameters.add(method.args.kwarg.arg)
    # Local aliases only count if assigned once in this direct function body.
    for stmt in method.body:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        expression = stmt.value
        if expression is None:
            continue
        names = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        for target in names:
            if (
                not isinstance(target, ast.Name)
                or assignments.get(target.id) != 1
                or target.id in parameters
            ):
                continue
            bound: str | None = None
            if isinstance(expression, (ast.Name, ast.Attribute)):
                bound = stable_reference(expression, tree)
                if bound is None and isinstance(expression, ast.Name):
                    previous = local_aliases.get(expression.id)
                    if previous is not None and previous[1] < stmt.lineno:
                        bound = previous[0]
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Attribute)
                and isinstance(expression.func.value, ast.Name)
                and expression.func.value.id == receiver
                and expression.func.attr == "get_enterpoint"
                and not expression.args
                and not expression.keywords
            ):
                bound = "<declared-entrypoint>"
            if bound in targets or bound == "<declared-entrypoint>":
                local_aliases[target.id] = (bound, stmt.lineno)
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        invoked = node.func
        if (isinstance(invoked, ast.NamedExpr)
                and isinstance(invoked.value, (ast.Name, ast.Attribute))
                and stable_reference(invoked.value, tree) in targets
                and isinstance(invoked.target, ast.Name)
                and assignments.get(invoked.target.id) == 1):
            return node
        if (
            isinstance(invoked, ast.Call)
            and isinstance(invoked.func, ast.Attribute)
            and invoked.func.attr == "get_enterpoint"
            and isinstance(invoked.func.value, ast.Name)
            and invoked.func.value.id == receiver
            and not invoked.args
            and not invoked.keywords
        ):
            return node
        if isinstance(invoked, ast.Name):
            if (invoked.id in local_aliases
                    and local_aliases[invoked.id][1] < node.lineno
                    and local_aliases[invoked.id][0] in targets | {"<declared-entrypoint>"}):
                return node
            if invoked.id in parameters or invoked.id in lexical_locals:
                continue
        if isinstance(invoked, ast.Attribute) and isinstance(invoked.value, ast.Name):
            imported = local_aliases.get(invoked.value.id)
            if imported is not None and imported[1] < node.lineno:
                if imported[0] + "." + invoked.attr in targets:
                    return node
        if isinstance(invoked, (ast.Name, ast.Attribute)):
            # A module alias shadowed by a local assignment, loop variable,
            # except-binding or with-binding has no proven shared identity.
            base = invoked
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name) and base.id in lexical_locals:
                continue
            reference = stable_reference(invoked, tree)
            if reference is not None and reference in targets:
                return node
    return None
