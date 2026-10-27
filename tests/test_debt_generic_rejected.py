"""Reject generic touched-debt deferrals through the public report validator."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from rqgtest import TestCase

from runtime.src.compact_report import render_compact_report, validate_compact_report
from runtime.src.model import Finding, QualityBaseline, ScanReport


class DebtGenericRejectedCase(TestCase):
    """Require concrete, verifiable reasons for touched historical debt deferrals."""

    def get_enterpoint(self):
        return validate_compact_report

    def get_input_data(self):
        case_path = Path(__file__).parent / "cases/debt-generic-rejected.json"
        case = json.loads(case_path.read_text(encoding="utf-8"))
        fixture_path = Path(__file__).parent / "cases" / case["input_fixture"]
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        snapshot = Path(__file__).parent / "cases" / fixture["git_fixture"]
        with tempfile.TemporaryDirectory(prefix="rqg-debt-generic-") as directory:
            root = Path(directory)
            with tarfile.open(snapshot, "r:gz") as archive:
                archive.extractall(root)
            subprocess.run(
                ["git", "checkout", "HEAD", "--", fixture["changed_path"]],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            changed = root / fixture["changed_path"]
            changed.write_text(
                changed.read_text(encoding="utf-8") + "\n# intentional diff-scope fixture\n",
                encoding="utf-8",
            )
            findings = [Finding(**row) for row in fixture["findings"]]
            baseline = QualityBaseline(
                revision="HEAD",
                files_scanned=2,
                definitions=0,
                documented_definitions=0,
                complete_docstrings=0,
                docstring_definitions=0,
                summary=fixture["baseline_summary"],
                by_rule={"QG123": {"warning": 1}, "QG124": {"warning": 1}},
                by_file={"sample.py": {"warning": 1}, "untouched.py": {"warning": 1}},
                finding_severities={finding.fingerprint: finding.severity for finding in findings},
            )
            report = ScanReport(
                root=root,
                files_scanned=2,
                findings=findings,
                definitions=0,
                project_name="synthetic-debt-generic",
                profile_source="explicit-test-fixture",
                baseline=baseline,
            )
            row = case["prior_debt_row"]
            existing = (
                "| DEBT | Decision | Concrete reason | Scope impact | Closure condition |\n"
                "|---|---|---|---|---|\n"
                f"| {row['debt_id']} | {row['decision']} | {row['reason']} | "
                f"{row['impact']} | {row['closure_condition']} |\n"
            )
            rendered = render_compact_report(
                report,
                "HEAD",
                existing=existing,
                debt_mode="progressive",
                audit_mode="normal",
            )
            yield ((rendered, report, "HEAD"), {"debt_mode": "progressive", "audit_mode": "normal"})

    def get_output_data(self):
        case = json.loads(
            (Path(__file__).parent / "cases/debt-generic-rejected.json").read_text(encoding="utf-8")
        )
        yield case["expected"]

    def equals(self, expected, actual):
        return (
            any(expected["required_issue"] in issue for issue in actual.issues)
            and any(expected["required_reason_issue"] in issue for issue in actual.issues)
        )
