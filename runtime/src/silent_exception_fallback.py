"""Source-bound evidence of an exception silently converted to a fallback value.

This rule certifies the *control-flow behavior*, not that the fallback violates
an application's external contract. Explicit logging, state recording, and
unknown fallback expressions are outside this deliberately narrow domain.
"""

from __future__ import annotations

import ast
import hashlib

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .model import Finding


def _fallback(value: ast.AST | None, defaults: frozenset[str]) -> str | None:
    """Recognize literal sentinels and defaulted formal parameters only."""
    if value is None:
        return "None"
    if isinstance(value, ast.Constant) and isinstance(
        value.value, (str, int, float, bool, type(None))
    ):
        return repr(value.value)
    if isinstance(value, ast.Dict) and not value.keys:
        return "{}"
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)) and not value.elts:
        return ast.unparse(value)
    if isinstance(value, ast.Name) and value.id in defaults:
        return f"parameter:{value.id}"
    return None


def _defaulted_parameters(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
) -> frozenset[str]:
    """Find parameters whose own declaration provides an explicit default value."""
    positional = (*method.args.posonlyargs, *method.args.args)
    names = (
        {arg.arg for arg in positional[len(positional) - len(method.args.defaults) :]}
        if method.args.defaults
        else set()
    )
    names.update(
        arg.arg
        for arg, default in zip(method.args.kwonlyargs, method.args.kw_defaults)
        if default is not None
    )
    return frozenset(names)


class _HandlerVisitor(ast.NodeVisitor):
    """Visit the immediate function's handlers without borrowing nested scopes."""

    def __init__(self, defaults: frozenset[str]) -> None:
        """Retain declared defaults and initialize the source-located fallback observations.

        Args:
            defaults: frozenset[str] passed to the source-bound __init__ operation.

        Returns:
            None; results are stored on the visitor, emitted to the requested file, or resources are released."""
        self.defaults = defaults
        self.fallbacks: list[tuple[ast.Try, ast.ExceptHandler, ast.Return, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Exclude nested callable bodies from the current exception-handling owner.

        Args:
            node: ast.FunctionDef passed to the source-bound visit_FunctionDef operation.

        Returns:
            None; results are stored on the visitor, emitted to the requested file, or resources are released."""
        pass

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef

    def visit_Try(self, node: ast.Try) -> None:
        """Record directly returned sentinels from broad handlers without assuming business invalidity.

        Args:
            node: ast.Try passed to the source-bound visit_Try operation.

        Returns:
            None; results are stored on the visitor, emitted to the requested file, or resources are released."""
        for handler in node.handlers:
            broad = handler.type is None or (
                isinstance(handler.type, ast.Name)
                and handler.type.id in {"Exception", "BaseException"}
            )
            if (
                broad
                and len(handler.body) == 1
                and isinstance(handler.body[0], ast.Return)
            ):
                returned = handler.body[0]
                sentinel = _fallback(returned.value, self.defaults)
                if sentinel is not None:
                    self.fallbacks.append((node, handler, returned, sentinel))
        self.generic_visit(node)


def silent_exception_fallback_findings(
    snapshot: RepositoryAnalysisSnapshot,
) -> tuple[Finding, ...]:
    """Find unobserved broad exception-to-sentinel conversions in authored code.

    Args:
        snapshot: Frozen parsed source and module units used to reconstruct lexical obligations.

    Returns:
        Exception-handler fallback observations that require downstream contract review."""
    findings: list[Finding] = []
    for path in snapshot.paths:
        unit = snapshot.units[path]
        if unit.tree is None:
            continue
        digest = hashlib.sha256(unit.source.encode()).hexdigest()
        for scope in unit.tree.body:
            if isinstance(scope, ast.ClassDef):
                methods = [
                    node
                    for node in scope.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
            elif isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods = [scope]
            else:
                continue
            for method in methods:
                if method.name == "__del__":
                    # CPython finalization admits best-effort cleanup.
                    continue
                visitor = _HandlerVisitor(_defaulted_parameters(method))
                for statement in method.body:
                    visitor.visit(statement)
                for block, handler, returned, sentinel in visitor.fallbacks:
                    findings.append(
                        Finding(
                            code="QG214",
                            severity="warning",
                            confidence="medium",
                            path=path,
                            line=returned.lineno,
                            column=returned.col_offset + 1,
                            symbol=method.name,
                            message="宽泛异常在此处理器内未经记录便转为 fallback 返回；需核对是否掩盖失败。",
                            suggestion="核对调用方是否明确允许该异常路径的降级值；若不允许，保留错误可观察性并使契约失败显式化。",
                            evidence={
                                "capability": "SOURCE_BOUND_BROAD_HANDLER_DIRECT_SENTINEL_OPEN_RUNTIME",
                                "source_sha256": digest,
                                "try_line": block.lineno,
                                "handler_line": handler.lineno,
                                "return_line": returned.lineno,
                                "fallback": sentinel,
                                "semantic_status": "REVIEW_NOT_PROVEN_DEFECT",
                            },
                            source="source-bound-r3",
                        )
                    )
    return tuple(sorted(findings, key=lambda row: (row.path, row.line, row.column)))
