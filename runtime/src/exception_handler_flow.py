"""Source-bound R1 exception-handler reads before an established local assignment.

A caught exception during RHS evaluation can enter its handler before STORE_NAME.
The handler witness is conditional on that exception actually arising: the
analysis never infers an external API's exception contract from its name.
"""

from __future__ import annotations

import ast
import hashlib

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .topology_facts import ExceptionHandlerReadFact, RepositoryTopology


def _local_reads(node: ast.AST) -> tuple[ast.Name, ...]:
    """Read names in one eager expression without descending deferred scopes."""
    found: list[ast.Name] = []

    class Collect(ast.NodeVisitor):
        """Collect eager reads in the immediate expression, excluding deferred lexical scopes.

        All observations remain local to the parsed source; runtime execution is not inferred.
        """

        def visit_Name(self, part: ast.Name) -> None:
            """Record only load-context names whose values are evaluated immediately.

            Args:
                part: Authored ast.Name under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            if isinstance(part.ctx, ast.Load):
                found.append(part)

        def visit_Lambda(self, part: ast.Lambda) -> None:
            """Do not descend into deferred lambda bodies during eager-read analysis.

            Args:
                part: Authored ast.Lambda under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            pass

        def visit_FunctionDef(self, part: ast.FunctionDef) -> None:
            """Do not attribute nested callable bodies to the current eager expression.

            Args:
                part: Authored ast.FunctionDef under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            pass

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        visit_GeneratorExp = visit_FunctionDef
        visit_ListComp = visit_FunctionDef
        visit_SetComp = visit_FunctionDef
        visit_DictComp = visit_FunctionDef

    Collect().visit(node)
    return tuple(found)


def _assigned(node: ast.AST) -> frozenset[str]:
    """Return local target bindings, excluding nested definitions' own bodies."""
    result: set[str] = set()

    class Collect(ast.NodeVisitor):
        """Collect local store and delete targets without traversing nested definitions.

        All observations remain local to the parsed source; runtime execution is not inferred.
        """

        def visit_Name(self, part: ast.Name) -> None:
            """Record local names bound or deleted by the current expression.

            Args:
                part: Authored ast.Name under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            if isinstance(part.ctx, (ast.Store, ast.Del)):
                result.add(part.id)

        def visit_FunctionDef(self, part: ast.FunctionDef) -> None:
            """Record a function declaration as a name binding, not its nested body.

            Args:
                part: Authored ast.FunctionDef under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            result.add(part.name)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, part: ast.ClassDef) -> None:
            """Record a class declaration as a name binding, not its nested body.

            Args:
                part: Authored ast.ClassDef under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            result.add(part.name)

        def visit_Lambda(self, part: ast.Lambda) -> None:
            """Ignore lambda-local bindings outside the current lexical owner.

            Args:
                part: Authored ast.Lambda under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            pass

    Collect().visit(node)
    return frozenset(result)


def _eager_reads_in_handler(handler: ast.ExceptHandler) -> tuple[ast.Name, ...]:
    """Return first-evaluation reads before any assignment in the handler.

    Nested branches are not certified in this limited pass. Assignment RHSs
    precede their target writes, matching Python's execution order.
    """
    bound = {handler.name} if handler.name else set()
    reads: list[ast.Name] = []
    for statement in handler.body:
        if isinstance(statement, (ast.If, ast.While)):
            exprs: tuple[ast.AST, ...] = (statement.test,)
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = statement.value
            exprs = (value,) if value is not None else ()
        elif isinstance(statement, ast.AugAssign):
            exprs = (statement.target, statement.value)
        elif isinstance(statement, (ast.Expr, ast.Return, ast.Raise, ast.Assert)):
            exprs = (statement,)
        else:
            break  # UNKNOWN: unsupported statement affects control flow.
        reads.extend(
            node
            for expr in exprs
            for node in _local_reads(expr)
            if node.id not in bound
        )
        if not isinstance(statement, (ast.If, ast.While)):
            bound.update(_assigned(statement))
        # After a conditional body begins, an exact dominating assignment
        # requires a full CFG proof; emit only reads of its test expression.
        if isinstance(statement, (ast.If, ast.While)):
            break
    return tuple(reads)


