from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from .config import GuardConfig, is_test_path
from .model import Finding
from .topology_facts import RepositoryTopology, SymbolFact, UsageEdge, UsageKind
from .topology_policy import (
    ephemeral_helper_candidate,
    symbol_is_externally_invoked,
    symbol_usage_evidence,
)


def _in_scope(
    symbol: SymbolFact,
    config: GuardConfig,
    paths: set[str] | None,
) -> bool:
    """Return whether one authored callable participates in QG168 analysis."""
    relative = symbol.path.as_posix()
    if symbol.foreign or symbol.generated or symbol.kind not in {"function", "method"}:
        return False
    if is_test_path(relative, config.project_name):
        return False
    if any(fnmatch(relative, pattern) for pattern in config.exclude):
        return False
    return paths is None or relative in paths


def _incoming_direct_edges(
    topology: RepositoryTopology, symbol_id: str
) -> tuple[UsageEdge, ...]:
    """Return high-confidence ordinary callers for one symbol."""
    return tuple(
        edge
        for edge in topology.incoming(symbol_id, (UsageKind.DIRECT_CALL,))
        if edge.confidence == "high"
    )


def _eligible_helpers(
    topology: RepositoryTopology,
    config: GuardConfig,
    paths: set[str] | None,
) -> dict[str, tuple[SymbolFact, dict[str, object]]]:
    """Return topology-proven ephemeral helpers eligible for QG168 chains."""
    eligible: dict[str, tuple[SymbolFact, dict[str, object]]] = {}
    for symbol in topology.symbols:
        if not _in_scope(symbol, config, paths):
            continue
        evidence = symbol_usage_evidence(symbol, topology)
        direct_edges = topology.incoming(symbol.symbol_id, (UsageKind.DIRECT_CALL,))
        if any(edge.confidence != "high" for edge in direct_edges):
            continue
        if not ephemeral_helper_candidate(
            symbol.kind,
            symbol.name,
            symbol.lines,
            evidence,
            config,
            symbol_is_externally_invoked(symbol),
        ):
            continue
        eligible[symbol.symbol_id] = (symbol, evidence.as_dict())
    return eligible


def _maximal_chain(
    start_id: str,
    eligible: dict[str, tuple[SymbolFact, dict[str, object]]],
    topology: RepositoryTopology,
) -> tuple[str, tuple[str, ...]] | None:
    """Return a maximal same-owner helper chain beginning at one eligible symbol.

    Args:
        start_id: Candidate first helper symbol.
        eligible: Topology-proven ephemeral helper symbols and usage evidence.
        topology: Repository graph that owns the direct-call edges.

    Returns:
        Caller id plus maximal helper chain, or None when the symbol is not a root.
    """
    incoming = _incoming_direct_edges(topology, start_id)
    if len(incoming) != 1 or incoming[0].source_id in eligible:
        return None
    caller_id = incoming[0].source_id
    chain = [start_id]
    while True:
        current_id = chain[-1]
        current_symbol = eligible[current_id][0]
        next_ids = {
            edge.target_id
            for edge in topology.outgoing(current_id, (UsageKind.DIRECT_CALL,))
            if edge.confidence == "high"
            and edge.target_id in eligible
            and eligible[edge.target_id][0].owner_id == current_symbol.owner_id
        }
        if len(next_ids) != 1:
            break
        next_id = next(iter(next_ids))
        if next_id in chain:
            break
        next_incoming = _incoming_direct_edges(topology, next_id)
        if len(next_incoming) != 1 or next_incoming[0].source_id != current_id:
            break
        chain.append(next_id)
    return caller_id, tuple(chain)


def _chain_finding(
    caller_id: str,
    chain: tuple[str, ...],
    eligible: dict[str, tuple[SymbolFact, dict[str, object]]],
    topology: RepositoryTopology,
) -> Finding:
    """Build the stable QG168 finding for one proven maximal helper chain.

    Args:
        caller_id: Non-helper caller that enters the chain.
        chain: Ordered eligible helper symbol ids.
        eligible: Helper symbols and normalized usage evidence.
        topology: Repository graph used to label the upstream caller.

    Returns:
        One high-confidence QG168 Critical finding.
    """
    first = eligible[chain[0]][0]
    caller = next(
        (item for item in topology.symbols if item.symbol_id == caller_id), None
    )
    caller_label = caller.qualname or caller.name if caller is not None else caller_id
    helper_labels = [
        eligible[item][0].qualname or eligible[item][0].name for item in chain
    ]
    chain_text = " → ".join([caller_label, *helper_labels])
    return Finding(
        code="QG168",
        severity="critical",
        confidence="high",
        path=first.path.as_posix(),
        line=first.line,
        column=1,
        symbol=first.symbol_id,
        message=(
            "检测到连续 one-shot helper 链："
            f"`{chain_text}`。链中 {len(chain)} 个 helper 均由 normalized topology "
            "证明为短小、单次普通调用、无 callback/protocol 复用。"
        ),
        suggestion=(
            "默认从最下游开始内联并删除中间 helper，直到剩余节点拥有真实复用或"
            "独立事务、资源、并发或生命周期边界；不得为降低复杂度数字保留纯转发链。"
        ),
        evidence={
            "chain": [caller_id, *chain],
            "owner": first.owner_id,
            "helper_count": len(chain),
            "helper_lines": [eligible[item][0].lines for item in chain],
            "total_helper_lines": sum(eligible[item][0].lines for item in chain),
            "usage": {item: eligible[item][1] for item in chain},
            "edge_kind": UsageKind.DIRECT_CALL.value,
            "topology_resolution": "normalized",
        },
    )


def single_use_chain_findings(
    root: Path,
    config: GuardConfig,
    topology: RepositoryTopology,
    paths: set[str] | None = None,
) -> list[Finding]:
    """Detect high-confidence chains of topology-proven one-shot helpers.

    QG168 consumes the same ephemeral-helper policy as QG001/QG013. Only ordinary
    high-confidence DIRECT_CALL edges may form a chain. Callback/reference/protocol
    consumers therefore break the chain instead of being mistaken for decomposition.

    Args:
        root: Current repository root retained by the architecture-rule call contract.
        config: Helper length, low-use and chain-length policy.
        topology: Normalized ownership/reuse graph for the same source snapshot.
        paths: Optional repository-relative production paths to analyze.

    Returns:
        One Critical finding for each maximal >=3-helper one-shot chain.
    """
    eligible = _eligible_helpers(topology, config, paths)
    findings: list[Finding] = []
    emitted: set[tuple[str, ...]] = set()
    for symbol_id in sorted(eligible):
        result = _maximal_chain(symbol_id, eligible, topology)
        if result is None:
            continue
        caller_id, chain = result
        if len(chain) < config.single_use_chain_min_helpers or chain in emitted:
            continue
        emitted.add(chain)
        findings.append(_chain_finding(caller_id, chain, eligible, topology))
    return findings
