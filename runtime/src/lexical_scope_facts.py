"""Lexically scoped Python binding/load facts for repository call resolution."""

from __future__ import annotations

import ast


class _ScopeNameCollector(ast.NodeVisitor):
    """Collect immediate function-scope bindings and loads without descending scopes."""

    def __init__(self) -> None:
        """Initialize one immediate lexical-scope accumulator.

        Returns:
            None.
        """
        self.bound: set[str] = set()
        self.loaded: set[str] = set()
        self.loaded_lines: dict[str, int] = {}
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        """Record one loaded or bound name in the current lexical scope.

        Args:
            node: Name expression owned by the current lexical scope.

        Returns:
            None.
        """
        if isinstance(node.ctx, ast.Load):
            self.loaded.add(node.id)
            self.loaded_lines[node.id] = node.lineno
        else:
            self.bound.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Treat a nested synchronous function name as a local binding.

        Args:
            node: Nested synchronous function declaration.

        Returns:
            None.
        """
        self.bound.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Treat a nested asynchronous function name as a local binding.

        Args:
            node: Nested asynchronous function declaration.

        Returns:
            None.
        """
        self.bound.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Treat a nested class name as a local binding without descending into it.

        Args:
            node: Nested class declaration.

        Returns:
            None.
        """
        self.bound.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Stop at lambda boundaries because they own a separate lexical scope.

        Args:
            node: Lambda expression that starts a nested lexical scope.

        Returns:
            None.
        """
        return

    def visit_Import(self, node: ast.Import) -> None:
        """Record names introduced by a normal import statement.

        Args:
            node: Import statement owned by the current lexical scope.

        Returns:
            None.
        """
        for alias in node.names:
            self.bound.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record explicit names introduced by a from-import statement.

        Args:
            node: From-import statement owned by the current lexical scope.

        Returns:
            None.
        """
        for alias in node.names:
            if alias.name != "*":
                self.bound.add(alias.asname or alias.name)

    def visit_Global(self, node: ast.Global) -> None:
        """Mark names explicitly bound outside the current function scope.

        Args:
            node: Global declaration in the current lexical scope.

        Returns:
            None.
        """
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        """Mark names explicitly captured from an enclosing function scope.

        Args:
            node: Nonlocal declaration in the current lexical scope.

        Returns:
            None.
        """
        self.nonlocal_names.update(node.names)
        for name in node.names:
            self.loaded_lines[name] = node.lineno

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Record exception aliases while continuing through the handler body.

        Args:
            node: Exception handler owned by the current lexical scope.

        Returns:
            None.
        """
        if node.name:
            self.bound.add(node.name)
        for statement in node.body:
            self.visit(statement)


def function_scope_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], set[str], dict[str, int]]:
    """Collect immediate lexical definitions and referenced names without nesting.

    Args:
        node: Authored function or asynchronous function defining this scope.

    Returns:
        Bound names, loaded names, and source line of each loaded name.
    """
    collector = _ScopeNameCollector()
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    collector.bound.update(argument.arg for argument in arguments)
    if node.args.vararg is not None:
        collector.bound.add(node.args.vararg.arg)
    if node.args.kwarg is not None:
        collector.bound.add(node.args.kwarg.arg)
    for statement in node.body:
        collector.visit(statement)
    collector.bound.difference_update(collector.global_names | collector.nonlocal_names)
    collector.loaded.update(collector.nonlocal_names)
    return collector.bound, collector.loaded, collector.loaded_lines