def _first_raising_assignments(body: list[ast.stmt]) -> dict[str, int]:
    """Record a possibly reachable RHS failure before the first local store.

    A for-loop may execute zero times; this is a conditional *possibility*,
    never a claim that the assignment or its caught exception is inevitable.
    Nested try/with/closure semantics remain outside this bounded domain.
    """
    first: dict[str, int] = {}
    rejected: set[str] = set()

    def walk(statements: list[ast.stmt]) -> None:
        """Visit bounded statement sequences, recording first potentially failing RHS evaluations.

        Args:
            statements: Ordered authored statements in the current lexical owner.

        Returns:
            Computed None field or analysis facts; unknown paths remain unproven.
        """
        for stmt in statements:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = (
                    stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                )
                eager = stmt.value is not None and any(
                    isinstance(node, ast.Await) for node in ast.walk(stmt.value)
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        if target.id not in first and target.id not in rejected:
                            if eager:
                                first[target.id] = stmt.lineno
                            else:
                                rejected.add(target.id)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                walk(stmt.body)
                walk(stmt.orelse)
            elif isinstance(stmt, ast.If):
                if not (
                    isinstance(stmt.test, ast.Constant) and stmt.test.value is False
                ):
                    walk(stmt.body)
                walk(stmt.orelse)
            elif isinstance(stmt, ast.While):
                if not (
                    isinstance(stmt.test, ast.Constant) and stmt.test.value is False
                ):
                    walk(stmt.body)
                walk(stmt.orelse)
            elif isinstance(
                stmt, (ast.Try, ast.TryStar, ast.With, ast.AsyncWith, ast.Match)
            ):
                # Unsupported nested exception/resource semantics: no certificate.
                break

    walk(body)
    return first


def exception_handler_read_facts(
    snapshot: RepositoryAnalysisSnapshot, topology: RepositoryTopology
) -> tuple[ExceptionHandlerReadFact, ...]:
    """Associate eager handler reads with a possibly interrupted local assignment.

    Args:
        snapshot: Parsed source of local assignments and exception handlers.
        topology: Unique authored function identities used to attribute findings.

    Returns:
        Conditional unbound-read facts without assuming the exception must occur.
    """
    declarations = {
        (symbol.path.as_posix(), symbol.line): symbol
        for symbol in topology.symbols
        if symbol.language == "python"
        and symbol.kind in {"function", "method"}
        and not symbol.foreign
    }
    facts: set[ExceptionHandlerReadFact] = set()
    for path in snapshot.paths:
        unit = snapshot.units[path]
        if unit.tree is None:
            continue
        for owner in unit.tree.body:
            if isinstance(owner, ast.ClassDef):
                functions = (
                    node
                    for node in owner.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
            elif isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions = (owner,)
            else:
                continue
            for function in functions:
                symbol = declarations.get((path, function.lineno))
                if symbol is None or function.decorator_list:
                    continue
                parameters = {
                    arg.arg
                    for arg in (
                        *function.args.posonlyargs,
                        *function.args.args,
                        *function.args.kwonlyargs,
                    )
                }
                if function.args.vararg:
                    parameters.add(function.args.vararg.arg)
                if function.args.kwarg:
                    parameters.add(function.args.kwarg.arg)
                bound_before = set(parameters)
                for statement in function.body:
                    if isinstance(statement, (ast.Global, ast.Nonlocal)):
                        bound_before.update(statement.names)
                    if (
                        isinstance(statement, (ast.Try, ast.TryStar))
                        and statement.handlers
                    ):
                        # Only source-visible assignment expressions with an
                        # eager potentially-raising RHS are considered.
                        candidates = _first_raising_assignments(statement.body)
                        for handler in statement.handlers:
                            for read in _eager_reads_in_handler(handler):
                                if read.id not in candidates or read.id in bound_before:
                                    continue
                                facts.add(
                                    ExceptionHandlerReadFact(
                                        path=symbol.path,
                                        line=read.lineno,
                                        column=read.col_offset + 1,
                                        source_sha256=hashlib.sha256(
                                            unit.source.encode()
                                        ).hexdigest(),
                                        owner_symbol_id=symbol.symbol_id,
                                        local_name=read.id,
                                        assignment_line=candidates[read.id],
                                        try_line=statement.lineno,
                                        handler_line=handler.lineno,
                                    )
                                )
                    bound_before.update(_assigned(statement))
    return tuple(
        sorted(
            facts,
            key=lambda f: (f.path.as_posix(), f.line, f.column, f.owner_symbol_id),
        )
    )
