from __future__ import annotations

import json
from pathlib import Path

from rqgtest import TestCase
from runtime.src.report_facts import current_test_quality_findings
from runtime.src.model import Finding, ScanReport


class DebtQG179TestCase(TestCase):
    """Gate-only QG179 must not be counted as ordinary test debt."""

    def get_enterpoint(self):
        return current_test_quality_findings

    def get_input_data(self):
        ordinary = Finding(code="QG126", severity="warning", confidence="high", path="tests/test_sample.py", line=1, column=1, message="Ordinary test warning.", symbol="ordinary_test_warning")
        gate = Finding(code="QG179", severity="critical", confidence="high", path="tests", line=1, column=1, message="Gate-only quality fingerprint regression finding.", symbol="test.quality_fingerprint_regression")
        report = ScanReport(root=Path.cwd(), files_scanned=0, findings=[], definitions=0, project_name="synthetic-test-debt", test_findings=[ordinary, gate])
        yield ((report,), {})

    def get_output_data(self):
        case = json.loads((Path(__file__).parent / "cases/b31-debt-qg179-test-v1.json").read_text(encoding="utf-8"))
        yield case["expected"]

    def equals(self, expected, actual):
        codes = {finding.code for finding in actual.values()}
        return len(actual) == expected["test_debt_count"] and expected["included_test_code"] in codes and expected["excluded_gate_code"] not in codes
