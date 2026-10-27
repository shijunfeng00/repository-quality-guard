from __future__ import annotations

import json
import tarfile
import tempfile
from pathlib import Path

from rqgtest import TestCase
from runtime.src.compact_report import render_compact_report
from runtime.src.model import Finding, ScanReport


class DebtNoBaselineCase(TestCase):
    """Current-source audit reports selected-scope debt without inventing a Git baseline."""

    def get_enterpoint(self):
        return render_compact_report

    def get_input_data(self):
        case = json.loads((Path(__file__).parent / "cases/debt-no-baseline.json").read_text(encoding="utf-8"))
        base = json.loads((Path(__file__).parent / "cases" / case["input_fixture"]).read_text(encoding="utf-8"))
        snapshot = Path(__file__).parent / "cases" / base["git_fixture"]
        with tempfile.TemporaryDirectory(prefix="rqg-debt-no-baseline-") as directory:
            root = Path(directory)
            with tarfile.open(snapshot, "r:gz") as archive:
                archive.extractall(root)
            findings = [Finding(**row) for row in base["findings"]]
            report = ScanReport(root=root, files_scanned=2, findings=findings, definitions=0, project_name="synthetic-no-baseline", profile_source="explicit-test-fixture", baseline=None)
            yield ((report, "N/A"), {"existing": None, "debt_mode": "progressive", "audit_mode": "ignore-diff"})

    def get_output_data(self):
        case = json.loads((Path(__file__).parent / "cases/debt-no-baseline.json").read_text(encoding="utf-8"))
        yield case["expected"]

    def equals(self, expected, actual):
        inventory = actual.split("<!-- RQG:AUTO:BEGIN untouched_historical_debt -->", 1)[1].split("<!-- RQG:AUTO:END untouched_historical_debt -->", 1)[0]
        return (
            "- no-baseline selected-scope remaining：2" in actual
            and "- 当前普通 C/E/W 总数：2" in actual
            and "Accepted design baseline：N/A" in actual
            and "本轮 Delta：N/A" in actual
            and "audit_mode: ignore-diff" in actual
            and "sample.py" in inventory and "untouched.py" in inventory
        )
