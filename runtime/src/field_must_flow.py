"""Bounded normal-exit field-initialization facts on the shared source topology.

The solver proves only authored lexical field assignments that dominate a read,
or survive *all* normal constructor exits. An unknown effect invalidates the
proof; unproven state is UNKNOWN, never an inferred missing field or BLOCKER.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .topology_facts import (
    FieldInitializationFact,
    InstanceFieldAccessFact,
    RepositoryTopology,
)


@dataclass(slots=True)
class _Flow:
    """One method's finite source-located field transfer domain."""

    facts: dict[tuple[int, int, str], InstanceFieldAccessFact]
    observed: dict[tuple[int, int, str], tuple[str, tuple[int, ...]]]
    normal_exits: list[dict[str, frozenset[int]]]
    receiver: str
    reachable_names: frozenset[str] = frozenset()
    external_names: frozenset[str] = frozenset()
    escaped: bool = False

    def _carries_receiver(self, expr: ast.AST) -> bool:
        """Trace actual returned values, not receiver subexpressions of a read."""
        return _originates_from_receiver(expr, self.reachable_names)

    def _read(self, node: ast.Attribute, state: dict[str, frozenset[int]]) -> None:
        key = (node.lineno, node.col_offset + 1, "READ")
        if key in self.facts:
            if node.attr in state and not self.escaped:
                self.observed[key] = ("MUST_AT_READ", tuple(sorted(state[node.attr])))
            else:
                self.observed.setdefault(key, ("UNKNOWN", ()))

    def _expression(
        self, expr: ast.AST, state: dict[str, frozenset[int]]
    ) -> dict[str, frozenset[int]]:
        """Any call/await/unknown effect kills field-state proofs, not the reads."""
        for part in ast.walk(expr):
            if (
                isinstance(part, ast.NamedExpr)
                and isinstance(part.target, ast.Name)
                and part.target.id in self.external_names
                and self._carries_receiver(part.value)
            ):
                self.escaped = True
                state.clear()
        calls = [part for part in ast.walk(expr) if isinstance(part, ast.Call)]
        if any(
            self._carries_receiver(part)
            for call in calls
            for part in (*call.args, *[word.value for word in call.keywords], call.func)
        ):
            # A callback or dynamic receiver invocation can publish this object.
            self.escaped = True
        if calls or any(
            isinstance(part, (ast.Await, ast.Yield, ast.YieldFrom))
            for part in ast.walk(expr)
        ):
            state = {}
        stack = [expr]
        while stack:
            part = stack.pop()
            if isinstance(
                part,
                (ast.Lambda, ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp),
            ):
                # Deferred or nested lexical execution is not a read on this path.
                continue
            if (
                isinstance(part, ast.Attribute)
                and isinstance(part.value, ast.Name)
                and part.value.id == self.receiver
                and isinstance(part.ctx, ast.Load)
            ):
                self._read(part, state)
            stack.extend(ast.iter_child_nodes(part))
        return state

    def _target(
        self, target: ast.AST, state: dict[str, frozenset[int]], action: str
    ) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for part in target.elts:
                self._target(part, state, action)
        elif (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == self.receiver
        ):
            key = (target.lineno, target.col_offset + 1, action)
            if key in self.facts:
                if action == "WRITE":
                    state[target.attr] = frozenset((target.lineno,))
                elif action == "DELETE":
                    state.pop(target.attr, None)
        else:
            # Unknown receiver aliases or arbitrary subscripts can mutate fields.
            if isinstance(target, (ast.Attribute, ast.Subscript)):
                state.clear()

    @staticmethod
    def join(
        states: list[dict[str, frozenset[int]]],
    ) -> dict[str, frozenset[int]] | None:
        """Intersect definitely initialized fields across reachable normal exits.

        Args:
            states: Field witness maps for all reachable normal branches.

        Returns:
            Shared field witnesses, or None when no normal branch survives.
        """
        if not states:
            return None
        common = set(states[0])
        for state in states[1:]:
            common.intersection_update(state)
        # Multiple normal branches may each assign; retain all actual witness lines.
        return {
            key: frozenset().union(*(item[key] for item in states)) for key in common
        }

    def statements(
        self, body: list[ast.stmt], incoming: dict[str, frozenset[int]] | None
    ) -> dict[str, frozenset[int]] | None:
        state = None if incoming is None else dict(incoming)
        for stmt in body:
            if state is None:
                break
            if isinstance(stmt, ast.If):
                state = self._expression(stmt.test, state)
                branches = [
                    result
                    for result in (
                        self.statements(stmt.body, state),
                        self.statements(stmt.orelse, state),
                    )
                    if result is not None
                ]
                state = self.join(branches)
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                if stmt.value is None:  # Bare attribute annotation is not a write.
                    continue
                state = self._expression(stmt.value, state)
                targets = (
                    stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                )
                for target in targets:
                    if self._carries_receiver(stmt.value) and (
                        isinstance(target, (ast.Attribute, ast.Subscript))
                        or isinstance(target, ast.Name)
                        and target.id in self.external_names
                        or not isinstance(target, ast.Name)
                    ):
                        self.escaped = True
                        state.clear()
                    self._target(target, state, "WRITE")
            elif isinstance(stmt, ast.Delete):
                for target in stmt.targets:
                    self._target(target, state, "DELETE")
            elif isinstance(stmt, ast.AugAssign):
                state = self._expression(stmt.value, state)
                if (
                    isinstance(stmt.target, ast.Attribute)
                    and isinstance(stmt.target.value, ast.Name)
                    and stmt.target.value.id == self.receiver
                    and (stmt.target.lineno, stmt.target.col_offset + 1, "READ_WRITE")
                    in self.facts
                ):
                    # AugAssign reads before it writes and may call __iadd__.
                    state = {}
                    state[stmt.target.attr] = frozenset((stmt.lineno,))
                else:
                    state = {}
            elif isinstance(stmt, ast.Return):
                if stmt.value is not None:
                    state = self._expression(stmt.value, state)
                if stmt.value is not None and self._carries_receiver(stmt.value):
                    self.escaped = True
                    state.clear()
                self.normal_exits.append(dict(state))
                state = None
            elif isinstance(stmt, ast.Raise):
                if stmt.exc is not None:
                    self._expression(stmt.exc, state)
                state = None
            elif isinstance(stmt, (ast.Expr, ast.Assert)):
                state = self._expression(stmt, state)
            elif isinstance(stmt, ast.Pass):
                continue
            else:
                # Loops, try/finally, with, match, scopes and early-exit effects
                # are not closed in this bounded interpreter. Conservatively KILL.
                state = {}
        return state


