"""Deterministic local Python import and re-export symbol resolution."""

from __future__ import annotations

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
