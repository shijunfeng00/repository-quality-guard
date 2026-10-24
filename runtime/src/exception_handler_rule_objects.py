"""Share source-bound broad-handler facts across independent Core rule owners."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .core_rule_contract import CoreQualityRule
from .model import Finding


@dataclass(frozen=True)
class HandlerRuleContext:
    """One authored handler with location and its method's declaration defaults.

    Carries one AST handler, method defaults and its immutable source identity.
    """

    path: str
    source_sha256: str
    method_name: str
    block: ast.Try
    handler: ast.ExceptHandler
    defaulted_parameters: frozenset[str]


def _defaulted_parameters(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
) -> frozenset[str]:
    """Resolve defaults from the method declaration without running user code.

    The rule consumes shared source facts and retains stable Finding evidence.
    """
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


def _fallback(value: ast.AST | None, defaults: frozenset[str]) -> str | None:
    """Identify an explicitly authored literal or default-parameter sentinel.

    The rule consumes shared source facts and retains stable Finding evidence.
    """
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


def _handler_kind(handler: ast.ExceptHandler) -> tuple[str, str | None] | None:
    """Classify direct pass/continue/local-sentinel suppression only.

    The rule consumes shared source facts and retains stable Finding evidence.
    """
    if handler.type is not None and not (
        isinstance(handler.type, ast.Name)
        and handler.type.id in {"Exception", "BaseException"}
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


class _HandlerCollector(ast.NodeVisitor):
    """Collect handler nodes once, respecting nested callable ownership.

    The rule consumes shared source facts and retains stable Finding evidence.
    """

    def __init__(self) -> None:
        """Initialize the collection of try/handler pairs for one lexical function body.

        The list preserves AST encounter order; nested callable scopes are not
        collected under their enclosing method.

        Returns:
            None; an empty handler list is stored on this visitor."""
        self.handlers: list[tuple[ast.Try, ast.ExceptHandler]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Stop AST descent when a nested callable owns its own handler scope.

        Args:
            node: Nested function declaration at which traversal stops.

        Returns:
            None; does not visit the nested function body."""
        return

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef

    def visit_Try(self, node: ast.Try) -> None:
        """Record the handlers attached to a try statement before visiting its body.

        Each handler is paired with its owning try statement. Recursion through
        generic_visit discovers nested try statements but not nested functions.

        Args:
            node: Try statement whose exception handlers are being collected.

        Returns:
            None; pairs are appended to the collector's ordered list."""
        for handler in node.handlers:
            self.handlers.append((node, handler))
        self.generic_visit(node)


def handler_rule_contexts(
    snapshot: RepositoryAnalysisSnapshot,
) -> tuple[HandlerRuleContext, ...]:
    """Extract source-bound exception handler contexts from one frozen repository snapshot.

    Iterate only successfully parsed units, assign function/method ownership,
    and compute a source digest once per unit; the resulting context stream is
    reused by both broad-exception and silent-suppression detectors.

    Args:
        snapshot: Immutable parsed source units with stable repository paths.

    Returns:
        Tuple of handler contexts ordered by file and AST encounter order."""
    contexts: list[HandlerRuleContext] = []
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
                    continue
                visitor = _HandlerCollector()
                for statement in method.body:
                    visitor.visit(statement)
                defaults = _defaulted_parameters(method)
                contexts.extend(
                    HandlerRuleContext(
                        path, digest, method.name, block, handler, defaults
                    )
                    for block, handler in visitor.handlers
                )
    return tuple(contexts)


