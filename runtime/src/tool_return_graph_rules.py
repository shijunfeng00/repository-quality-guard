"""Source-bound QG121 consumers over the shared repository call topology.

Only report a reachable, discriminated Tool variant missing a field that a
statically bound consumer reads.  Unknown calls/flows do not become errors.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .config import GuardConfig
from .core_rule_contract import CoreQualityRule
from .model import Finding
from .topology_facts import RepositoryTopology, UsageKind


@dataclass(frozen=True, slots=True)
class ToolGraphContext:
    """Carry typed rule inputs: snapshot, topology, config, registered_tools, audited_paths.

    Fields: snapshot, topology, config, registered_tools, audited_paths."""

    snapshot: RepositoryAnalysisSnapshot
    topology: RepositoryTopology
    config: GuardConfig
    registered_tools: frozenset[str]
    audited_paths: frozenset[str]


def _functions(tree: ast.Module):
    """Yield functions with source locations, without duplicate nested walks."""

    def descend(node):
        """Walk module/class scopes to find independently declared callables.

        Args:
            node: AST scope currently explored for function declarations.

        Yields:
            Function or async-function nodes, including their nested callables."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield child
                yield from descend(child)
            elif isinstance(child, (ast.Module, ast.ClassDef)):
                yield from descend(child)

    yield from descend(tree)


def _returned_literal(statements: list[ast.stmt]) -> ast.Dict | None:
    """Resolve a closed mapping returned through unique straight-line assignments.

    This is positive assignment/alias provenance, not a guess from a return
    annotation. Mutations, calls, branches and repeated writes invalidate the
    local proof. An unknown path remains unclassified.
    """
    known: dict[str, ast.Dict] = {}
    written: set[str] = set()
    if not statements or not isinstance(statements[-1], ast.Return):
        return None
    for statement in statements[:-1]:
        if not (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            return None
        name = statement.targets[0].id
        if name in written:
            return None
        written.add(name)
        value = statement.value
        if isinstance(value, ast.Dict):
            known[name] = value
        elif isinstance(value, ast.Name) and value.id in known:
            known[name] = known[value.id]
        else:
            return None
    returned = statements[-1].value
    if isinstance(returned, ast.Dict):
        return returned
    if isinstance(returned, ast.Name):
        return known.get(returned.id)
    return None


def _variants(fn: ast.FunctionDef | ast.AsyncFunctionDef):
    """Prove two closed Tool mapping variants from finite authored branches.

    Supports an early-return branch, an explicit if/else return, and the
    phi-like join of two uniquely initialized locals at a shared return.
    No indirect updates, general joins, inferred defaults or dynamic calls.
    """
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    if not body or not isinstance(body[0], ast.If):
        return None
    conditional = body[0]
    test = conditional.test
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        test = test.operand
    formals = {
        argument.arg
        for argument in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    }
    if not isinstance(test, ast.Name) or test.id not in formals:
        return None
    if conditional.orelse:
        if len(body) == 1:
            branches = [
                _returned_literal(conditional.body),
                _returned_literal(conditional.orelse),
            ]
        elif len(body) == 2 and isinstance(body[1], ast.Return):
            # Both branches must each fully assign the shared return name;
            # only the same strict local assignment resolver can prove that.
            trailing_return = body[1]
            branches = [
                _returned_literal([*conditional.body, trailing_return]),
                _returned_literal([*conditional.orelse, trailing_return]),
            ]
        else:
            return None
    else:
        if len(body) < 2:
            return None
        branches = [
            _returned_literal(conditional.body),
            _returned_literal(body[1:]),
        ]
    if any(branch is None for branch in branches):
        return None
    variants: list[dict[str, object]] = []
    for branch in branches:
        fields: dict[str, object] = {}
        for key, value in zip(branch.keys, branch.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                return None
            # Key presence does not depend on the value: literal None is a
            # PRESENT key, not a missing one.
            fields[key.value] = value.value if isinstance(value, ast.Constant) else None
        variants.append(fields)
    common = set.intersection(*(set(v) for v in variants))
    discriminators = [
        field
        for field in sorted(common)
        if all(v[field] is not None for v in variants)
        and len({(type(v[field]).__name__, repr(v[field])) for v in variants}) > 1
    ]
    return (variants, discriminators) if discriminators else None


def _immediately_evaluated_nodes(node: ast.AST):
    """Walk effects evaluated *now*, not merely written in deferred code.

    Lambda defaults are evaluated at creation, whereas its body runs only for
    a directly invoked lambda.  A generator evaluates its first iterable at
    creation; its body is deferred.  Other comprehensions have unproved
    iteration reachability and are not positive read evidence.
    """
    pending = [node]
    while pending:
        item = pending.pop()
        if isinstance(item, (ast.ListComp, ast.SetComp, ast.DictComp)):
            # The container is constructed now, but a particular element read
            # is not guaranteed to execute (the iterator may be empty).
            yield item
            continue
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(item, ast.Lambda):
            pending.extend(item.args.defaults)
            pending.extend(default for default in item.args.kw_defaults if default)
            continue
        if isinstance(item, ast.GeneratorExp):
            if item.generators:
                pending.append(item.generators[0].iter)
            continue
        yield item
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Lambda):
            # The callee's body is reached at this exact call expression.
            pending.append(item.func.body)
        pending.extend(ast.iter_child_nodes(item))


def _field_reads(node: ast.AST, aliases: frozenset[str]):
    """Report only literal subscript loads of immediately evaluated aliases."""
    for item in _immediately_evaluated_nodes(node):
        if (
            isinstance(item, ast.Subscript)
            and isinstance(item.value, ast.Name)
            and item.value.id in aliases
            and isinstance(item.slice, ast.Constant)
            and isinstance(item.slice.value, str)
            and isinstance(item.ctx, ast.Load)
        ):
            yield item.slice.value, item.lineno


def _required_get_reads(node: ast.AST, aliases: frozenset[str]):
    """Yield literal `.get(key)` reads of the same graph-proven result object."""
    for item in _immediately_evaluated_nodes(node):
        if (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "get"
            and isinstance(item.func.value, ast.Name)
            and item.func.value.id in aliases
            and len(item.args) in {1, 2}
            and isinstance(item.args[0], ast.Constant)
            and isinstance(item.args[0].value, str)
        ):
            yield item.args[0].value, item.lineno


def _bound_variants(
    expr: ast.expr, aliases: frozenset[str], variants: list[dict[str, object]]
):
    """Refine one exact boolean tag read from any proven local alias."""
    negated = isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not)
    target = expr.operand if negated else expr
    if not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Name):
        return None
    if target.value.id not in aliases or not isinstance(target.slice, ast.Constant):
        return None
    field = target.slice.value
    if not isinstance(field, str) or not all(
        field in variant and isinstance(variant[field], bool) for variant in variants
    ):
        return None
    yes = [v for v in variants if bool(v[field]) is not negated]
    no = [v for v in variants if bool(v[field]) is negated]
    return yes, no


