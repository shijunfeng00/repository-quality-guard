"""Deterministic local Python import and re-export symbol resolution."""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping


def resolve_local_import_symbol(
    candidate: str,
    imports_by_module: Mapping[str, Mapping[str, str]],
    known_symbols: Collection[str],
) -> str | None:
    """Resolve one repository-local imported symbol through explicit re-exports.

    Args:
        candidate: Dotted symbol produced by an authored import binding.
        imports_by_module: Module-local import bindings, already normalized to dotted names.
        known_symbols: Canonical repository symbol ids accepted as terminal targets.

    Returns:
        Canonical known symbol id, or ``None`` when static import evidence is insufficient.
    """
    current = candidate
    seen: set[str] = set()
    while True:
        if current in known_symbols:
            return current
        if current in seen:
            return None
        seen.add(current)

        parts = current.split(".") if current else []
        expanded: str | None = None
        for split in range(len(parts) - 1, 0, -1):
            module = ".".join(parts[:split])
            if module not in imports_by_module:
                continue
            bindings = imports_by_module[module]
            alias = parts[split]
            if alias not in bindings:
                continue
            target = bindings[alias]
            suffix = parts[split + 1 :]
            expanded = ".".join((target, *suffix)) if suffix else target
            break
        if expanded is None and len(parts) == 1 and "" in imports_by_module:
            root_bindings = imports_by_module[""]
            if parts[0] in root_bindings:
                expanded = root_bindings[parts[0]]
        if expanded is None or expanded == current:
            return None
        current = expanded


def authored_import_bindings(
    current_module: str, path: str, node: ast.Import | ast.ImportFrom
) -> dict[str, str]:
    """Normalize one authored import statement into local-name to target bindings.

    Args:
        current_module: Canonical module name for the importing source file.
        path: Source-relative filename used to resolve package initializer semantics.
        node: Parsed top-level import statement.

    Returns:
        Explicit local bindings without loading modules or executing user code.
    """
    if isinstance(node, ast.Import):
        return {
            alias.asname or alias.name.split(".", 1)[0]: alias.name
            for alias in node.names
        }
    module = node.module or ""
    if node.level:
        package = (
            current_module
            if path == "__init__.py" or path.endswith("/__init__.py")
            else current_module.rpartition(".")[0]
        )
        parts = [part for part in package.split(".") if part]
        remove = node.level - 1
        if remove > len(parts):
            return {}
        base = parts[: len(parts) - remove]
        if module:
            base.extend(module.split("."))
        module = ".".join(base)
    if not module:
        return {}
    return {
        alias.asname or alias.name: f"{module}.{alias.name}"
        for alias in node.names
        if alias.name != "*"
    }


def module_import_declarations(
    module: str,
    path: str,
    node: ast.Import | ast.ImportFrom,
    namespace_modules: Collection[str],
) -> tuple[dict[str, str], bool]:
    """Project authored imports to safe symbol/module prefixes for graph analysis.

    Args:
        module: Canonical module name of the importing source.
        path: Source-relative filename used for relative import resolution.
        node: Parsed top-level import statement.
        namespace_modules: Known namespace-package module identifiers.

    Returns:
        Normalized import bindings and whether a wildcard import opens the namespace.
    """
    if isinstance(node, ast.Import):
        return (
            {
                alias.asname or alias.name.split(".", 1)[0]: (
                    alias.name if alias.asname else alias.name.split(".", 1)[0]
                )
                + "."
                for alias in node.names
            },
            False,
        )
    imports = authored_import_bindings(module, path, node)
    return (
        {
            key: target + "." if target in namespace_modules else target
            for key, target in imports.items()
        },
        any(alias.name == "*" for alias in node.names),
    )


def python_module_name(path: str) -> str:
    """Return the normalized Python module identity from an authored relative path.

    Args:
        path: Original repository-relative or absolute source path, as required by the owner.

    Returns:
        Canonical module identity computed from the source file path."""
    module = path[:-3].replace("/", ".")
    if module == "__init__":
        return ""
    return module[: -len(".__init__")] if module.endswith(".__init__") else module
