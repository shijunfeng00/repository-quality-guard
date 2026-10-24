"""QG214 compatibility entrypoint for independently owned exception rule."""

from __future__ import annotations

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .exception_handler_rule_objects import exception_handler_rule_findings
from .model import Finding


def silent_exception_fallback_findings(
    snapshot: RepositoryAnalysisSnapshot,
) -> tuple[Finding, ...]:
    """Select QG214 swallowed-exception fallback findings from the shared owner.

    Args:
        snapshot: Frozen parsed module sources used by the unified exception
            handler rule evaluator.

    Returns:
        The QG214 group of Findings; QG215 handler fallthrough remains owned
        by the companion result from the same analysis.
    """
    return exception_handler_rule_findings(snapshot)[0]
