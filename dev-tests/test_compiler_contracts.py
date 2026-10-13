"""Source-bound R1 findings are not automatically runtime BLOCKERs."""

from __future__ import annotations

import ast
import hashlib

import pytest

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.compiler_contracts import r1_bound_call_findings


def _frozen(
    source: str, callee: str = "deliver"
) -> tuple[RepositoryAnalysisSnapshot, dict]:
    unit = PythonUnit("module.py", source, ast.parse(source))
    snapshot = RepositoryAnalysisSnapshot(None, "DIRECTORY", {"module.py": unit})
    digest = hashlib.sha256(source.encode()).hexdigest()
    return snapshot, {
        "verdict": "STATIC_ARITY_MISMATCH_REVIEW_REQUIRED",
        "runtime_dispatch": "OPEN_WORLD",
        "capability": "COMPILER_UNIQUE_SOURCE_SIGNATURE",
        "path": "module.py",
        "line": 3,
        "column": 1,
        "symbol": f"module.{callee}",
        "caller_sha256": digest,
        "callee_sha256": digest,
        "callee_path": "module.py",
        "witness": "missing a required argument: 'right'",
    }


def test_r1_finding_replays_open_world_call_and_deduplicates() -> None:
    snapshot, evidence = _frozen(
        "def deliver(left, right):\n    return left\ndeliver(1)\n"
    )
    findings = r1_bound_call_findings(snapshot, [evidence, evidence])
    assert len(findings) == 1
    assert findings[0].code == "QG209"
    assert findings[0].severity == "warning"
    assert findings[0].evidence["dispatch"] == "OPEN_WORLD"


@pytest.mark.parametrize(
    "change",
    [
        {"caller_sha256": "stale"},
        {"callee_sha256": "stale"},
        {"runtime_dispatch": "CLOSED_WORLD"},
        {"line": 1},
        {"symbol": "module.unrelated"},
    ],
)
def test_r1_rejects_unproven_compiler_evidence(change: dict) -> None:
    snapshot, evidence = _frozen(
        "def deliver(left, right):\n    return left\ndeliver(1)\n"
    )
    evidence.update(change)
    with pytest.raises(ValueError):
        r1_bound_call_findings(snapshot, [evidence])
