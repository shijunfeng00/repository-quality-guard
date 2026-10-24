"""Conservative Python documentation inheritance using resolved override relationships.

This policy applies only to Python definitions currently measured by QG027–030.
Native C++ and Java symbols need provider-specific documentation facts before they
can participate; method-name similarity is not a substitute for typed override proof.
"""

from __future__ import annotations

from collections import defaultdict

from .ast_utils import decorator_leaf
from .config import GuardConfig
from .model import Definition
from .topology_facts import RepositoryTopology, UsageKind


def documented_override_ids(
    definitions: list[Definition],
    topology: RepositoryTopology | None,
    config: GuardConfig,
) -> frozenset[str]:
    """Find missing Python method documentation inherited from an abstract slot.

    Require a high-confidence override edge, source-exact parent declaration,
    matching parameter names, and a complete parent contract. For multiple
    inheritance, an earlier base can shadow a documented later base; only
    accept the first demonstrably effective implementation in source base order.
    Unresolved base identities or possible inherited shadows leave the method
    subject to normal document checks.

    Args:
        definitions: Python definitions parsed for this repository scan.
        topology: Shared repository topology with resolved inheritance edges.
        config: Current documentation requirements and completeness thresholds.

    Returns:
        IDs of undocumented methods for which local documentation is optional.
    """
    if topology is None or not config.require_docstrings:
        return frozenset()

    definition_by_id = {item.symbol_id: item for item in definitions}
    symbol_by_id = {item.symbol_id: item for item in topology.symbols}
    bases_by_owner: dict[str, set[str]] = defaultdict(set)
    overrides: dict[str, set[str]] = defaultdict(set)
    for edge in topology.edges:
        if edge.confidence != "high":
            continue
        if edge.kind is UsageKind.INHERITANCE:
            bases_by_owner[edge.source_id].add(edge.target_id)
        elif edge.kind is UsageKind.OVERRIDE:
            overrides[edge.source_id].add(edge.target_id)

    def bound(symbol_id: str, kind: str) -> Definition | None:
        """Require the graph and scanner to identify the same authored declaration.

        Args:
            symbol_id: Exact normalized identity of a class or method.
            kind: Expected declaration type, either class or method.

        Returns:
            The scanned definition when path, line, kind, and language agree;
            otherwise None. Unknown external symbols never qualify.
        """
        definition = definition_by_id.get(symbol_id)
        symbol = symbol_by_id.get(symbol_id)
        if (
            definition is None
            or symbol is None
            or definition.kind != kind
            or symbol.kind != kind
            or symbol.language != "python"
            or definition.path != symbol.path
            or definition.line != symbol.line
        ):
            return None
        return definition

    def ordered_bases(owner_id: str) -> tuple[str, ...] | None:
        """Reconstruct the order of fully resolved direct Python base classes.

        Args:
            owner_id: Exact normalized identity of the overriding class.

        Returns:
            Proven direct bases in declared order, or None when a base cannot
            be matched uniquely to an inheritance edge and source declaration.
        """
        owner = bound(owner_id, "class")
        if owner is None:
            return None
        resolved = bases_by_owner.get(owner_id, set())
        if len(resolved) != len(owner.bases):
            return None
        remaining = set(resolved)
        ordered = []
        for expression in owner.bases:
            # Resolve *within proven inheritance edges*. Do not create an edge
            # based solely on a matching class name or assumed import alias.
            matches = [
                base_id
                for base_id in remaining
                if (
                    (base := bound(base_id, "class")) is not None
                    and (expression == base.name or expression == base.symbol_id)
                )
            ]
            if len(matches) != 1:
                return None
            ordered.append(matches[0])
            remaining.remove(matches[0])
        return tuple(ordered)

    # This is deliberately not a general dynamic Python MRO emulator. A direct
    # first-base method wins; a base with unknown ancestor methods may not be
    # treated as an empty slot ahead of another base. Later ambiguous slots fail
    # closed instead of selecting a convenient documented parent.
    accepted: set[str] = set()
    for definition in definitions:
        if definition.kind != "method" or definition.has_docstring:
            continue
        symbol = symbol_by_id.get(definition.symbol_id)
        if symbol is None or bound(definition.symbol_id, "method") is None:
            continue
        base_ids = ordered_bases(symbol.owner_id)
        if not base_ids:
            continue
        candidates = overrides.get(definition.symbol_id, set())
        if not candidates:
            continue
        effective: Definition | None = None
        for base_id in base_ids:
            direct_id = f"{base_id}.{definition.name}"
            if direct_id in candidates:
                effective = bound(direct_id, "method")
                break
            # A base that has ancestors might inherit the same method even
            # without declaring it locally; its first MRO slot is unknown.
            previous = bound(base_id, "class")
            if previous is None or previous.bases:
                break
        if effective is None:
            continue
        if not any(
            decorator_leaf(value) == "abstractmethod" for value in effective.decorators
        ):
            continue
        if effective.parameter_names != definition.parameter_names:
            continue
        private = effective.name.startswith("_") and not (
            effective.name.startswith("__") and effective.name.endswith("__")
        )
        if private:
            complete = effective.has_docstring
        else:
            complete = effective.has_complete_docstring(
                config.docstring_min_lines, config.require_docstring_sections
            )
        if complete:
            accepted.add(definition.symbol_id)
    return frozenset(accepted)
