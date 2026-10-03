"""JS/TS provider for normalized repository topology facts."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .multilang import SCRIPT_LANGUAGES
from .topology_facts import (
    OwnerFact,
    OwnerKind,
    RepositoryTopology,
    SymbolFact,
    UsageEdge,
    UsageKind,
    Visibility,
)


def script_topology(scripts: list[dict[str, Any]]) -> RepositoryTopology:
    """Adapt supported JS/TS parser facts to the language-neutral topology.

    The bundled parser currently proves lexical definitions and simple call names, but
    it does not prove module exports or TypeScript access modifiers. Those unsupported
    facts remain ``UNKNOWN`` instead of being reconstructed from naming conventions.
    Direct-call edges are emitted only for a unique same-file target.

    Args:
        scripts: Bundled parser facts for supported script-language source files.

    Returns:
        Normalized JS/TS symbols, owners, and statically proven direct-call edges.
    """
    owners: dict[str, OwnerFact] = {}
    symbols: dict[str, SymbolFact] = {}
    by_file_name: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    definition_rows: list[tuple[dict[str, Any], dict[str, Any], str]] = []

    for script in scripts:
        if script["language"] not in SCRIPT_LANGUAGES:
            continue
        module_id = f"{script['language']}:module:{script['path']}"
        owners[module_id] = OwnerFact(
            owner_id=module_id,
            language=script["language"],
            kind=OwnerKind.MODULE,
            path=Path(script["path"]),
            line=1,
        )
        for definition in script["definitions"]:
            if definition["name"].startswith("<anonymous@"):
                continue
            symbol_id = (
                f"{script['language']}:symbol:{script['path']}:"
                f"{definition['qualname']}@{definition['line']}"
            )
            owner_id = module_id
            owner_kind = OwnerKind.MODULE
            scope = definition["scope_key"]
            if scope.startswith("class:"):
                class_label = scope.removeprefix("class:")
                class_name, _, line_text = class_label.rpartition("@")
                class_line = (
                    int(line_text) if line_text.isdigit() else definition["line"]
                )
                owner_id = (
                    f"{script['language']}:class:{script['path']}:"
                    f"{class_name}@{class_line}"
                )
                owner_kind = OwnerKind.CLASS
                owners[owner_id] = OwnerFact(
                    owner_id=owner_id,
                    language=script["language"],
                    kind=OwnerKind.CLASS,
                    path=Path(script["path"]),
                    line=class_line,
                )
            symbols[symbol_id] = SymbolFact(
                symbol_id=symbol_id,
                language=script["language"],
                kind=definition["kind"],
                owner_id=owner_id,
                owner_kind=owner_kind,
                visibility=Visibility.UNKNOWN,
                path=Path(script["path"]),
                line=definition["line"],
                end_line=definition["end_line"],
                lines=definition["end_line"] - definition["line"] + 1,
                parameter_count=definition["parameter_count"],
                name=definition["name"],
                qualname=definition["qualname"],
            )
            by_file_name[(script["path"], definition["name"])].append(symbol_id)
            definition_rows.append((script, definition, symbol_id))

    edges: list[UsageEdge] = []
    for script, definition, source_id in definition_rows:
        seen_targets: set[str] = set()
        for name in definition["calls"]:
            candidates = by_file_name[(script["path"], name)]
            if len(candidates) != 1 or candidates[0] == source_id:
                continue
            target_id = candidates[0]
            if target_id in seen_targets:
                continue
            seen_targets.add(target_id)
            edges.append(
                UsageEdge(
                    source_id=source_id,
                    target_id=target_id,
                    kind=UsageKind.DIRECT_CALL,
                    path=Path(script["path"]),
                    line=definition["line"],
                    confidence="medium",
                )
            )

    return RepositoryTopology(
        symbols=tuple(sorted(symbols.values(), key=lambda item: item.symbol_id)),
        owners=tuple(sorted(owners.values(), key=lambda item: item.owner_id)),
        edges=tuple(
            sorted(
                edges,
                key=lambda item: (
                    item.path.as_posix(),
                    item.line,
                    item.source_id,
                    item.target_id,
                    item.kind,
                ),
            )
        ),
    )
