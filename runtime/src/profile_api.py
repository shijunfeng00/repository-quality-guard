"""Stable public extension API for Repository Quality Guard Profiles."""

from .model import Finding

from .project_profiles import (
    CallableContract,
    FrozenSSEProtocolContract,
    HeaderContract,
    ProfileSelection,
    ProjectProfile,
    QualityGuardProfile,
    QualityRule,
    ReportExtension,
    RulePack,
    RouteContract,
    RuleContext,
    SSEProtocolContract,
    SearchStrategy,
    StableMappingContract,
)

__all__ = [
    "CallableContract",
    "Finding",
    "FrozenSSEProtocolContract",
    "HeaderContract",
    "ProfileSelection",
    "ProjectProfile",
    "QualityGuardProfile",
    "QualityRule",
    "ReportExtension",
    "RulePack",
    "RouteContract",
    "RuleContext",
    "SSEProtocolContract",
    "SearchStrategy",
    "StableMappingContract",
]