def _alias_assignment(statement: ast.stmt) -> tuple[str, str | None] | None:
    """Identify only a single direct lexical assignment, never a branch guess."""
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target, value = statement.targets[0], statement.value
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        target, value = statement.target, statement.value
    else:
        return None
    if not isinstance(target, ast.Name):
        return None
    return target.id, value.id if isinstance(value, ast.Name) else None


def _alias_mutation(statement: ast.stmt, aliases: frozenset[str]) -> bool:
    """A slot/attribute write through any known alias invalidates all key facts."""
    for item in _immediately_evaluated_nodes(statement):
        if not isinstance(item, (ast.Attribute, ast.Subscript)) or not isinstance(
            item.ctx, (ast.Store, ast.Del)
        ):
            continue
        if isinstance(item.value, ast.Name) and item.value.id in aliases:
            return True
    return False


def _alias_escapes(statement: ast.stmt, aliases: frozenset[str]) -> bool:
    """Unknown calls may mutate passed dictionaries, so stop at that boundary.

    A dict `.get()` on a proven alias is a known read-only operation.  Any
    other call with the tracked object as receiver/argument is an open-world
    boundary, not an affirmative contract violation.
    """
    # Publishing the dictionary into a property/collection, or wrapping the
    # alias inside an aggregate, loses exclusive local provenance.
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        assignment = _alias_assignment(statement)
        value = statement.value
        if value is not None and any(
            isinstance(item, ast.Name)
            and isinstance(item.ctx, ast.Load)
            and item.id in aliases
            for item in _immediately_evaluated_nodes(value)
        ):
            if isinstance(value, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
                return True
            if isinstance(value, ast.Name) and assignment is None:
                return True
    # Eager comprehensions may execute their body zero or more times.  Do not
    # assert a positive field read, but also do not preserve a stale contract
    # after a possible mutation/escape within such a body.
    for expression in _immediately_evaluated_nodes(statement):
        if not isinstance(expression, (ast.ListComp, ast.SetComp, ast.DictComp)):
            continue
        for child in ast.walk(expression):
            if (
                isinstance(child, (ast.Attribute, ast.Subscript))
                and isinstance(child.ctx, (ast.Store, ast.Del))
                and isinstance(child.value, ast.Name)
                and child.value.id in aliases
            ):
                return True
            if not isinstance(child, ast.Call):
                continue
            if (
                isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id in aliases
                and child.func.attr != "get"
            ):
                return True
            if any(
                isinstance(alias, ast.Name) and alias.id in aliases
                for arg in (*child.args, *(kw.value for kw in child.keywords))
                for alias in ast.walk(arg)
            ):
                return True
    for call in _immediately_evaluated_nodes(statement):
        if not isinstance(call, ast.Call):
            continue
        if (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in aliases
            and call.func.attr != "get"
        ):
            return True
        if any(
            isinstance(item, ast.Name) and item.id in aliases
            for expr in (*call.args, *(kw.value for kw in call.keywords))
            for item in ast.walk(expr)
        ):
            return True
    return False


def _read_events(
    expression: ast.AST,
    aliases: frozenset[str],
    variants: list[dict[str, object]],
):
    """Share a proven producer→consumer read stream between QG003/QG121."""
    for field, line in _field_reads(expression, aliases):
        if any(field not in variant for variant in variants):
            yield "QG121", field, line
    for field, line in _required_get_reads(expression, aliases):
        if all(field in variant for variant in variants):
            yield "QG003", field, line


def _name_rebinds(statement: ast.stmt, aliases: frozenset[str]) -> bool:
    """Identify writes that may replace a proven local alias identity."""
    return any(
        isinstance(item, ast.Name)
        and item.id in aliases
        and isinstance(item.ctx, (ast.Store, ast.Del))
        for item in _immediately_evaluated_nodes(statement)
    )


@dataclass(frozen=True, slots=True)
class _CallableGraph:
    """Source-bound callable identities and edges from the existing shared topology.

    The AST body is only consulted after an exact normalized CALLS edge. An
    unresolved name/indirect target never gets a guessed destination.
    """

    nodes: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]
    targets: dict[tuple[str, int], frozenset[str]]
    captures: frozenset[tuple[str, str, str]]
    symbol_paths: dict[str, str]
    node_ids: dict[int, str]
    shadowed_next: frozenset[str]


