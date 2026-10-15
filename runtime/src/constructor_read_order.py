"""Source-bound conditional QG210 for an instance field read before first write.

Only a straight-line, undecorated, source-owned ``__init__`` in a class with no
explicit base is eligible. Runtime monkey patching or custom construction remains
open-world; this is a review witness, not a claim that AttributeError occurred.
"""

from __future__ import annotations

import ast
import hashlib

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .model import Finding


def _direct_write(stmt: ast.stmt, receiver: str) -> set[str]:
    """Find immediate assignment targets, never branch-local possible writes."""
    targets: list[ast.AST] = []
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        targets = [stmt.target]
    elif isinstance(stmt, ast.AugAssign):
        targets = [stmt.target]
    return {
        target.attr
        for root in targets
        for target in ast.walk(root)
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == receiver
        and isinstance(target.ctx, ast.Store)
    }


def _read_before_write(stmt: ast.stmt, receiver: str) -> tuple[ast.Attribute, ...]:
    """Evaluate RHS first; an augmented assignment reads its target first."""
    expressions: list[ast.AST] = []
    if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
        expressions.append(stmt.value)
    elif isinstance(stmt, ast.AugAssign):
        expressions.extend((stmt.target, stmt.value))
    elif isinstance(stmt, (ast.Expr, ast.Return)) and stmt.value is not None:
        expressions.append(stmt.value)
    return tuple(
        part
        for expression in expressions
        for part in ast.walk(expression)
        if isinstance(part, ast.Attribute)
        and (
            isinstance(part.ctx, ast.Load)
            or isinstance(stmt, ast.AugAssign)
            and part is stmt.target
        )
        and isinstance(part.value, ast.Name)
        and part.value.id == receiver
    )


def constructor_read_order_findings(
    snapshot: RepositoryAnalysisSnapshot,
) -> tuple[Finding, ...]:
    """Witness a literal constructor read that precedes any authored write.

    Args:
        snapshot: Frozen parsed source and module units used to reconstruct lexical obligations.

    Returns:
        Source-bound constructor read-before-write findings; unresolved paths are not declared safe."""
    rows: list[Finding] = []
    for path in snapshot.paths:
        unit = snapshot.units[path]
        if unit.tree is None:
            continue
        digest = hashlib.sha256(unit.source.encode()).hexdigest()
        for cls in unit.tree.body:
            if (
                not isinstance(cls, ast.ClassDef)
                or cls.bases
                or cls.keywords
                or cls.decorator_list
            ):
                continue
            members = {
                child.name
                for child in cls.body
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
            }
            members.update(
                target.id
                for statement in cls.body
                if isinstance(statement, (ast.Assign, ast.AnnAssign))
                for target in (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                if isinstance(target, ast.Name)
            )
            if members & {
                "__new__",
                "__getattribute__",
                "__getattr__",
                "__getattr_static__",
            }:
                continue
            constructors = [
                node
                for node in cls.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "__init__"
                and not node.decorator_list
                and (node.args.posonlyargs or node.args.args)
            ]
            if len(constructors) != 1:
                continue
            method = constructors[0]
            receiver = (*method.args.posonlyargs, *method.args.args)[0].arg
            seen: dict[str, int] = {}
            body = method.body
            for index, statement in enumerate(body):
                if not isinstance(
                    statement, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)
                ):
                    break  # No CFG assertion through branches, calls or early exits.
                if any(
                    isinstance(child, ast.Name)
                    and child.id == receiver
                    and isinstance(child.ctx, (ast.Store, ast.Del))
                    for child in ast.walk(statement)
                ):
                    break  # A rebound receiver destroys this constructor proof.
                # Calls may publish/mutate the receiver; a simple conservative
                # call boundary avoids manufacturing a missing-field proof.
                if any(
                    isinstance(node, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom))
                    for node in ast.walk(statement)
                ):
                    break
                contiguous = []
                for following in body[index:]:
                    if not isinstance(
                        following, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)
                    ):
                        break
                    if any(
                        isinstance(
                            node,
                            (
                                ast.Call,
                                ast.Await,
                                ast.Yield,
                                ast.YieldFrom,
                                ast.Lambda,
                                ast.GeneratorExp,
                                ast.ListComp,
                                ast.SetComp,
                                ast.DictComp,
                            ),
                        )
                        for node in ast.walk(following)
                    ):
                        break
                    contiguous.append(following)
                future_writes = set().union(
                    *(_direct_write(other, receiver) for other in contiguous)
                )
                for read in _read_before_write(statement, receiver):
                    member = read.attr
                    if (
                        member.startswith("__")
                        or member in members
                        or member in seen
                        or member not in future_writes
                    ):
                        continue
                    line = next(
                        other.lineno
                        for other in contiguous
                        if member in _direct_write(other, receiver)
                    )
                    rows.append(
                        Finding(
                            code="QG210",
                            severity="warning",
                            confidence="medium",
                            path=path,
                            line=read.lineno,
                            column=read.col_offset + 1,
                            symbol=f"{cls.name}.__init__",
                            message="构造方法在可见的首次实例字段写入之前读取同一字段；需核对构造前置契约。",
                            suggestion="确认字段是否由明确的 __new__/描述符或外部构造契约预置，否则在首次读取之前完成初始化。",
                            evidence={
                                "capability": "SOURCE_BOUND_STRAIGHT_LINE_READ_BEFORE_WRITE_OPEN_RUNTIME",
                                "source_sha256": digest,
                                "owner_line": cls.lineno,
                                "constructor_line": method.lineno,
                                "field": member,
                                "first_write_line": line,
                                "semantic_status": "REVIEW_NOT_PROVEN_RUNTIME_FAILURE",
                            },
                            source="source-bound-r2",
                        )
                    )
                seen.update(
                    {
                        member: statement.lineno
                        for member in _direct_write(statement, receiver)
                    }
                )
    return tuple(sorted(rows, key=lambda row: (row.path, row.line, row.column)))
