"""Behavioral regression for introduced quality findings on a Git baseline."""

from __future__ import annotations

from pathlib import Path

def test_real_new_warning_triggers_delta_gate(tmp_path: Path) -> None:
    """An introduced high-confidence warning must trigger the published delta gate."""
    from runtime.src.cli import _append_quality_count_regression_finding
    from runtime.src.gate_status import code_status
    from runtime.src.model import Finding, QualityBaseline, ScanReport

    bad = Finding(
        code="QG007",
        severity="warning",
        confidence="high",
        path="service.py",
        line=2,
        column=1,
        message="Unrelated, truly introduced warning",
    )
    prior = QualityBaseline(
        revision="original-git-base",
        files_scanned=1,
        definitions=1,
        documented_definitions=0,
        complete_docstrings=0,
        docstring_definitions=1,
        summary={"critical": 0, "error": 0, "warning": 0, "info": 0},
        by_rule={},
        by_file={},
        finding_severities={},
    )
    report = ScanReport(
        root=tmp_path,
        files_scanned=1,
        findings=[bad],
        definitions=1,
        baseline=prior,
    )
    _append_quality_count_regression_finding(report)
    assert any(f.code == "QG179" for f in report.findings)
    assert code_status(report) == "REJECT"