def _scope_binds_next(
    node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Conservatively exclude builtin next() when lexical/global bindings shadow it."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        formals = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        if any(formal.arg == "next" for formal in formals):
            return True
        if node.args.vararg is not None and node.args.vararg.arg == "next":
            return True
        if node.args.kwarg is not None and node.args.kwarg.arg == "next":
            return True
    pending = list(node.body)
    while pending:
        statement = pending.pop()
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if statement.name == "next":
                return True
            continue
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            if any(
                (alias.asname or alias.name.split(".")[0]) == "next"
                for alias in statement.names
            ):
                return True
            continue
        if (
            isinstance(statement, ast.Name)
            and statement.id == "next"
            and isinstance(statement.ctx, (ast.Store, ast.Del))
        ):
            return True
        pending.extend(ast.iter_child_nodes(statement))
    return False


def _is_generator_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Detect yield in this function's own scope, not in a nested callable."""
    pending: list[ast.AST] = list(fn.body)
    while pending:
        node = pending.pop()
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            continue
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
        pending.extend(ast.iter_child_nodes(node))
    return False


def _first_generator_value(node: ast.AST) -> ast.expr | None:
    """A first-step guarantee only for one pure, unconditional yield expression.

    A generator with a conditional, preceding yield, side effects or an unknown
    iterable is deliberately outside this bounded proof. The absence of a
    recognized first yield is UNKNOWN, not evidence that the field is safe.
    """
    if isinstance(node, ast.FunctionDef):
        body = node.body
        if len(body) == 1 and isinstance(body[0], ast.Expr):
            first = body[0].value
            if isinstance(first, ast.Yield):
                return first.value
        return None
    if isinstance(node, ast.GeneratorExp) and len(node.generators) == 1:
        comprehension = node.generators[0]
        if comprehension.is_async or comprehension.ifs:
            return None
        collection = comprehension.iter
        if not isinstance(collection, (ast.List, ast.Tuple)) or not collection.elts:
            return None
        if not all(isinstance(element, ast.Constant) for element in collection.elts):
            return None
        return node.elt
    return None


def _generator_first_read(
    expression: ast.expr,
    known: frozenset[str],
    callables: dict[
        str, tuple[ast.Lambda | ast.FunctionDef | ast.AsyncFunctionDef, str | None]
    ],
    graph: _CallableGraph | None,
    caller_id: str,
) -> tuple[ast.expr, frozenset[str]] | None:
    """Resolve a proven local generator object without treating its creation as execution."""
    if isinstance(expression, ast.GeneratorExp):
        first = _first_generator_value(expression)
        if first is None:
            return None
        # A generator's own target shadows a captured name during iteration.
        target = expression.generators[0].target
        if any(
            isinstance(item, ast.Name)
            and isinstance(item.ctx, ast.Store)
            and item.id in known
            for item in ast.walk(target)
        ):
            return None
        return first, known
    if not (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Name)
        and not expression.args
        and not expression.keywords
    ):
        return None
    local = callables.get(expression.func.id)
    if local is None:
        return None
    fn, target = local
    if not isinstance(fn, ast.FunctionDef) or target is None or graph is None:
        return None
    if target not in graph.targets.get((caller_id, expression.lineno), frozenset()):
        return None
    first = _first_generator_value(fn)
    if first is None:
        return None
    captures = frozenset(
        name for name in known if (target, caller_id, name) in graph.captures
    )
    return (first, captures) if captures else None


