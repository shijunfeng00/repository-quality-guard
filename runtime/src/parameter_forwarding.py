"""Conservative formal-to-formal source forwarding on existing bound call edges.

No new name-based callee resolver: only the existing normalized topology's
located DIRECT_CALL edges are considered and each is independently checked
against a lexical/module import declaration. Facts stay open-world, and
unknown effects prevent certified *content* propagation.
"""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict, deque

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .topology_facts import ParameterForwardingFact, RepositoryTopology, UsageKind


def _functions(snapshot: RepositoryAnalysisSnapshot, topology: RepositoryTopology):
    """Index actual top-level undecorated functions by authored symbol identity."""
    definitions = {}
    for symbol in topology.symbols:
        if (
            symbol.language != "python"
            or symbol.kind != "function"
            or symbol.nested
            or symbol.foreign
        ):
            continue
        path = symbol.path.as_posix()
        unit = snapshot.units.get(path)
        if unit is None or unit.tree is None:
            continue
        matches = [
            n
            for n in unit.tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.lineno == symbol.line
            and n.name == symbol.name
            and not n.decorator_list
        ]
        if (
            len(matches) == 1
            and sum(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == symbol.name
                for n in unit.tree.body
            )
            == 1
        ):
            definitions[symbol.symbol_id] = (symbol, matches[0])
    return definitions


def _assignments(node: ast.AST) -> set[str]:
    """Compute source-local rebinding of names without traversing nested scopes."""
    names = set()

    class Scan(ast.NodeVisitor):
        def visit_FunctionDef(self, n):
            pass

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        visit_Lambda = visit_FunctionDef

        def visit_Name(self, n):
            if isinstance(n.ctx, (ast.Store, ast.Del)):
                names.add(n.id)

    s = Scan()
    for stmt in node.body:
        s.visit(stmt)
    return names


def _calls(node: ast.AST) -> list[ast.Call]:
    """Find calls in one lexical body, excluding nested functions and closures."""
    result = []

    class Scan(ast.NodeVisitor):
        def visit_FunctionDef(self, n):
            pass

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        visit_Lambda = visit_FunctionDef
        visit_GeneratorExp = visit_FunctionDef
        visit_ListComp = visit_FunctionDef
        visit_SetComp = visit_FunctionDef
        visit_DictComp = visit_FunctionDef

        def visit_Call(self, n):
            result.append(n)
            self.generic_visit(n)

    s = Scan()
    for stmt in node.body:
        s.visit(stmt)
    return result


