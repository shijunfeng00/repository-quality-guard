from __future__ import annotations

from dataclasses import dataclass

from .config import GuardConfig
from .topology_facts import RepositoryTopology, SymbolFact, UsageKind, Visibility

_EXTERNAL_DECORATOR_MARKERS = frozenset(
    {
        "abstractmethod",
        "callback",
        "command",
        "event",
        "fixture",
        "property",
        "route",
        "signal",
        "task",
        "validator",
    }
)


@dataclass(slots=True, frozen=True)
class HelperUsageEvidence:
    """Describe normalized usage facts shared by helper-fragmentation rules.

    The immutable record keeps direct-call reuse separate from callback/protocol
    consumers so QG001, QG013 and QG168 cannot classify the same symbol differently.
    """

    direct_callers: int
    direct_call_sites: int
    callable_consumers: int
    protocol_edges: int
    visibility: str
    exported: bool
    owner_id: str
    nested: bool = False
    topology_resolution: str = "normalized"

    def as_dict(self) -> dict[str, object]:
        """Return stable report evidence.

        Returns:
            A plain mapping containing the normalized helper-usage facts.
        """
        return {
            "direct_callers": self.direct_callers,
            "direct_call_sites": self.direct_call_sites,
            "callable_consumers": self.callable_consumers,
            "protocol_edges": self.protocol_edges,
            "visibility": self.visibility,
            "exported": self.exported,
            "owner_id": self.owner_id,
            "nested": self.nested,
            "topology_resolution": self.topology_resolution,
        }


def symbol_usage_evidence(
    symbol: SymbolFact, topology: RepositoryTopology
) -> HelperUsageEvidence:
    """Compute normalized reuse evidence for one authored callable.

    Args:
        symbol: Authored symbol being classified.
        topology: Repository ownership/reuse graph for the same source snapshot.

    Returns:
        Direct-call, callable-reference, protocol and visibility evidence.
    """
    direct = topology.incoming(symbol.symbol_id, (UsageKind.DIRECT_CALL,))
    callable_edges = topology.incoming(
        symbol.symbol_id,
        (UsageKind.CALLABLE_REFERENCE, UsageKind.CALLBACK_REGISTRATION),
    )
    protocol = topology.outgoing(
        symbol.symbol_id, (UsageKind.PROTOCOL_HOOK, UsageKind.OVERRIDE)
    )
    return HelperUsageEvidence(
        direct_callers=len({edge.source_id for edge in direct}),
        direct_call_sites=len(direct),
        callable_consumers=len({edge.source_id for edge in callable_edges}),
        protocol_edges=len(protocol),
        visibility=symbol.visibility.value,
        exported=symbol.exported,
        owner_id=symbol.owner_id,
        nested=symbol.nested,
    )


def symbol_is_externally_invoked(symbol: SymbolFact) -> bool:
    """Return whether static metadata marks framework/protocol invocation.

    Args:
        symbol: Authored symbol whose decorators/bases/name are inspected.

    Returns:
        True when the symbol is a declared hook/callback/protocol entry point.
    """
    if symbol.name.startswith("__") and symbol.name.endswith("__"):
        return True
    if symbol.name.startswith("visit_") and any(
        base.endswith("NodeVisitor") for base in symbol.bases
    ):
        return True
    return any(
        marker in decorator.lower()
        for marker in _EXTERNAL_DECORATOR_MARKERS
        for decorator in symbol.decorators
    )


def ephemeral_helper_candidate(
    kind: str,
    name: str,
    lines: int,
    evidence: HelperUsageEvidence,
    config: GuardConfig,
    externally_invoked: bool,
) -> bool:
    """Return whether one callable is a short internal one-shot helper candidate.

    Args:
        kind: Normalized callable kind.
        name: Authored callable name.
        lines: Definition code-line count.
        evidence: Normalized direct/reference/protocol usage facts.
        config: Current helper length and low-use thresholds.
        externally_invoked: Whether decorators/bases/name expose a framework hook.

    Returns:
        True only when the callable is internal, short and lacks real reuse evidence.
    """
    internal = evidence.nested or evidence.visibility in {
        Visibility.INTERNAL.value,
        Visibility.PRIVATE.value,
    }
    return (
        kind in {"function", "method"}
        and name not in config.ignored_names
        and not (name.startswith("__") and name.endswith("__"))
        and lines <= config.short_max_lines
        and internal
        and not evidence.exported
        and evidence.direct_callers <= config.low_use_max_calls
        and evidence.direct_call_sites <= config.low_use_max_calls
        and evidence.callable_consumers == 0
        and evidence.protocol_edges == 0
        and not externally_invoked
    )