def _callable_invocations(
    statement: ast.stmt,
    caller_id: str,
    aliases: frozenset[str],
    callables: dict[
        str, tuple[ast.Lambda | ast.FunctionDef | ast.AsyncFunctionDef, str | None]
    ],
    graph: _CallableGraph | None,
):
    """Yield bodies reached by calls, using existing source-bound graph edges.

    Local lambda bindings have no symbol in the legacy graph; their initializer
    is a unique lexical binding, not a heuristic name-based repository lookup.
    """
    awaited_calls = {
        id(item.value)
        for item in _immediately_evaluated_nodes(statement)
        if isinstance(item, ast.Await) and isinstance(item.value, ast.Call)
    }
    for item in _immediately_evaluated_nodes(statement):
        if not isinstance(item, ast.Call) or not isinstance(item.func, ast.Name):
            continue
        name = item.func.id
        local = callables.get(name)
        if local is not None:
            definition, target_id = local
            if isinstance(definition, ast.Lambda):
                arguments = definition.args
                # Exact, first-class lambda argument binding. We deliberately
                # exclude default/variadic/keyword dispatch until individually
                # proved; no speculative target or unknown positional binding.
                formals = [*arguments.posonlyargs, *arguments.args]
                if (
                    arguments.vararg is not None
                    or arguments.kwarg is not None
                    or arguments.kwonlyargs
                    or arguments.defaults
                    or item.keywords
                    or len(item.args) != len(formals)
                    or any(isinstance(actual, ast.Starred) for actual in item.args)
                ):
                    continue
                formal_names = {formal.arg for formal in formals}
                captured = set(aliases) - formal_names
                for formal, actual in zip(formals, item.args):
                    if isinstance(actual, ast.Name) and actual.id in aliases:
                        captured.add(formal.arg)
                if captured:
                    yield definition.body, captured, target_id
            elif (
                not item.args
                and not item.keywords
                and graph is not None
                and target_id is not None
            ):
                if (
                    isinstance(definition, ast.AsyncFunctionDef)
                    and id(item) not in awaited_calls
                ):
                    continue
                if target_id not in graph.targets.get(
                    (caller_id, item.lineno), frozenset()
                ):
                    continue
                if _is_generator_function(definition):
                    continue
                captured = {
                    alias
                    for alias in aliases
                    if (target_id, caller_id, alias) in graph.captures
                }
                yield definition.body, captured, target_id
            continue
        if graph is None:
            continue
        targets = graph.targets.get((caller_id, item.lineno), frozenset())
        if len(targets) != 1:
            continue
        target_id = next(iter(targets))
        callee = graph.nodes.get(target_id)
        if callee is None or callee.decorator_list or _is_generator_function(callee):
            continue
        if isinstance(callee, ast.AsyncFunctionDef) and id(item) not in awaited_calls:
            continue
        if callee.args.vararg is not None or callee.args.kwarg is not None:
            continue
        params = [*callee.args.posonlyargs, *callee.args.args]
        if len(item.args) > len(params) or any(
            isinstance(arg, ast.Starred) for arg in item.args
        ):
            continue
        bound: dict[str, str] = {}
        for formal, actual in zip(params, item.args):
            if isinstance(actual, ast.Name) and actual.id in aliases:
                bound[formal.arg] = actual.id
        for keyword in item.keywords:
            if keyword.arg is None:
                bound.clear()
                break
            if isinstance(keyword.value, ast.Name) and keyword.value.id in aliases:
                if keyword.arg in bound:
                    bound.clear()
                    break
                bound[keyword.arg] = keyword.value.id
        positional_names = {formal.arg for formal in params[: len(item.args)]}
        keyword_names = {keyword.arg for keyword in item.keywords}
        if None in keyword_names or len(keyword_names) != len(item.keywords):
            continue
        if positional_names & keyword_names:
            continue
        all_params = {param.arg for param in (*params, *callee.args.kwonlyargs)}
        if keyword_names - all_params:
            continue
        required = {
            formal.arg for formal in params[: len(params) - len(callee.args.defaults)]
        }
        required.update(
            formal.arg
            for formal, default in zip(callee.args.kwonlyargs, callee.args.kw_defaults)
            if default is None
        )
        if not required <= positional_names | keyword_names:
            continue
        if bound:
            # Capture identity is permitted only when the exact binding edge
            # exists, never from a coincidentally equal name in another scope.
            bound.update(
                {
                    alias: alias
                    for alias in aliases
                    if (target_id, caller_id, alias) in graph.captures
                }
            )
            yield callee.body, set(bound), target_id


