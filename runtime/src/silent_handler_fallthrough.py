"""Source-bound witnesses of broad exceptions erased without an observable action.

This only detects syntactically proven silent handler paths. It does not treat
legitimate best-effort paths as confirmed application defects.
"""

from __future__ import annotations

import ast
import hashlib

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .model import Finding

_BROAD = frozenset({"Exception", "BaseException"})


def _handler_kind(handler: ast.ExceptHandler) -> tuple[str, str | None] | None:
    """Recognize one no-call suppression shape, excluding observable handlers."""
    if handler.type is not None and not (
        isinstance(handler.type, ast.Name) and handler.type.id in _BROAD
    ):
        return None
    if len(handler.body) != 1:
        return None
    statement = handler.body[0]
    if isinstance(statement, ast.Pass):
        return ("pass", None)
    if isinstance(statement, ast.Continue):
        return ("continue", None)
    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1 or not isinstance(
            statement.targets[0], ast.Name
        ):
            return None
        if isinstance(statement.value, ast.Constant) and (
            statement.value.value is None or statement.value.value is False
        ):
            return ("assign_sentinel", statement.targets[0].id)
    if isinstance(statement, ast.AnnAssign):
        if (
            isinstance(statement.target, ast.Name)
            and isinstance(statement.value, ast.Constant)
            and (statement.value.value is None or statement.value.value is False)
        ):
            return ("assign_sentinel", statement.target.id)
    return None


class _Visitor(ast.NodeVisitor):
    """Keep nested lexical functions separate so witnesses are not duplicated."""

    def __init__(self) -> None:
        """Initialize the collection of source-local broad exception handler observations.

        Returns:
            None; results are stored on the visitor, emitted to the requested file, or resources are released."""
        self.rows: list[tuple[ast.Try, ast.ExceptHandler, str, str | None]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Stop at nested definitions so handler findings belong to their actual owner.

        Args:
            node: ast.FunctionDef passed to the source-bound visit_FunctionDef operation.

        Returns:
            None; results are stored on the visitor, emitted to the requested file, or resources are released."""
        return

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef

    def visit_Try(self, node: ast.Try) -> None:
        """Inspect each authored handler and retain only recognized suppression paths.

        Args:
            node: ast.Try passed to the source-bound visit_Try operation.

        Returns:
            None; results are stored on the visitor, emitted to the requested file, or resources are released."""
        for handler in node.handlers:
            kind = _handler_kind(handler)
            if kind is not None:
                self.rows.append((node, handler, *kind))
        self.generic_visit(node)


def silent_handler_fallthrough_findings(
    snapshot: RepositoryAnalysisSnapshot,
) -> tuple[Finding, ...]:
    """Find broad exception suppression with no logging, calls or re-raise.

    Args:
        snapshot: Frozen parsed source and module units used to reconstruct lexical obligations.

    Returns:
        Potential exception swallowing or fallthrough observations with source locations."""
    findings: list[Finding] = []
    for path in snapshot.paths:
        unit = snapshot.units[path]
        if unit.tree is None:
            continue
        digest = hashlib.sha256(unit.source.encode()).hexdigest()
        for parent in unit.tree.body:
            if isinstance(parent, ast.ClassDef):
                methods = [
                    item
                    for item in parent.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
            elif isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods = [parent]
            else:
                continue
            for method in methods:
                if method.name == "__del__":
                    continue
                visitor = _Visitor()
                for statement in method.body:
                    visitor.visit(statement)
                for scope, handler, kind, variable in visitor.rows:
                    findings.append(
                        Finding(
                            code="QG215",
                            severity="warning",
                            confidence="medium",
                            path=path,
                            line=handler.lineno,
                            column=handler.col_offset + 1,
                            symbol=method.name,
                            message="宽泛异常被直接忽略或转为局部哨兵值；需核对错误传播契约。",
                            suggestion="核对该路径是否为明确允许的 best-effort 或故障状态传播；不得将 UNKNOWN 直接视为缺陷。",
                            evidence={
                                "capability": "SOURCE_BOUND_BROAD_EXCEPTION_DIRECT_SUPPRESSION_OPEN_RUNTIME",
                                "source_sha256": digest,
                                "try_line": scope.lineno,
                                "handler_line": handler.lineno,
                                "suppression": kind,
                                "local": variable,
                                "semantic_status": "REVIEW_NOT_PROVEN_DEFECT",
                            },
                            source="source-bound-r3",
                        )
                    )
    return tuple(sorted(findings, key=lambda row: (row.path, row.line, row.column)))
