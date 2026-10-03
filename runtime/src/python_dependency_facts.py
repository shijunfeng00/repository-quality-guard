"""Bounded static Python dependency symbol resolution.

The resolver reads source or stub files from configured import roots. It never imports
third-party packages merely to discover protocol or callback contracts.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

from .model import Confidence

_MAX_RESOLUTION_DEPTH = 6
_MAX_SOURCE_BYTES = 2_000_000


@dataclass(slots=True, frozen=True)
class ExternalClassFact:
    """Describe one statically resolved external Python class contract.

    Attributes:
        qualified_name: Fully qualified external class name.
        methods: Directly declared method names on the resolved class.
        source: Source or stub file that supplied the contract.
        line: Class declaration line in the external source.
        confidence: Confidence after direct resolution or re-export traversal.
    """

    qualified_name: str
    methods: tuple[str, ...]
    source: Path
    line: int
    confidence: Confidence


class StaticPythonDependencyResolver:
    """Resolve external Python class contracts without importing dependency code.

    Resolution is bounded by explicit filesystem roots, source size and re-export depth.
    Missing or unsupported metadata remains unresolved instead of triggering runtime import.
    """

    def __init__(self, search_roots: tuple[Path, ...]) -> None:
        """Create a bounded resolver over explicit import roots.

        Args:
            search_roots: Existing directories searched in stable order.

        Returns:
            None.
        """
        self.search_roots = tuple(
            root.resolve() for root in search_roots if root.is_dir()
        )
        self._module_cache: dict[str, tuple[Path, ast.Module, bool] | None] = {}
        self._class_cache: dict[str, ExternalClassFact | None] = {}

    @classmethod
    def from_environment(
        cls, extra_roots: tuple[Path, ...] = ()
    ) -> StaticPythonDependencyResolver:
        """Create a resolver from static interpreter search paths.

        Args:
            extra_roots: Additional dependency roots placed before interpreter paths.

        Returns:
            Resolver that inspects files only and never imports discovered packages.
        """
        roots: list[Path] = [*extra_roots]
        for entry in sys.path:
            if not entry:
                continue
            candidate = Path(entry)
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
        return cls(tuple(roots))

    def resolve_class(self, qualified_name: str) -> ExternalClassFact | None:
        """Resolve a fully qualified external class using source/stub metadata only.

        Args:
            qualified_name: Dotted class name after project import alias expansion.

        Returns:
            Resolved external class contract, or ``None`` when static evidence is absent.
        """
        if qualified_name in self._class_cache:
            return self._class_cache[qualified_name]
        result = self._resolve_class(qualified_name, 0, frozenset())
        self._class_cache[qualified_name] = result
        return result

    def _resolve_class(
        self,
        qualified_name: str,
        depth: int,
        seen: frozenset[str],
    ) -> ExternalClassFact | None:
        """Resolve direct class declarations or bounded ``from`` re-exports."""
        if depth > _MAX_RESOLUTION_DEPTH or qualified_name in seen:
            return None
        parts = qualified_name.split(".")
        next_seen = seen | {qualified_name}
        for split in range(len(parts) - 1, 0, -1):
            module = ".".join(parts[:split])
            symbol_path = parts[split:]
            loaded = self._load_module(module)
            if loaded is None:
                continue
            source, tree, is_package = loaded
            direct = self._class_declared_in_module(
                qualified_name, symbol_path, source, tree, depth
            )
            if direct is not None:
                return direct
            if len(symbol_path) != 1:
                continue
            resolved = self._resolve_reexported_class(
                qualified_name,
                module,
                symbol_path[0],
                tree,
                is_package,
                depth,
                next_seen,
            )
            if resolved is not None:
                return resolved
        return None

    def _class_declared_in_module(
        self,
        qualified_name: str,
        symbol_path: list[str],
        source: Path,
        tree: ast.Module,
        depth: int,
    ) -> ExternalClassFact | None:
        """Return a class contract declared directly under one loaded module path."""
        current_body: list[ast.stmt] = tree.body
        class_node: ast.ClassDef | None = None
        for symbol in symbol_path:
            class_node = next(
                (
                    item
                    for item in current_body
                    if isinstance(item, ast.ClassDef) and item.name == symbol
                ),
                None,
            )
            if class_node is None:
                return None
            current_body = class_node.body
        methods = tuple(
            sorted(
                item.name
                for item in class_node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
        )
        return ExternalClassFact(
            qualified_name=qualified_name,
            methods=methods,
            source=source,
            line=class_node.lineno,
            confidence="high" if depth == 0 else "medium",
        )

    def _resolve_reexported_class(
        self,
        qualified_name: str,
        module: str,
        target_name: str,
        tree: ast.Module,
        is_package: bool,
        depth: int,
        seen: frozenset[str],
    ) -> ExternalClassFact | None:
        """Follow one bounded from-import re-export for an external class contract."""
        for statement in tree.body:
            if not isinstance(statement, ast.ImportFrom) or statement.module is None:
                continue
            alias = next(
                (
                    item
                    for item in statement.names
                    if (item.asname or item.name) == target_name
                ),
                None,
            )
            if alias is None:
                continue
            target_module = self._resolve_import_module(
                module, is_package, statement.module, statement.level
            )
            if not target_module:
                continue
            resolved = self._resolve_class(
                f"{target_module}.{alias.name}", depth + 1, seen
            )
            if resolved is not None:
                return ExternalClassFact(
                    qualified_name=qualified_name,
                    methods=resolved.methods,
                    source=resolved.source,
                    line=resolved.line,
                    confidence="medium",
                )
        return None

    def _load_module(self, module: str) -> tuple[Path, ast.Module, bool] | None:
        """Load one module AST from a static source/stub path with bounded file size."""
        if module in self._module_cache:
            return self._module_cache[module]
        relative = Path(*module.split("."))
        for root in self.search_roots:
            candidates = (
                (root / relative).with_suffix(".pyi"),
                (root / relative).with_suffix(".py"),
                root / relative / "__init__.pyi",
                root / relative / "__init__.py",
            )
            for candidate in candidates:
                if (
                    not candidate.is_file()
                    or candidate.stat().st_size > _MAX_SOURCE_BYTES
                ):
                    continue
                try:
                    source = candidate.read_text(encoding="utf-8")
                    tree = ast.parse(source, filename=str(candidate))
                except (OSError, UnicodeDecodeError, SyntaxError):
                    continue
                loaded = (
                    candidate.resolve(),
                    tree,
                    candidate.name.startswith("__init__."),
                )
                self._module_cache[module] = loaded
                return loaded
        self._module_cache[module] = None
        return None

    @staticmethod
    def _resolve_import_module(
        current_module: str,
        current_is_package: bool,
        imported_module: str,
        level: int,
    ) -> str:
        """Resolve a relative ``from`` import without importing either module."""
        if level == 0:
            return imported_module
        package = (
            current_module if current_is_package else current_module.rpartition(".")[0]
        )
        parts = [part for part in package.split(".") if part]
        remove = level - 1
        if remove > len(parts):
            return ""
        base = parts[: len(parts) - remove]
        if imported_module:
            base.extend(imported_module.split("."))
        return ".".join(base)