def _tool_read_events(
    statements: tuple[ast.stmt, ...] | list[ast.stmt],
    aliases: set[str],
    variants: list[dict[str, object]],
    graph: _CallableGraph | None = None,
    caller_id: str = "",
    visited: frozenset[str] = frozenset(),
):
    """Follow exact alias and bounded source-bound callable consumption.

    The shared topology owns cross-file identities/captures. Unmatched calls,
    mutable escapes, branches and recursion remain UNKNOWN, never positive risk.
    """
    active = variants
    local_callables: dict[
        str, tuple[ast.Lambda | ast.FunctionDef | ast.AsyncFunctionDef, str | None]
    ] = {}
    local_generators: dict[str, tuple[ast.expr, frozenset[str]]] = {}

    def invalidate_generator_family(name: str) -> None:
        # Assignment aliases refer to the same runtime iterator; mutation,
        # escape or advancement invalidates *every* lexical alias.
        identity = local_generators.get(name)
        if identity is not None:
            for alias, candidate in tuple(local_generators.items()):
                if candidate is identity:
                    local_generators.pop(alias)

    shadowed_next = False
    for statement in statements:
        if not active or not aliases:
            return
        known = frozenset(aliases)
        if isinstance(statement, ast.If):
            if _alias_escapes(statement.test, known):
                return
            for code, field, line in _read_events(statement.test, known, active):
                yield code, field, line, caller_id
            partition = _bound_variants(statement.test, known, active)
            if partition is None:
                return
            yes, no = partition
            yield from _tool_read_events(
                statement.body, set(known), yes, graph, caller_id, visited
            )
            yield from _tool_read_events(
                statement.orelse, set(known), no, graph, caller_id, visited
            )
            if any(
                _alias_mutation(part, known)
                or _alias_escapes(part, known)
                or _name_rebinds(part, known)
                for part in (*statement.body, *statement.orelse)
            ):
                return
            yes_exit = bool(statement.body) and isinstance(
                statement.body[-1], (ast.Return, ast.Raise)
            )
            no_exit = bool(statement.orelse) and isinstance(
                statement.orelse[-1], (ast.Return, ast.Raise)
            )
            active = ([] if yes_exit else yes) + ([] if no_exit else no)
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                statement.decorator_list
                or statement.args.defaults
                or any(x is not None for x in statement.args.kw_defaults)
            ):
                return
            target_id = graph.node_ids.get(id(statement)) if graph else None
            local_callables[statement.name] = (statement, target_id)
            continue
        if isinstance(statement, ast.For):
            # Entering a synchronous for-loop advances its iterable once.
            # Only a statically nonempty, source-bound generator can supply
            # positive first-element evidence; complex loop bodies remain open.
            if isinstance(statement.iter, ast.Name):
                deferred = local_generators.get(statement.iter.id)
            else:
                deferred = _generator_first_read(
                    statement.iter, known, local_callables, graph, caller_id
                )
            if deferred is not None:
                first, captured = deferred
                for code, field, line in _read_events(first, captured & known, active):
                    yield code, field, line, caller_id
            return
        if isinstance(
            statement,
            (
                ast.AsyncFor,
                ast.While,
                ast.Try,
                ast.With,
                ast.AsyncWith,
                ast.Match,
                ast.ClassDef,
            ),
        ):
            return
        if _alias_mutation(statement, known):
            return
        # Unknown observers and methods can advance, close or replace a
        # generator. After it escapes, its first-step state is no longer
        # provable. Do not retain stale key-read evidence across that call.
        for call in _immediately_evaluated_nodes(statement):
            if not isinstance(call, ast.Call):
                continue
            if isinstance(call.func, ast.Attribute) and isinstance(
                call.func.value, ast.Name
            ):
                invalidate_generator_family(call.func.value.id)
            if isinstance(call.func, ast.Name) and call.func.id == "next":
                continue
            for argument in (*call.args, *(kw.value for kw in call.keywords)):
                if isinstance(argument, ast.Name):
                    invalidate_generator_family(argument.id)
        # A generator function call only constructs a generator.  Its first
        # yield is evaluated when a known generator is advanced by next().
        # Iteration of unknown collections/paths is an open boundary.
        for call in _immediately_evaluated_nodes(statement):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "next"
                and len(call.args) == 1
                and not call.keywords
            ):
                continue
            # A user-defined `next` can return anything; only builtin next()
            # has the forced-advance semantics proved here.
            if (
                shadowed_next
                or "next" in local_callables
                or (graph is not None and caller_id in graph.shadowed_next)
            ):
                continue
            if graph and any(
                graph.nodes.get(target) is not None
                and graph.nodes[target].name == "next"
                for target in graph.targets.get((caller_id, call.lineno), frozenset())
            ):
                continue
            source = call.args[0]
            if isinstance(source, ast.Name):
                deferred = local_generators.get(source.id)
            else:
                deferred = _generator_first_read(
                    source, known, local_callables, graph, caller_id
                )
            if deferred is not None:
                first, captured = deferred
                for code, field, line in _read_events(first, captured & known, active):
                    yield code, field, line, caller_id
                if isinstance(source, ast.Name):
                    invalidate_generator_family(source.id)
        # For known direct calls, read before the conservative escape boundary.
        # A call may mutate the value: no field facts survive beyond that call.
        executed = False
        for body, callee_aliases, target in _callable_invocations(
            statement, caller_id, known, local_callables, graph
        ):
            if target is not None and (target in visited or len(visited) >= 5):
                continue
            executed = True
            child_visited = visited | ({target} if target else set())
            if isinstance(body, list):
                yield from _tool_read_events(
                    body,
                    callee_aliases,
                    active,
                    graph,
                    target or caller_id,
                    frozenset(child_visited),
                )
            else:
                for code, field, line in _read_events(
                    body, frozenset(callee_aliases), active
                ):
                    yield code, field, line, caller_id
        if _alias_escapes(statement, known):
            return
        for code, field, line in _read_events(statement, known, active):
            yield code, field, line, caller_id
        if executed:
            # Side effects in reached callables are not proven absent.
            return
        bound = _alias_assignment(statement)
        if bound is not None:
            target, source = bound
            if target == "next":
                shadowed_next = True
            if source in known:
                aliases.add(target)
            else:
                aliases.discard(target)
            value = statement.value
            if isinstance(value, ast.Lambda):
                local_callables[target] = (value, None)
            elif source in local_callables:
                local_callables[target] = local_callables[source]
            else:
                local_callables.pop(target, None)
            deferred = _generator_first_read(
                value, known, local_callables, graph, caller_id
            )
            if deferred is not None:
                local_generators[target] = deferred
            elif source in local_generators:
                local_generators[target] = local_generators[source]
            else:
                local_generators.pop(target, None)
        elif _name_rebinds(statement, known):
            return
        if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return


