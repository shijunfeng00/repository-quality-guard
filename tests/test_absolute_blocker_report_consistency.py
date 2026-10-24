"""The published blocker table must agree with the production gate predicate."""
from __future__ import annotations

from pathlib import Path

from rqgtest import TestCase

from runtime.src.compact_report import render_compact_report
from runtime.src.model import Finding, ScanReport


class AbsoluteBlockerReportCase(TestCase):
    """Compare authored production/test boundary cases against report text."""

    def get_enterpoint(self):
        """Return the real report renderer, not a synthetic gate adapter."""
        return render_compact_report

    def get_input_data(self):
        """Provide production, test-only and explicit-evidence blocker cases."""
        scenarios = (
            ("QG149", "tests/test_example.py", {}, False),
            ("QG149", "runtime/src/example.py", {}, False),
            ("QG130", "runtime/src/example.py", {"absolute_blocker": True}, False),
            ("QG245", "tests/test_example.py", {"absolute_blocker": True}, True),
            ("QG245", "tests/test_example.py", {"absolute_blocker": False}, True),
        )
        for code, path, evidence, is_contract in scenarios:
            finding = Finding(
                code=code,
                severity="critical",
                confidence="high",
                path=path,
                line=7,
                column=1,
                message="Independent intentionally authored blocker evidence",
                symbol="fixture_target",
                evidence=evidence,
            )
            report = ScanReport(root=Path("/report-fixture"), files_scanned=1,
                                findings=[] if is_contract else [finding], definitions=0,
                                test_findings=[finding] if is_contract else [])
            yield ((report, ""), {})

    def get_output_data(self):
        """Expected rows and gate status originate from the behavior contract."""
        yield ("REVIEW_REQUIRED", "none", "QG149")
        yield ("REJECT", "QG149", "runtime/src/example.py:7")
        yield ("REJECT", "QG130", "runtime/src/example.py:7")
        yield ("REJECT", "QG245", "tests/test_example.py:7")
        yield ("ACCEPT", "none", "QG245")

    def equals(self, expected, actual):
        """Match the effective gate and its delimited blocker table together."""
        status, expected_code, expected_path = expected
        header = "<!-- RQG:AUTO:BEGIN current_absolute_blockers -->"
        footer = "<!-- RQG:AUTO:END current_absolute_blockers -->"
        if header not in actual or footer not in actual:
            return False
        table = actual.split(header, 1)[1].split(footer, 1)[0]
        if f"tool_status: {status}" not in actual:
            return False
        if expected_code == "none":
            return "无当前绝对阻断" in table and f"`{expected_path}`" not in table
        return f"`{expected_code}`" in table and f"`{expected_path}`" in table
