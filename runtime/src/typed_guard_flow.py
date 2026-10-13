"""Bounded Python structured-CFG key-presence evidence for typed consumers.

Transfer uses explicit control-flow joins, not statement-order heuristics. Only a
literal membership test of an annotated receiver can add a fact. Calls, loops,
exception handlers, escapes and unknown effects cannot establish a MUST fact.
"""

from __future__ import annotations

import ast

Presence = dict[tuple[str, str], int]
ReadLocation = tuple[int, int, str, str]


def _literal_membership(node: ast.expr, receivers: set[str]):
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        result = _literal_membership(node.operand, receivers)
        return None if result is None else (result[0], result[1], not result[2])
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == len(node.comparators) == 1
        and isinstance(node.ops[0], (ast.In, ast.NotIn))
        and isinstance(node.left, ast.Constant)
        and isinstance(node.left.value, str)
        and isinstance(node.comparators[0], ast.Name)
        and node.comparators[0].id in receivers
    ):
        return (
            node.comparators[0].id,
            node.left.value,
            isinstance(node.ops[0], ast.In),
        )
    return None


def _reads(expr: ast.AST, receivers: set[str]):
    for node in ast.walk(expr):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id in receivers
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            yield (node.lineno, node.col_offset, node.value.id, node.slice.value)


def _receiver_escapes(statement: ast.AST, receivers: set[str]) -> bool:
    """Do not retain MUST when the receiver is copied into an unknown alias."""
    if not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
        return False
    value = statement.value
    parents = {
        id(child): node
        for node in ast.walk(value)
        for child in ast.iter_child_nodes(node)
    }
    for node in ast.walk(value):
        if not isinstance(node, ast.Name) or node.id not in receivers:
            continue
        if not isinstance(node.ctx, ast.Load):
            continue
        parent = parents.get(id(node))
        # Direct indexing observes an element, not an alias to the dictionary.
        if isinstance(parent, ast.Subscript) and parent.value is node:
            continue
        return True
    return False


def _unknown_effect(node: ast.AST, receivers: set[str]) -> bool:
    """Withhold MUST when receiver state may change or effects are unbound."""
    for part in ast.walk(node):
        if isinstance(part, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)):
            return True
        if (
            isinstance(part, ast.Name)
            and part.id in receivers
            and isinstance(part.ctx, (ast.Store, ast.Del))
        ):
            return True
        if isinstance(part, (ast.Subscript, ast.Attribute)) and isinstance(
            part.ctx, (ast.Store, ast.Del)
        ):
            if isinstance(part.value, ast.Name) and part.value.id in receivers:
                return True
    return False


def _join(a: Presence | None, b: Presence | None) -> Presence | None:
    """A returned/raised branch has no normal successor; otherwise intersect."""
    if a is None:
        return b
    if b is None:
        return a
    return {key: a[key] for key in a.keys() & b.keys() if a[key] == b[key]}


def presence_witnesses(
    declaration: ast.FunctionDef | ast.AsyncFunctionDef, receivers: set[str]
) -> dict[ReadLocation, int]:
    """Prove key presence at located reads in the supported structured CFG.

    The returned integer is a source line of a dominating membership guard.
    No witness means UNKNOWN, not absence, invalid data, or a missing-key BUG.
    """
    observed: dict[ReadLocation, int] = {}

    def record(node: ast.AST, state: Presence) -> None:
        available = {} if _unknown_effect(node, receivers) else state
        for location in _reads(node, receivers):
            witness = available.get((location[2], location[3]))
            if witness is not None:
                observed[location] = witness

    def walk(
        statements: list[ast.stmt], input_state: Presence | None
    ) -> Presence | None:
        state = input_state
        for stmt in statements:
            if state is None:
                break
            if isinstance(stmt, ast.If):
                record(stmt.test, state)
                guard = _literal_membership(stmt.test, receivers)
                if _unknown_effect(stmt.test, receivers):
                    yes, no = {}, {}
                else:
                    yes, no = dict(state), dict(state)
                    if guard is not None:
                        key = (guard[0], guard[1])
                        (yes if guard[2] else no)[key] = stmt.lineno
                        (no if guard[2] else yes).pop(key, None)
                state = _join(walk(stmt.body, yes), walk(stmt.orelse, no))
            elif isinstance(stmt, (ast.Return, ast.Raise)):
                value = stmt.value if isinstance(stmt, ast.Return) else stmt.exc
                if value is not None:
                    record(value, state)
                state = None
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)):
                record(stmt, state)
                if _unknown_effect(stmt, receivers) or _receiver_escapes(
                    stmt, receivers
                ):
                    state = {}
            elif isinstance(stmt, ast.Pass):
                continue
            else:
                # Unsupported loops, with, try/except/finally, match and nested
                # declarations may contain exceptional or hidden exits/aliases.
                # No positive proof is manufactured on those paths.
                state = {}
        return state

    walk(declaration.body, {})
    return observed