def _originates_from_receiver(expr: ast.AST, names: frozenset[str] | set[str]) -> bool:
    """Only a value proven sourced from the receiver or a containing aggregate.

    ``receiver.field`` is not itself an alias of ``receiver``. Keeping that
    distinction avoids treating every ordinary getter as a publication.
    """
    if isinstance(expr, ast.Name):
        return isinstance(expr.ctx, ast.Load) and expr.id in names
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        return any(_originates_from_receiver(v, names) for v in expr.elts)
    if isinstance(expr, ast.Dict):
        return any(
            _originates_from_receiver(v, names)
            for v in (*expr.keys, *expr.values)
            if v is not None
        )
    if isinstance(expr, ast.IfExp):
        return any(
            _originates_from_receiver(v, names) for v in (expr.body, expr.orelse)
        )
    if isinstance(expr, ast.BoolOp):
        return any(_originates_from_receiver(v, names) for v in expr.values)
    if isinstance(expr, ast.NamedExpr):
        return _originates_from_receiver(expr.value, names)
    return False


def _receiver_reachability(
    method: ast.FunctionDef | ast.AsyncFunctionDef, receiver: str
) -> tuple[frozenset[str], frozenset[str]]:
    """Compute a conservative lexical MAY-alias/container closure.

    A local assignment is not an external escape. Global/nonlocal assignments
    and attribute/subscript publication are handled at their actual statement.
    The analysis intentionally over-approximates aliases across branches; no
    function, class, variable or field spelling supplies semantic evidence.
    """
    statements: list[ast.AST] = []
    external: set[str] = set()

    class Collect(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            pass  # A nested scope is not an immediate assignment.

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        visit_Lambda = visit_FunctionDef

        def visit_Global(self, node: ast.Global) -> None:
            external.update(node.names)

        visit_Nonlocal = visit_Global

        def visit_Assign(self, node: ast.Assign) -> None:
            statements.append(node)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if node.value is not None:
                statements.append(node)
            self.generic_visit(node)

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
            statements.append(node)
            self.generic_visit(node)

    scan = Collect()
    for statement in method.body:
        scan.visit(statement)
    reachable = {receiver}
    while True:
        previous = len(reachable)
        for node in statements:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            else:
                targets, value = [node.target], node.value
            if not _originates_from_receiver(value, reachable):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    reachable.add(target.id)
        if len(reachable) == previous:
            break
    return frozenset(reachable), frozenset(external)


def _cross_method_invalidations(
    methods: list[ast.FunctionDef | ast.AsyncFunctionDef],
    initial: ast.FunctionDef,
    field_facts: list[InstanceFieldAccessFact],
) -> tuple[set[str], bool]:
    """Invalidate constructor seeds reachable from other authored methods."""
    invalidated = {
        fact.member
        for fact in field_facts
        if fact.method_symbol_id.rsplit(".", 1)[-1] != initial.name
        and fact.operation in {"DELETE", "READ_WRITE"}
    }
    may_escape = False
    for method in methods:
        if method is initial:
            continue
        formal = (*method.args.posonlyargs, *method.args.args)[0].arg
        reachable, external = _receiver_reachability(method, formal)
        for node in ast.walk(ast.Module(body=method.body, type_ignores=[])):
            if isinstance(node, ast.Call) and any(
                _originates_from_receiver(arg, reachable)
                for arg in (*node.args, *[k.value for k in node.keywords])
            ):
                may_escape = True
            elif isinstance(node, (ast.Delete, ast.AugAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Delete) else [node.target]
                )
                if any(
                    isinstance(part, ast.Name)
                    and part.id in reachable
                    and isinstance(part.ctx, ast.Load)
                    for target in targets
                    for part in ast.walk(target)
                ):
                    may_escape = True
            elif isinstance(node, ast.Return) and node.value is not None:
                if _originates_from_receiver(node.value, reachable):
                    may_escape = True
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                value = node.value
                if (
                    value is not None
                    and any(
                        isinstance(part, ast.Name)
                        and isinstance(part.ctx, ast.Load)
                        and part.id in reachable
                        for part in ast.walk(value)
                    )
                    and any(
                        isinstance(target, (ast.Attribute, ast.Subscript))
                        or isinstance(target, ast.Name)
                        and target.id in external
                        for target in targets
                    )
                ):
                    may_escape = True
        if may_escape:
            break
    return invalidated, may_escape


def field_initialization_facts(
    snapshot: RepositoryAnalysisSnapshot, topology: RepositoryTopology
) -> tuple[FieldInitializationFact, ...]:
    """Attach source-only MUST witnesses to existing owner-bound field accesses."""
    by_method: dict[str, list[InstanceFieldAccessFact]] = {}
    for fact in topology.instance_field_accesses:
        by_method.setdefault(fact.method_symbol_id, []).append(fact)
    owners = {
        (symbol.path.as_posix(), symbol.line): symbol
        for symbol in topology.symbols
        if symbol.language == "python" and symbol.kind == "class"
    }
    closed = {
        fact.owner_symbol_id
        for fact in topology.constructor_lifecycles
        if fact.capability == "SOURCE_BOUND_CPYTHON_C3_OPEN_RUNTIME"
        and fact.mro_symbol_ids == (fact.owner_symbol_id,)
    }
    results: list[FieldInitializationFact] = []
    for path in snapshot.paths:
        tree = snapshot.units[path].tree
        if tree is None:
            continue
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            owner = owners.get((path, cls.lineno))
            if owner is None:
                continue
            methods_in_class = [
                fn
                for fn in cls.body
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not fn.decorator_list
                and fn.args.posonlyargs + fn.args.args
            ]
            exits: dict[str, frozenset[int]] = {}
            init = next((fn for fn in methods_in_class if fn.name == "__init__"), None)
            if (
                init is not None
                and owner.symbol_id in closed
                and not any(
                    isinstance(part, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom))
                    for statement in init.body
                    for part in ast.walk(statement)
                )
            ):
                # A constructor invoking unknown code can publish or call back
                # the object before later assignments. No cross-method MUST seed.
                facts = by_method.get(f"{owner.symbol_id}.__init__", ())
                receiver = (init.args.posonlyargs + init.args.args)[0].arg
                reachable, external = _receiver_reachability(init, receiver)
                local = _Flow(
                    {(f.line, f.column, f.operation): f for f in facts},
                    {},
                    [],
                    receiver,
                    reachable,
                    external,
                )
                remaining = local.statements(init.body, {})
                normal = list(local.normal_exits)
                if remaining is not None:
                    normal.append(remaining)
                exits = {} if local.escaped else local.join(normal) or {}
                invalidated, escaped = _cross_method_invalidations(
                    methods_in_class,
                    init,
                    [
                        fact
                        for method in methods_in_class
                        for fact in by_method.get(
                            f"{owner.symbol_id}.{method.name}", ()
                        )
                    ],
                )
                if escaped:
                    exits.clear()
                else:
                    for member in invalidated:
                        exits.pop(member, None)
            for fn in methods_in_class:
                ident = f"{owner.symbol_id}.{fn.name}"
                member_facts = by_method.get(ident, ())
                receiver = (fn.args.posonlyargs + fn.args.args)[0].arg
                reachable, external = _receiver_reachability(fn, receiver)
                local = _Flow(
                    {(f.line, f.column, f.operation): f for f in member_facts},
                    {},
                    [],
                    receiver,
                    reachable,
                    external,
                )
                seed = exits if fn is not init else {}
                local.statements(fn.body, seed)
                for fact in member_facts:
                    if fact.operation != "READ":
                        continue
                    proof, lines = local.observed.get(
                        (fact.line, fact.column, "READ"), ("UNKNOWN", ())
                    )
                    # A constructor-derived fact requires proof of a normal exit
                    # and that no unknown mutation could occur before the read.
                    results.append(
                        FieldInitializationFact(
                            path=fact.path,
                            line=fact.line,
                            column=fact.column,
                            source_sha256=fact.source_sha256,
                            owner_symbol_id=fact.owner_symbol_id,
                            method_symbol_id=fact.method_symbol_id,
                            member=fact.member,
                            verdict=proof,
                            witness_lines=lines,
                        )
                    )
    return tuple(
        sorted(
            results,
            key=lambda f: (
                f.path.as_posix(),
                f.line,
                f.column,
                f.method_symbol_id,
                f.member,
            ),
        )
    )