@dataclass(frozen=True, slots=True)
class ToolResultBinding:
    """One graph-resolved producer/consumer join and a bounded result alias.

    Fields: path, producer_id, consumer_id, consumer_line, variable, variants, discriminators, tail."""

    path: str
    producer_id: str
    consumer_id: str
    consumer_line: int
    variable: str
    variants: list[dict[str, object]]
    discriminators: list[str]
    tail: tuple[ast.stmt, ...]
    graph: _CallableGraph | None = None
    audited_paths: frozenset[str] = frozenset()


def tool_result_bindings(context: ToolGraphContext) -> tuple[ToolResultBinding, ...]:
    """Join literal Tool-result variants to source-bound consumers via topology.

    Reject unknown producer identities and non-discriminated return shapes;
    preserve the original source and consumer evidence for QG121/QG003.

    Args:
        context: Frozen source snapshot, call graph, registrations and scope.

    Returns:
        ToolResultBinding tuples for provable producer/consumer relationships."""
    locations = {
        (symbol.path.as_posix(), symbol.line): symbol
        for symbol in context.topology.symbols
        if symbol.kind in {"function", "method"}
    }
    producers: dict[str, tuple[list[dict[str, object]], list[str]]] = {}
    consumers = []
    for path, unit in context.snapshot.units.items():
        if unit.tree is None:
            continue
        for fn in _functions(unit.tree):
            symbol = locations.get((path, fn.lineno))
            if symbol is None:
                continue
            decorators = {
                (decorator.id if isinstance(decorator, ast.Name) else decorator.attr)
                for decorator in fn.decorator_list
                if isinstance(decorator, (ast.Name, ast.Attribute))
            }
            is_tool = symbol.symbol_id in context.registered_tools or bool(
                decorators & set(context.config.tool_decorators)
            )
            if is_tool:
                variants = _variants(fn)
                if variants is not None:
                    producers[symbol.symbol_id] = variants
            if path in context.audited_paths:
                consumers.append((path, fn, symbol))
    graph_nodes = {
        symbol.symbol_id: fn
        for path, unit in context.snapshot.units.items()
        if unit.tree is not None
        for fn in _functions(unit.tree)
        for symbol in [locations.get((path, fn.lineno))]
        if symbol is not None
    }
    target_index: dict[tuple[str, int], set[str]] = {}
    captured = set()
    for edge in context.topology.edges:
        if edge.kind is UsageKind.DIRECT_CALL:
            target_index.setdefault((edge.source_id, edge.line), set()).add(
                edge.target_id
            )
        elif edge.kind is UsageKind.CLOSURE_CAPTURE and edge.target_id.startswith(
            "binding:"
        ):
            _, owner, alias = edge.target_id.rsplit(":", 2)
            captured.add((edge.source_id, owner, alias))
    shadowed_modules = {
        path
        for path, unit in context.snapshot.units.items()
        if unit.tree is not None and _scope_binds_next(unit.tree)
    }
    shadowed_symbols = frozenset(
        symbol.symbol_id
        for path, unit in context.snapshot.units.items()
        if unit.tree is not None
        for fn in _functions(unit.tree)
        for symbol in [locations.get((path, fn.lineno))]
        if symbol is not None and (path in shadowed_modules or _scope_binds_next(fn))
    )
    call_graph = _CallableGraph(
        graph_nodes,
        {key: frozenset(value) for key, value in target_index.items()},
        frozenset(captured),
        {
            symbol.symbol_id: symbol.path.as_posix()
            for symbol in context.topology.symbols
        },
        {id(node): identifier for identifier, node in graph_nodes.items()},
        shadowed_symbols,
    )
    bindings = []
    for path, fn, symbol in consumers:
        for index, statement in enumerate(fn.body):
            if not (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and isinstance(statement.value, ast.Call)
            ):
                continue
            # A literal call argument can restrict producer branches; without
            # source-bound argument/branch specialization it is not sound to
            # report that *all* authored return variants remain reachable.
            if any(
                isinstance(arg, ast.Constant) for arg in statement.value.args
            ) or any(
                isinstance(keyword.value, ast.Constant)
                for keyword in statement.value.keywords
            ):
                continue
            edges = [
                edge
                for edge in context.topology.outgoing(
                    symbol.symbol_id, (UsageKind.DIRECT_CALL,)
                )
                if edge.line == statement.value.lineno
            ]
            targets = {edge.target_id for edge in edges}
            if len(targets) != 1:
                continue
            producer_id = next(iter(targets))
            if producer_id not in producers:
                continue
            variants, discriminators = producers[producer_id]
            bindings.append(
                ToolResultBinding(
                    path,
                    producer_id,
                    symbol.symbol_id,
                    fn.lineno,
                    statement.targets[0].id,
                    variants,
                    discriminators,
                    tuple(fn.body[index + 1 :]),
                    call_graph,
                    context.audited_paths,
                )
            )
    return tuple(bindings)