def _authored_target(
    call: ast.Call,
    source: ast.Module,
    caller,
    callee,
    assignments: set[str],
    caller_formals: set[str],
) -> bool:
    """Cross-check the existing edge with a unique authored import/declaration.

    Import path strings are exact declared module identities; they do not infer
    behavior or side effects from developer-chosen names.
    """
    target = call.func
    root_writes = {
        n.id
        for statement in source.body
        if not isinstance(
            statement,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Import,
                ast.ImportFrom,
            ),
        )
        for n in ast.walk(statement)
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))
    }
    if isinstance(target, ast.Name):
        name = target.id
        if name in assignments or name in caller_formals or name in root_writes:
            return False
        if callee.module == caller.module:
            imported_shadow = any(
                isinstance(stmt, (ast.Import, ast.ImportFrom))
                and any(
                    (alias.asname or alias.name.split(".", 1)[0]) == name
                    for alias in stmt.names
                )
                for stmt in source.body
            )
            return name == callee.name and not imported_shadow
        imports = [
            n
            for n in source.body
            if isinstance(n, ast.ImportFrom)
            and n.level == 0
            and n.module == callee.module
            for alias in n.names
            if alias.name == callee.name and (alias.asname or alias.name) == name
        ]
        return len(imports) == 1 and not any(
            isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name
            for n in source.body
        )
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.attr == callee.name
    ):
        name = target.value.id
        if name in assignments or name in caller_formals or name in root_writes:
            return False
        imports = [
            n
            for n in source.body
            if isinstance(n, ast.Import)
            for a in n.names
            if a.name == callee.module
            and (a.asname or a.name.split(".")[0]) == name
            and (a.asname or "." not in a.name)
        ]
        published_member_rebind = any(
            isinstance(part, ast.Attribute)
            and isinstance(part.ctx, (ast.Store, ast.Del))
            and isinstance(part.value, ast.Name)
            and part.value.id == name
            and part.attr == callee.name
            for statement in source.body
            for part in ast.walk(statement)
            if not isinstance(
                statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
        )
        return len(imports) == 1 and not published_member_rebind
    return False


def parameter_forwarding_facts(
    snapshot: RepositoryAnalysisSnapshot, topology: RepositoryTopology
) -> tuple[ParameterForwardingFact, ...]:
    """Project exact lexical argument forwarding into the sole normalized topology.

    These are positive *possible identity* edges. Dynamic modules, monkey patch,
    unrecognized alias effects and external calls cannot upgrade to MUST/ERROR.
    """
    definitions = _functions(snapshot, topology)
    by_source = defaultdict(list)
    for edge in topology.edges:
        if (
            edge.kind == UsageKind.DIRECT_CALL
            and edge.source_id in definitions
            and edge.target_id in definitions
        ):
            by_source[edge.source_id].append(edge)
    results = set()
    for caller_id, edges in by_source.items():
        caller, body = definitions[caller_id]
        unit = snapshot.units[caller.path.as_posix()]
        assigned = _assignments(body)
        formals = (*body.args.posonlyargs, *body.args.args)
        original = {
            arg.arg: i for i, arg in enumerate(formals) if arg.arg not in assigned
        }
        # Alias of a formal is valid only if assigned exactly once, from a formal.
        # If a formal is rebound, its original identity is never used as a source.
        assignments = defaultdict(list)
        for statement in body.body:
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                assignments[statement.targets[0].id].append(statement)
        for name, rows in assignments.items():
            if (
                len(rows) == 1
                and isinstance(rows[0].value, ast.Name)
                and rows[0].value.id in original
                and name not in original
            ):
                original[name] = original[rows[0].value.id]
        calls = _calls(body)
        for edge in edges:
            callee, target = definitions[edge.target_id]
            matches = [c for c in calls if c.lineno == edge.line]
            if len(matches) != 1:
                continue  # no column certified by legacy graph
            call = matches[0]
            if call.keywords or any(isinstance(a, ast.Starred) for a in call.args):
                continue
            if not _authored_target(
                call, unit.tree, caller, callee, assigned, {a.arg for a in formals}
            ):
                continue
            positional = (*target.args.posonlyargs, *target.args.args)
            if len(call.args) > len(positional):
                continue
            # Calls/receiver-affecting mutations before the call can change the
            # content. They MUST NOT become a 'validated schema' proof.
            earlier = [
                c
                for c in calls
                if (c.lineno, c.col_offset) < (call.lineno, call.col_offset)
                and c is not call
            ]
            if earlier:
                continue
            if any(
                isinstance(stmt, (ast.AugAssign, ast.Delete))
                and stmt.lineno < call.lineno
                for stmt in ast.walk(body)
            ):
                continue
            for index, arg in enumerate(call.args):
                if not isinstance(arg, ast.Name) or arg.id not in original:
                    continue
                if (
                    arg.id in assignments
                    and arg.id not in formals
                    and any(s.lineno >= call.lineno for s in assignments[arg.id])
                ):
                    continue
                results.add(
                    ParameterForwardingFact(
                        source_symbol_id=caller_id,
                        source_parameter_index=original[arg.id],
                        target_symbol_id=edge.target_id,
                        target_parameter_index=index,
                        path=caller.path,
                        line=call.lineno,
                        column=call.col_offset + 1,
                        caller_sha256=hashlib.sha256(unit.source.encode()).hexdigest(),
                        callee_sha256=hashlib.sha256(
                            snapshot.units[callee.path.as_posix()].source.encode()
                        ).hexdigest(),
                    )
                )
    return tuple(
        sorted(
            results,
            key=lambda x: (
                x.path.as_posix(),
                x.line,
                x.column,
                x.source_symbol_id,
                x.target_symbol_id,
                x.source_parameter_index,
                x.target_parameter_index,
            ),
        )
    )


def trace_parameter_forwarding(
    topology: RepositoryTopology, source_symbol_id: str, source_parameter_index: int
) -> tuple[tuple[str, int], ...]:
    """Worklist reachability, cycle-safe; never treats unobserved callers as absent."""
    adj = defaultdict(list)
    for x in topology.parameter_forwardings:
        adj[(x.source_symbol_id, x.source_parameter_index)].append(
            (x.target_symbol_id, x.target_parameter_index)
        )
    visited = {(source_symbol_id, source_parameter_index)}
    pending = deque(visited)
    while pending:
        for dest in adj.get(pending.popleft(), ()):
            if dest not in visited:
                visited.add(dest)
                pending.append(dest)
    return tuple(sorted(visited))
