"""Source-bound Python receiver candidates enriching the shared topology.

Declared/constructed receiver types establish *possible* calls, not runtime
MUST targets. No inference is made from bare method names or fallback values.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .topology_facts import UsageEdge, UsageKind


def _local_calls(body):
    """Collect calls and writes within one lexical callable, never nested owners."""

    class _Visitor(ast.NodeVisitor):
        """Read one lexical owner without entering nested callable bodies."""

        def __init__(self):
            """Prepare lexical call and reassignment evidence containers.

            Returns:
                None.
            """
            self.calls = []
            self.writes = []

        def visit_FunctionDef(self, node):
            """Skip nested callable declarations owned by a different symbol.

            Args:
                node: Nested function expression or declaration.

            Returns:
                None.
            """
            return None

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        visit_Lambda = visit_FunctionDef

        def visit_Call(self, node):
            """Record all calls belonging to the current lexical owner.

            Args:
                node: Source-located call expression.

            Returns:
                None.
            """
            self.calls.append(node)
            self.generic_visit(node)

        def visit_Name(self, node):
            """Find receiver reassignments that invalidate a declared type.

            Args:
                node: Name expression with load, store or delete context.

            Returns:
                None.
            """
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self.writes.append(node.id)

    visitor = _Visitor()
    for statement in body:
        visitor.visit(statement)
    return visitor.calls, visitor.writes


def _declared_receiver_types(graph, owner, function, fields=None):
    """Return unique declared class identities for unmodified formal receivers."""
    calls, writes = _local_calls(function.body)
    types = {}
    fields = fields or {}
    args = function.args.posonlyargs + function.args.args + function.args.kwonlyargs
    for arg in args:
        if arg.annotation is None or arg.arg in writes:
            continue
        class_id = graph.resolve_authored_class(
            owner.module, owner.node_id, arg.annotation
        )
        if class_id is not None:
            types[arg.arg] = class_id
    for statement in function.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            continue
        variable = targets[0].id
        if writes.count(variable) != 1 or variable in types:
            continue
        value = statement.value
        if isinstance(statement, ast.AnnAssign) and statement.annotation is not None:
            class_id = graph.resolve_authored_class(
                owner.module, owner.node_id, statement.annotation
            )
        else:
            class_id = _receiver_class(graph, owner, value, types, fields)
        if class_id is not None:
            types[variable] = class_id
    return calls, types


def _field_receivers(graph, owner, class_node, injected=None):
    """Resolve uniquely initialized self fields; later writes invalidate candidates."""
    constructors = [
        item
        for item in class_node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == "__init__"
    ]
    if len(constructors) != 1:
        return {}
    constructor = constructors[0]
    _, constructor_types = _declared_receiver_types(graph, owner, constructor)
    constructor_types.update(injected or {})
    written = {}
    for member in class_node.body:
        if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for item in ast.walk(member):
            if not isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                continue
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    written.setdefault(target.attr, []).append(item)
    result = {}
    for statement in constructor.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        if len(targets) != 1 or not isinstance(targets[0], ast.Attribute):
            continue
        target = targets[0]
        if not isinstance(target.value, ast.Name) or target.value.id != "self":
            continue
        if len(written[target.attr]) != 1:
            continue
        value = statement.value
        resolved = _receiver_class(graph, owner, value, constructor_types, {})
        if resolved is not None:
            result[target.attr] = resolved
    return result


def _receiver_class(graph, owner, func, local, fields):
    """Resolve a simple local, initialized self field, or inline constructor."""
    if isinstance(func, ast.Name):
        return local.get(func.id)
    if isinstance(func, ast.Call):
        return graph.resolve_authored_class(owner.module, owner.node_id, func.func)
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
    ):
        return fields.get(func.attr)
    return None


def _constructor_injection_types(graph, classes, methods, known_fields):
    """Bind constructor parameters from explicit call sites with typed arguments.

    Args:
        graph: Source-bounded repository symbol resolver.
        classes: Authored class declarations keyed by class symbol ID.
        methods: Authored function declarations paired with their owner nodes.
        known_fields: First-pass constructor field type proofs.

    Returns:
        Consistent constructor parameter identities for proven call sites only.
    """
    observed = {}
    for owner, node in methods:
        field_types = known_fields.get(f"{owner.module}.{owner.owner}", {})
        calls, local_types = _declared_receiver_types(graph, owner, node, field_types)
        for call in calls:
            class_id = graph.resolve_authored_class(
                owner.module, owner.node_id, call.func
            )
            declared = classes.get(class_id)
            if declared is None or any(isinstance(x, ast.Starred) for x in call.args):
                continue
            constructors = [
                item
                for item in declared.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == "__init__"
            ]
            if len(constructors) != 1 or any(k.arg is None for k in call.keywords):
                continue
            args = constructors[0].args
            parameters = args.posonlyargs + args.args
            if parameters and parameters[0].arg == "self":
                parameters = parameters[1:]
            formal = [x.arg for x in parameters]
            if len(call.args) > len(formal):
                continue
            actual = dict(zip(formal, call.args))
            for keyword in call.keywords:
                if keyword.arg in formal or keyword.arg in {
                    x.arg for x in args.kwonlyargs
                }:
                    actual[keyword.arg] = keyword.value
            for parameter, expression in actual.items():
                resolved = _receiver_class(
                    graph, owner, expression, local_types, field_types
                )
                observed.setdefault((class_id, parameter), set()).add(resolved)
    inferred = {}
    for (class_id, parameter), possibilities in observed.items():
        if len(possibilities) == 1 and None not in possibilities:
            if class_id not in inferred:
                inferred[class_id] = {}
            inferred[class_id][parameter] = next(iter(possibilities))
    return inferred


def _method_receiver_edges(graph, owner, node, fields):
    """Resolve a single lexical callable without guessing owner or polymorphism.

    Args:
        graph: Authored project resolver.
        owner: Source-bound method/function identity.
        node: Authored function syntax tree.
        fields: Proven receiver fields of the lexical class, if any.

    Returns:
        Source-located possible call edges, never unverified direct calls.
    """
    edges = []
    calls, receivers = _declared_receiver_types(graph, owner, node, fields)
    for call in calls:
        func = call.func
        if not isinstance(func, ast.Attribute):
            continue
        class_id = _receiver_class(graph, owner, func.value, receivers, fields)
        if class_id is None:
            continue
        target = graph.resolve_authored_method(class_id, func.attr)
        if target is None or target == owner.node_id:
            continue
        edges.append(
            UsageEdge(
                source_id=owner.node_id,
                target_id=target,
                kind=UsageKind.MAY_DISPATCH,
                path=Path(owner.path),
                line=call.lineno,
                confidence="medium",
            )
        )
    return edges


def _receiver_declarations(graph):
    """Index real class declarations and callable owners for typed propagation.

    Args:
        graph: A normalized source graph with source-located authored symbols.

    Returns:
        Owner-matched class AST, receiver fields, and lexical callable nodes.
    """
    by_path = {path: {} for path in graph.snapshot.paths}
    classes_by_path = {path: {} for path in graph.snapshot.paths}
    for symbol in graph.nodes.values():
        if symbol.kind in {"function", "method", "test"}:
            by_path[symbol.path][symbol.line, symbol.name] = symbol
        elif symbol.kind == "class":
            classes_by_path[symbol.path][symbol.line, symbol.name] = symbol
    class_nodes = {}
    class_declarations = {}
    class_owners = {}
    methods = []
    declarations = (
        (path, node)
        for path in graph.snapshot.paths
        for node in (
            ast.walk(graph.snapshot.units[path].tree)
            if graph.snapshot.units[path].tree is not None
            else ()
        )
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for path, node in declarations:
        is_class = isinstance(node, ast.ClassDef)
        owner_map = classes_by_path if is_class else by_path
        owner = owner_map[path].get((node.lineno, node.name))
        if owner is None:
            continue
        if is_class:
            class_declarations[owner.node_id] = node
            class_owners[owner.node_id] = owner
            class_nodes[owner.node_id] = _field_receivers(graph, owner, node)
        else:
            methods.append((owner, node))
    return class_nodes, class_declarations, class_owners, methods


def typed_receiver_edges(graph):
    """Create MAY_DISPATCH facts only for uniquely source-bound typed receivers.

    Args:
        graph: Authoritative repository relation graph and immutable snapshot.

    Returns:
        Tuple of possible call edges with source positions; never DIRECT_CALL.
    """
    edges = []
    class_nodes, class_declarations, class_owners, methods = _receiver_declarations(
        graph
    )
    injected = _constructor_injection_types(
        graph, class_declarations, methods, class_nodes
    )
    for class_id, fields in injected.items():
        class_nodes[class_id] = _field_receivers(
            graph, class_owners[class_id], class_declarations[class_id], fields
        )
    for owner, node in methods:
        identity = f"{owner.module}.{owner.owner}"
        if identity in class_nodes:
            fields = class_nodes[identity]
        else:
            fields = {}  # Standalone functions have no instance receiver fields.
        edges.extend(_method_receiver_edges(graph, owner, node, fields))
    return tuple(edges)