class ToolConsumerContractRule(CoreQualityRule[tuple[ToolResultBinding, ...]]):
    """QG121: proven producer-variant/consumer-field violation.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG121"

    def evaluate(self, bindings: tuple[ToolResultBinding, ...]) -> list[Finding]:
        """Evaluate QG121 using the supplied rule evidence.

        Args:
            bindings: tuple[ToolResultBinding, ...] input consumed by this detector.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        findings = []
        for row in bindings:
            for code, field, read_line, reader_id in _tool_read_events(
                row.tail, {row.variable}, row.variants, row.graph, row.consumer_id
            ):
                if code != self.code:
                    continue
                findings.append(
                    Finding(
                        code=self.code,
                        severity="error",
                        confidence="high",
                        message=f"Tool `{row.producer_id}` 的可达返回变体缺少消费者读取的 `{field}`。",
                        suggestion="按已证明的判别字段收窄返回变体后读取其专属键。",
                        evidence={
                            "producer": row.producer_id,
                            "consumer": row.consumer_id,
                            "missing_field": field,
                            "read_line": read_line,
                            "read_owner": reader_id,
                            "discriminators": row.discriminators,
                            "return_variant_keys": [sorted(v) for v in row.variants],
                            "topology_resolution": "normalized-direct-call",
                        },
                        path=row.path,
                        line=row.consumer_line,
                        column=1,
                        symbol=row.consumer_id,
                    )
                )
        return findings


