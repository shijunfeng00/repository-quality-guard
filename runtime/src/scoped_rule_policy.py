"""Exact file and Python definition scoped rule policy for project Profiles."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from collections.abc import Mapping, Sequence
from typing import Any


UNSUPPRESSIBLE_RULE_PREFIXES = ("QG98", "QG99")

_IDENTIFIER_CHAIN = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*$")


@dataclass(frozen=True, slots=True)
class ScopedRuleDisable:
    """Represent one exact, deliberately bounded Profile rule suppression.

    Fields:
        code: Fully validated QG identifier being suppressed.
        path: Exact repository-relative file path, never a glob.
        symbol: Optional qualified Python declaration; empty means whole file."""

    code: str
    path: str
    symbol: str = ""


def parse_scoped_disables(
    value: Any, validate_code: Any
) -> tuple[ScopedRuleDisable, ...]:
    """Validate explicit Profile suppressions without inventing wildcard scope.

    Each entry must specify a valid rule and exact repository path; optional
    symbol selectors are accepted only as declared. Invalid shapes fail early.

    Args:
        value: Profile-provided sequence of code/path/symbol records.
        validate_code: Validator for registered QG code identifiers.

    Returns:
        Immutable ScopedRuleDisable entries in declaration order.

    Raises:
        ValueError: Malformed entries, unknown code or unsupported path scope."""
    if not isinstance(value, (list, tuple)):
        raise ValueError("profile rules.scoped_disable must be an array")
    entries: list[ScopedRuleDisable] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) not in (
            {"code", "path"},
            {"code", "path", "symbol"},
        ):
            raise ValueError(
                "scoped_disable entries require code, path and optional symbol"
            )
        if not isinstance(raw["code"], str) or not isinstance(raw["path"], str):
            raise ValueError("scoped_disable code and path must be strings")
        code = validate_code(raw["code"])
        if code.startswith(UNSUPPRESSIBLE_RULE_PREFIXES):
            raise ValueError(f"rule {code} belongs to the guard trust boundary")
        path = raw["path"]
        parts = PurePosixPath(path).parts
        if (
            not path
            or path != PurePosixPath(path).as_posix()
            or path.startswith("/")
            or "\\" in path
            or ".." in parts
            or any(char in path for char in "*?[]")
            or path.endswith("/")
            or path == "."
        ):
            raise ValueError(
                f"scoped_disable path must be an exact relative POSIX file: {path!r}"
            )
        symbol = raw.get("symbol", "")
        if not isinstance(symbol, str) or (
            symbol
            and (not path.endswith(".py") or not _IDENTIFIER_CHAIN.fullmatch(symbol))
        ):
            raise ValueError(
                "scoped_disable symbol must be a Python qualified definition"
            )
        entries.append(ScopedRuleDisable(code, path, symbol))
    return tuple(dict.fromkeys(entries))


class ScopedRuleMatcher:
    """Match a finding only to an explicitly authorized file or lexical scope.

    Scope extraction is cached per Python path. Ambiguous names, unknown
    paths and unparsable source are not granted speculative exemptions."""

    def __init__(self, root: Path, entries: Sequence[ScopedRuleDisable]) -> None:
        """Index declared suppressions by their exact rule code and relative path.

        Args:
            root: Repository root used to resolve and confine candidate paths.
            entries: Already validated scoped suppressions; no glob expansion.

        Returns:
            None; initializes the rule/path index and empty AST-scope cache."""
        self.root = root
        grouped: dict[tuple[str, str], list[str]] = {}
        for entry in entries:
            grouped.setdefault((entry.code, entry.path), []).append(entry.symbol)
        self.by_key = {key: tuple(values) for key, values in grouped.items()}
        self._scopes: dict[str, dict[str, tuple[tuple[int, int], ...]]] = {}

    def matches(self, code: str, path: str, line: int) -> bool:
        """Decide whether a finding's line belongs to a declared Python owner.

        Whole-file entries are sufficient without AST parsing; otherwise the
        declaration's unique source span must contain the reported line.

        Args:
            code: Rule identifier attached to the finding.
            path: Repository-relative path, matched exactly.
            line: One-based location of the finding within its source file.

        Returns:
            True only for proven matching file or unique declaration scope."""
        symbols = self.by_key.get((code, path))
        if symbols is None:
            return False
        if "" in symbols:
            return True
        if line < 1:
            return False
        if path not in self._scopes:
            self._scopes[path] = self._parse_scopes(path)
        spans = self._scopes[path]
        return any(
            start <= line <= end
            for symbol in symbols
            for start, end in spans.get(symbol, ())
        )

    def _parse_scopes(self, path: str) -> dict[str, tuple[tuple[int, int], ...]]:
        """Extract authoritative lexical class/function boundaries, not name heuristics."""
        candidate = (self.root / path).resolve()
        # A deleted baseline path or an out-of-repository symlink has no
        # provable current Python definition. Keep the finding rather than
        # granting a speculative scoped exception.
        if not candidate.is_relative_to(self.root.resolve()):
            return {}
        try:
            source = candidate.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            # Invalid code has no provable AST ownership: keep every finding.
            return {}
        found: dict[str, list[tuple[int, int]]] = {}

        def visit(node: ast.AST, prefix: tuple[str, ...]) -> None:
            """Collect qualified lexical owners and decorator-inclusive source spans.

            Args:
                node: Current AST scope in the parsed file.
                prefix: Enclosing class/function names forming lexical identity.

            Returns:
                None; records each discovered owner span in the local index."""
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    qualified = (*prefix, child.name)
                    start = min(
                        (item.lineno for item in child.decorator_list),
                        default=child.lineno,
                    )
                    found.setdefault(".".join(qualified), []).append(
                        (start, child.end_lineno)
                    )
                    visit(child, qualified)
                else:
                    visit(child, prefix)

        visit(tree, ())
        # A qualified name can denote multiple distinct lexical definitions.
        # In that case Profile cannot prove which one the user intended; do
        # not silently suppress every such definition (or its descendants).
        ambiguous = {name for name, spans in found.items() if len(spans) != 1}
        unique: dict[str, tuple[tuple[int, int], ...]] = {}
        for name, spans in found.items():
            if name in ambiguous:
                continue
            components = name.split(".")
            if any(
                ".".join(components[:index]) in ambiguous
                for index in range(1, len(components))
            ):
                continue
            unique[name] = (spans[0],)
        return unique
