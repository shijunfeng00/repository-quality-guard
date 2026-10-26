"""Public report contract for touched versus untouched historical debt."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from rqgtest import TestCase

from runtime.src.compact_report import render_compact_report
from runtime.src.model import Finding, QualityBaseline, ScanReport


class DebtScopedHistoryCase(TestCase):
    """Keep changed-file debt accountable and unrelated history inventory-only."""

    def get_enterpoint(self):
        return render_compact_report

    def get_input_data(self):
        fixture_path = Path(__file__).parent / "cases/b31-debt-scoped-history-v1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        snapshot = Path(__file__).parent / "cases" / fixture["git_fixture"]
        with tempfile.TemporaryDirectory(prefix="rqg-debt-scope-") as directory:
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
                project_name="synthetic-debt-scope",
                profile_source="explicit-test-fixture",
                baseline=baseline,
            )
            yield ((report, "HEAD"), {
                "existing": None,
                "debt_mode": fixture["expected"]["debt_mode"],
                "audit_mode": "normal",
            })

    def get_output_data(self):
        fixture = json.loads(
            (Path(__file__).parent / "cases/b31-debt-scoped-history-v1.json").read_text(encoding="utf-8")
        )
        yield fixture["expected"]

    def equals(self, expected, actual):
        touched_marker = "<!-- RQG:AUTO:BEGIN touched_historical_debt -->"
        touched_end = "<!-- RQG:AUTO:END touched_historical_debt -->"
        untouched_marker = "<!-- RQG:AUTO:BEGIN untouched_historical_debt -->"
        untouched_end = "<!-- RQG:AUTO:END untouched_historical_debt -->"
        if any(marker not in actual for marker in (touched_marker, touched_end, untouched_marker, untouched_end)):
            return False
        touched = actual.split(touched_marker, 1)[1].split(touched_end, 1)[0]
        untouched = actual.split(untouched_marker, 1)[1].split(untouched_end, 1)[0]
        return (
            expected["touched_path"] in touched
            and expected["untouched_path"] not in touched
            and expected["untouched_path"] in untouched
            and expected["touched_path"] not in untouched
            and f"- touched historical remaining：{expected['touched_count']}" in actual
            and f"- untouched historical remaining：{expected['untouched_count']}" in actual
            and f"- 债务模式：`{expected['debt_mode']}`" in actual
        )