def required_tool_result_get_findings(
    bindings: tuple[ToolResultBinding, ...], code: str
) -> list[Finding]:
    """Build graph-proven QG003 findings for a caller-owned rule contract.

    Tool graph extraction stays adjacent to its shared facts, but the caller
    supplies the code so this helper cannot become a second Core rule owner.
    """
    findings: list[Finding] = []
    for row in bindings:
        for event_code, field, read_line, reader_id in _tool_read_events(
            row.tail, {row.variable}, row.variants, row.graph, row.consumer_id
        ):
            if event_code != code:
                continue
            original_path = (
                row.graph.symbol_paths.get(reader_id, row.path)
                if row.graph
                else row.path
            )
            read_path = (
                original_path if original_path in row.audited_paths else row.path
            )
            findings.append(
                Finding(
                    code=code,
                    severity="warning",
                    confidence="high",
                    message=f"Tool `{row.producer_id}` 所有可达返回变体均包含必需字段 `{field}`，消费者不应使用 `.get()` 静默兜底。",
                    suggestion="使用严格索引；如该字段确实可缺失，先修正生产者与 Schema 契约。",
                    evidence={
                        "producer": row.producer_id,
                        "consumer": row.consumer_id,
                        "required_field": field,
                        "discriminators": row.discriminators,
                        "topology_resolution": "normalized-direct-call",
                        "read_line": read_line,
                        "read_owner": reader_id,
                    },
                    path=read_path,
                    line=read_line
                    if original_path in row.audited_paths
                    else row.consumer_line,
                    column=1,
                    symbol=reader_id
                    if original_path in row.audited_paths
                    else row.consumer_id,
                )
            )
    return findings
