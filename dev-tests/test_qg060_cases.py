"""Cases-driven, independently adjudicated QG060 acceptance and adversarial set."""

import json
from pathlib import Path

import pytest

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner

CASES = json.loads(
    (Path(__file__).parent / "cases" / "qg060.json").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_qg060_case_oracle(case: dict, tmp_path: Path) -> None:
    """Compare a formal case oracle with the real production Scanner output."""
    (tmp_path / "input.py").write_text(case["source"], encoding="utf-8")
    report = RepositoryScanner(
        tmp_path,
        GuardConfig(require_docstrings=False, require_docstring_sections=False),
    ).scan()
    actual = [
        finding.severity for finding in report.findings if finding.code == "QG060"
    ]
    assert actual == case["expected"], case["id"]
    for finding in report.findings:
        if finding.code == "QG060":
            assert finding.evidence["classification"] in {"PROVEN", "REVIEW"}
            if finding.severity == "critical":
                assert finding.evidence["relation"] == "disjoint_builtin"
                assert finding.confidence == "high"
