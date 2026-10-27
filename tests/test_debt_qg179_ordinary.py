from __future__ import annotations

import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

from rqgtest import TestCase
from runtime.src.compact_report import render_compact_report
from runtime.src.model import Finding, QualityBaseline, ScanReport


class DebtQG179OrdinaryCase(TestCase):
    """Verify the production debt-report contract for b31-debt-qg179-ordinary."""

    def get_enterpoint(self):
        return render_compact_report

    def get_input_data(self):
        case = json.loads((Path(__file__).parent / "cases/debt-qg179-ordinary.json").read_text(encoding="utf-8"))
        base = json.loads((Path(__file__).parent / "cases" / case["input_fixture"]).read_text(encoding="utf-8"))
        snapshot = Path(__file__).parent / "cases" / base["git_fixture"]
        with tempfile.TemporaryDirectory(prefix="rqg-b31-debt-qg179-ordinary-") as directory:
            root = Path(directory)
            with tarfile.open(snapshot, "r:gz") as archive:
                archive.extractall(root)
            subprocess.run(["git", "checkout", "HEAD", "--", base["changed_path"]], cwd=root, check=True, capture_output=True, text=True)
            changed = root / base["changed_path"]
            changed.write_text(changed.read_text(encoding="utf-8") + "\n# intentional debt-contract diff\n", encoding="utf-8")
            findings = [Finding(**row) for row in base["findings"]]
            findings.append(Finding(code="QG179", severity="critical", confidence="high", path=".", line=1, column=1, message="Gate-only quality fingerprint regression finding.", symbol="repository.quality_fingerprint_regression"))
            baseline_severities = {finding.fingerprint: finding.severity for finding in findings[:2]}
            baseline_summary = {"critical": 0, "error": 0, "warning": 2, "info": 0}
            by_rule = {"QG123": {"warning": 1}, "QG124": {"warning": 1}}
            baseline = QualityBaseline(revision="HEAD", files_scanned=2, definitions=0, documented_definitions=0, complete_docstrings=0, docstring_definitions=0, summary=baseline_summary, by_rule=by_rule, by_file={"sample.py": {"warning": 1}, "untouched.py": {"warning": 1}}, finding_severities=baseline_severities)
            report = ScanReport(root=root, files_scanned=2, findings=findings, definitions=0, project_name="synthetic-debt-contract", profile_source="explicit-test-fixture", baseline=baseline)
            yield ((report, "HEAD"), {"existing": None, "debt_mode": "progressive", "audit_mode": "normal"})

    def get_output_data(self):
        case = json.loads((Path(__file__).parent / "cases/debt-qg179-ordinary.json").read_text(encoding="utf-8"))
        yield case["expected"]

    def equals(self, expected, actual):
        touched = actual.split("<!-- RQG:AUTO:BEGIN touched_historical_debt -->", 1)[1].split("<!-- RQG:AUTO:END touched_historical_debt -->", 1)[0]
        untouched = actual.split("<!-- RQG:AUTO:BEGIN untouched_historical_debt -->", 1)[1].split("<!-- RQG:AUTO:END untouched_historical_debt -->", 1)[0]
        return (
            "- 当前普通 C/E/W 总数：2" in actual
            and "- 新增/恶化普通债务：0" in actual
            and "QG123" in touched
            and "QG124" in untouched
            and "QG179" not in touched and "QG179" not in untouched
        )
