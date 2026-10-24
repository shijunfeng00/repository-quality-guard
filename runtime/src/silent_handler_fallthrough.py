"""QG215 compatibility entrypoint for independently owned exception rule."""

from __future__ import annotations

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .exception_handler_rule_objects import exception_handler_rule_findings
from .model import Finding


def silent_handler_fallthrough_findings(
    snapshot: RepositoryAnalysisSnapshot,
) -> tuple[Finding, ...]:
    """Select QG215 handler-fallthrough findings from unified exception analysis.

    Args:
        snapshot: Shared immutable module AST snapshot for both handler rules.

    Returns:
        The QG215 tuple, without duplicating the QG214 source-bound analysis.
    """
    return exception_handler_rule_findings(snapshot)[1]