class SilentExceptionFallbackRule(CoreQualityRule[HandlerRuleContext]):
    """QG214: detect direct broad-handler return of an authored sentinel.

    Its evaluator owns both the condition and the resulting Finding payload.
    """

    code = "QG214"

    def evaluate(self, context: HandlerRuleContext) -> list[Finding]:
        """Construct the same source-located review Finding from a shared handler fact.

        A warning requires an explicit return of a recognized constant or default.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        handler = context.handler
        if handler.type is not None and not (
            isinstance(handler.type, ast.Name)
            and handler.type.id in {"Exception", "BaseException"}
        ):
            return []
        if len(handler.body) != 1 or not isinstance(handler.body[0], ast.Return):
            return []
        returned = handler.body[0]
        sentinel = _fallback(returned.value, context.defaulted_parameters)
        if sentinel is None:
            return []
        return [
            Finding(
                code=self.code,
                severity="warning",
                confidence="medium",
                path=context.path,
                line=returned.lineno,
                column=returned.col_offset + 1,
                symbol=context.method_name,
                message="宽泛异常在此处理器内未经记录便转为 fallback 返回；需核对是否掩盖失败。",
                suggestion="核对调用方是否明确允许该异常路径的降级值；若不允许，保留错误可观察性并使契约失败显式化。",
                evidence={
                    "capability": "SOURCE_BOUND_BROAD_HANDLER_DIRECT_SENTINEL_OPEN_RUNTIME",
                    "source_sha256": context.source_sha256,
                    "try_line": context.block.lineno,
                    "handler_line": handler.lineno,
                    "return_line": returned.lineno,
                    "fallback": sentinel,
                    "semantic_status": "REVIEW_NOT_PROVEN_DEFECT",
                },
                source="source-bound-r3",
            )
        ]


class SilentHandlerFallthroughRule(CoreQualityRule[HandlerRuleContext]):
    """QG215: detect direct broad-handler suppression without observable action.

    Its evaluator owns exact suppression classification and Finding construction.
    """

    code = "QG215"

    def evaluate(self, context: HandlerRuleContext) -> list[Finding]:
        """Construct a conditional suppression Finding from a shared handler fact.

        Unproven runtime effects retain conditional warning severity and medium confidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        handler = context.handler
        classified = _handler_kind(handler)
        if classified is None:
            return []
        kind, variable = classified
        return [
            Finding(
                code=self.code,
                severity="warning",
                confidence="medium",
                path=context.path,
                line=handler.lineno,
                column=handler.col_offset + 1,
                symbol=context.method_name,
                message="宽泛异常被直接忽略或转为局部哨兵值；需核对错误传播契约。",
                suggestion="核对该路径是否为明确允许的 best-effort 或故障状态传播；不得将 UNKNOWN 直接视为缺陷。",
                evidence={
                    "capability": "SOURCE_BOUND_BROAD_EXCEPTION_DIRECT_SUPPRESSION_OPEN_RUNTIME",
                    "source_sha256": context.source_sha256,
                    "try_line": context.block.lineno,
                    "handler_line": handler.lineno,
                    "suppression": kind,
                    "local": variable,
                    "semantic_status": "REVIEW_NOT_PROVEN_DEFECT",
                },
                source="source-bound-r3",
            )
        ]


def exception_handler_rule_findings(
    snapshot: RepositoryAnalysisSnapshot,
) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]:
    """Evaluate both silent-fallback rule owners on the same handler facts.

    Both streams are independently sorted by path, line and column, retaining
    separate QG attribution without duplicate AST traversals.

    Args:
        snapshot: Frozen source units shared by both exception detectors.

    Returns:
        Pair of sorted Finding tuples for fallback and handler suppression."""
    fallback_rule = SilentExceptionFallbackRule()
    suppression_rule = SilentHandlerFallthroughRule()
    fallback: list[Finding] = []
    suppression: list[Finding] = []
    for context in handler_rule_contexts(snapshot):
        fallback.extend(fallback_rule.evaluate(context))
        suppression.extend(suppression_rule.evaluate(context))
    return (
        tuple(sorted(fallback, key=lambda row: (row.path, row.line, row.column))),
        tuple(sorted(suppression, key=lambda row: (row.path, row.line, row.column))),
    )
