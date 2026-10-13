"""JS/TS provider for normalized repository topology facts."""

from __future__ import annotations

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

    The TypeScript Compiler API supplies source-located declaration targets.
    All observed calls remain MAY-dispatch even when lexical binding is unique;
    dynamic dispatch and external entries remain unknown.

    Args:
        scripts: Bundled parser facts for supported script-language source files.

    Returns:
        Normalized JS/TS symbols, owners, and compiler-bound MAY-dispatch edges.
    """
    owners: dict[str, OwnerFact] = {}
    symbols: dict[str, SymbolFact] = {}
    definition_rows: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    locations: dict[tuple[str, int, int], str] = {}

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
            if "column" in definition:
                position = (script["path"], definition["line"], definition["column"])
                if position in locations:
                    raise ValueError(
                        f"duplicate authored script source location: {position}"
                    )
                locations[position] = symbol_id
            definition_rows.append((script, definition, symbol_id))

    edges: list[UsageEdge] = []
    for script, definition, source_id in definition_rows:
        # A TypeScript symbol resolution locates a real authored declaration,
        # never an identifier with coincidentally equal spelling. Its target
        # is MAY dispatch because runtime method/variable rebinding is open.
        for call in definition.get("callsites", ()):
            target = call.get("target")
            if target is None:
                continue
            point = (target["path"], target["line"], target["column"])
            target_id = locations.get(point)
            if target_id is None:
                continue
            edges.append(
                UsageEdge(
                    source_id=source_id,
                    target_id=target_id,
                    kind=UsageKind.MAY_DISPATCH,
                    path=Path(script["path"]),
                    line=call["line"],
                    confidence="high",
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
