"""Language-neutral ownership and reuse topology facts.

Language providers translate syntax/type-system constructs into this model. Quality
policy consumes the normalized graph instead of branching on language names whenever
those facts are available.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .model import Confidence, Definition, Usage


class OwnerKind(StrEnum):
    """Classify an authored architectural owner.

    The shared policy layer uses these values without depending on language syntax.
    """

    MODULE = "module"
    CLASS = "class"
    NAMESPACE = "namespace"
    SUBSYSTEM = "subsystem"
    UNKNOWN = "unknown"


class Visibility(StrEnum):
    """Describe normalized authored-symbol visibility.

    Providers map language-specific access rules or conventions onto these values.
    """

    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class UsageKind(StrEnum):
    """Classify a semantic relationship between two normalized symbols.

    Direct calls remain distinct from callback, protocol, state and dependency edges.
    """

    DIRECT_CALL = "direct_call"
    CALLABLE_REFERENCE = "callable_reference"
    CALLBACK_REGISTRATION = "callback_registration"
    PROTOCOL_HOOK = "protocol_hook"
    OVERRIDE = "override"
    STATIC_POLYMORPHIC_DISPATCH = "static_polymorphic_dispatch"
    FIELD_ACCESS = "field_access"
    DEPENDENCY = "dependency"
    CLOSURE_CAPTURE = "closure_capture"
    INHERITANCE = "inheritance"


class ContractOwnership(StrEnum):
    """Classify who owns a runtime contract inspected by a quality rule.

    Unknown ownership stays explicit instead of being guessed as internal or external.
    """

    INTERNAL_FORMAL = "internal_formal"
    EXTERNAL_OPTIONAL = "external_optional"
    DYNAMIC_BOUNDARY = "dynamic_boundary"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class SymbolFact:
    """Represent one normalized authored definition.

    The record keeps only facts that shared quality policy may consume across languages.
    """

    symbol_id: str
    language: str
    kind: str
    owner_id: str
    owner_kind: OwnerKind
    visibility: Visibility
    path: Path
    line: int
    end_line: int
    lines: int
    parameter_count: int = 0
    name: str = ""
    module: str = ""
    qualname: str = ""
    decorators: tuple[str, ...] = ()
    bases: tuple[str, ...] = ()
    exported: bool = False
    nested: bool = False
    test: bool = False
    fingerprint: str = ""
    generated: bool = False
    foreign: bool = False


@dataclass(slots=True, frozen=True)
class OwnerFact:
    """Represent a normalized architectural owner.

    An owner may be a module, class, namespace or later an explicit subsystem.
    """

    owner_id: str
    language: str
    kind: OwnerKind
    path: Path
    line: int


@dataclass(slots=True, frozen=True)
class UsageEdge:
    """Represent one typed static relationship between symbols or owners.

    The edge kind preserves how a target is consumed instead of collapsing all usage.
    """

    source_id: str
    target_id: str
    kind: UsageKind
    path: Path
    line: int
    confidence: Confidence = "high"


@dataclass(slots=True, frozen=True)
class ContractFact:
    """Represent one normalized runtime-contract access and its static ownership.

    Language providers record the access shape separately from the ownership verdict so
    shared policy can distinguish formal internal contracts, declared external optional
    contracts, explicit dynamic boundaries and unresolved cases without guessing.
    """

    path: Path
    line: int
    receiver: str
    ownership: ContractOwnership
    confidence: Confidence
    operation: str = ""
    selector: str = ""
    owner_id: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class RepositoryTopology:
    """Hold the repository-level normalized ownership and reuse graph.

    The graph is generated during scanning but remains policy-neutral in Phase 1.
    """

    symbols: tuple[SymbolFact, ...]
    owners: tuple[OwnerFact, ...]
    edges: tuple[UsageEdge, ...]
    contracts: tuple[ContractFact, ...] = ()

    def incoming(
        self, target_id: str, kinds: tuple[UsageKind, ...] = ()
    ) -> tuple[UsageEdge, ...]:
        """Return stable incoming edges for a target, optionally filtered by kind.

        Args:
            target_id: Normalized target symbol identifier.
            kinds: Accepted edge kinds; empty means all kinds.

        Returns:
            Incoming edges in stable repository order.
        """
        accepted = frozenset(kinds)
        return tuple(
            edge
            for edge in self.edges
            if edge.target_id == target_id and (not accepted or edge.kind in accepted)
        )

    def state_components(self, owner_id: str) -> tuple[tuple[str, ...], ...]:
        """Return field-connected state components for one authored owner.

        Methods/functions and fields participate only when a FIELD_ACCESS edge links
        them. Isolated callables without state access are omitted rather than treated
        as artificial singleton owner clusters.

        Args:
            owner_id: Normalized class/module owner identifier.

        Returns:
            Stable connected components containing at least one field and callable.
        """
        owned = {
            symbol.symbol_id: symbol
            for symbol in self.symbols
            if symbol.owner_id == owner_id
        }
        adjacency: dict[str, set[str]] = {}
        for edge in self.edges:
            if edge.kind is not UsageKind.FIELD_ACCESS:
                continue
            if edge.source_id not in owned or edge.target_id not in owned:
                continue
            adjacency.setdefault(edge.source_id, set()).add(edge.target_id)
            adjacency.setdefault(edge.target_id, set()).add(edge.source_id)

        components: list[tuple[str, ...]] = []
        pending = set(adjacency)
        while pending:
            start = min(pending)
            stack = [start]
            visited: set[str] = set()
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                stack.extend(sorted(adjacency[current] - visited, reverse=True))
            pending.difference_update(visited)
            has_field = any(owned[item].kind == "field" for item in visited)
            has_callable = any(
                owned[item].kind in {"function", "method"} for item in visited
            )
            if has_field and has_callable:
                components.append(tuple(sorted(visited)))
        return tuple(sorted(components))

    def outgoing(
        self, source_id: str, kinds: tuple[UsageKind, ...] = ()
    ) -> tuple[UsageEdge, ...]:
        """Return stable outgoing edges for a source, optionally filtered by kind.

        Args:
            source_id: Normalized source symbol or owner identifier.
            kinds: Accepted edge kinds; empty means all kinds.

        Returns:
            Outgoing edges in stable repository order.
        """
        accepted = frozenset(kinds)
        return tuple(
            edge
            for edge in self.edges
            if edge.source_id == source_id and (not accepted or edge.kind in accepted)
        )


def merge_topologies(topologies: tuple[RepositoryTopology, ...]) -> RepositoryTopology:
    """Merge language-provider topology into one stable repository graph.

    Providers own syntax extraction, while shared QG policy consumes one normalized
    graph.  The merge therefore deduplicates identical immutable facts but rejects
    conflicting symbol/owner identities instead of silently choosing one provider.

    Args:
        topologies: Provider graphs generated from the same repository snapshot.

    Returns:
        One deterministically ordered normalized repository topology.

    Raises:
        ValueError: Two providers emitted different facts for the same symbol/owner id.
    """
    symbols: dict[str, SymbolFact] = {}
    owners: dict[str, OwnerFact] = {}
    edges: set[UsageEdge] = set()
    contracts: set[ContractFact] = set()

    for topology in topologies:
        for symbol in topology.symbols:
            if symbol.symbol_id in symbols and symbols[symbol.symbol_id] != symbol:
                raise ValueError(f"conflicting normalized symbol: {symbol.symbol_id}")
            symbols[symbol.symbol_id] = symbol
        for owner in topology.owners:
            if owner.owner_id in owners and owners[owner.owner_id] != owner:
                raise ValueError(f"conflicting normalized owner: {owner.owner_id}")
            owners[owner.owner_id] = owner
        edges.update(topology.edges)
        contracts.update(topology.contracts)

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
        contracts=tuple(
            sorted(
                contracts,
                key=lambda item: (
                    item.path.as_posix(),
                    item.line,
                    item.receiver,
                    item.operation,
                    item.selector,
                ),
            )
        ),
    )


def python_usage_edge(usage: Usage, target: Definition) -> UsageEdge:
    """Translate a resolved Python usage into a normalized typed edge.

    Args:
        usage: Static Python usage after repository-local target resolution.
        target: Resolved repository definition consumed by the usage.

    Returns:
        A direct-call or callable-reference edge with the lexical source symbol.
    """
    source_id = (
        f"{usage.module}.{usage.owner_qualname}"
        if usage.module and usage.owner_qualname
        else usage.owner_qualname or usage.module
    )
    return UsageEdge(
        source_id=source_id,
        target_id=target.symbol_id,
        kind=(UsageKind.DIRECT_CALL if usage.is_call else UsageKind.CALLABLE_REFERENCE),
        path=usage.path,
        line=usage.line,
    )


def python_topology(
    definitions: list[Definition], resolved_edges: list[UsageEdge]
) -> RepositoryTopology:
    """Normalize existing Python scanner facts without changing rule decisions.

    Args:
        definitions: Existing resolved Python definitions from the scanner.
        resolved_edges: Typed repository-local usage edges produced during resolution.

    Returns:
        Repository topology containing symbols, module/class owners and usage edges.
    """
    symbol_facts: list[SymbolFact] = []
    for definition in definitions:
        if definition.kind == "method":
            owner_qualname, _, _ = definition.qualname.rpartition(".")
            owner_id = (
                f"{definition.module}.{owner_qualname}"
                if definition.module and owner_qualname
                else owner_qualname or definition.module
            )
            owner_kind = OwnerKind.CLASS
        else:
            owner_id = definition.module
            owner_kind = OwnerKind.MODULE
        name = definition.name
        if name.startswith("__") and not name.endswith("__"):
            visibility = Visibility.PRIVATE
        elif name.startswith("_") and not name.endswith("__"):
            visibility = Visibility.INTERNAL
        else:
            visibility = Visibility.PUBLIC
        symbol_facts.append(
            SymbolFact(
                symbol_id=definition.symbol_id,
                language="python",
                kind=definition.kind,
                owner_id=owner_id,
                owner_kind=owner_kind,
                visibility=visibility,
                path=definition.path,
                line=definition.line,
                end_line=definition.end_line,
                lines=definition.lines,
                parameter_count=definition.parameter_count,
                nested=definition.nested,
            )
        )
    symbols = tuple(sorted(symbol_facts, key=lambda item: item.symbol_id))
    owner_map: dict[str, OwnerFact] = {}
    for definition in definitions:
        if definition.module and definition.module not in owner_map:
            owner_map[definition.module] = OwnerFact(
                owner_id=definition.module,
                language="python",
                kind=OwnerKind.MODULE,
                path=definition.path,
                line=1,
            )
        if definition.kind != "class":
            continue
        owner_map[definition.symbol_id] = OwnerFact(
            owner_id=definition.symbol_id,
            language="python",
            kind=OwnerKind.CLASS,
            path=definition.path,
            line=definition.line,
        )
    edges = tuple(
        sorted(
            resolved_edges,
            key=lambda item: (
                item.path.as_posix(),
                item.line,
                item.source_id,
                item.target_id,
                item.kind,
            ),
        )
    )
    return RepositoryTopology(
        symbols=symbols,
        owners=tuple(sorted(owner_map.values(), key=lambda item: item.owner_id)),
        edges=edges,
    )
