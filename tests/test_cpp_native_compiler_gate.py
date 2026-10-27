"""Native compiler execution failure must not become a QG quality Finding."""
from __future__ import annotations

from pathlib import Path

from rqgtest import TestCase

from runtime.src.gate_status import code_status
from runtime.src.model import Finding, ScanReport


class NativeCompilerGateCase(TestCase):
    """Native compiler failures remain diagnostics, never static quality blockers."""

    def get_enterpoint(self):
        """Use the production public gate decision."""
        return code_status

    def get_input_data(self):
        """Include native failures, coverage gaps and a real static blocker."""
        for mode in ("failed", "ok", "QG208", "both", "static_blocker", "baseline_error"):
            report = ScanReport(Path('/native-fixture'), 1, [], 0)
            report.multilang_summary = {
                "cpp_native_failures": [{
                    "path": "src/sample.cpp",
                    "compiler_family": "gcc",
                    "command": ("g++", "-fsyntax-only", "src/sample.cpp"),
                    "returncode": 1,
                    "stderr": "header missing",
                }] if mode in {"failed", "both"} else [],
            }
            if mode in {"QG208", "both"}:
                report.findings.append(Finding(
                    code="QG208", severity="info", confidence="high",
                    path="src/sample.cpp", line=1, column=1,
                    message="Native syntax not measured",
                ))
            if mode == "static_blocker":
                report.findings.append(Finding(
                    code="QG149", severity="critical", confidence="high",
                    path="src/unsafe.py", line=1, column=1,
                    message="Proven quality blocker", evidence={"absolute_blocker": True},
                ))
            if mode == "baseline_error":
                report.baseline_error = "Missing required quality baseline"
            yield ((report,), {})

    def get_output_data(self):
        """Require independent quality verdicts, not build success verdicts."""
        for status in ("ACCEPT", "ACCEPT", "ACCEPT", "ACCEPT", "REJECT", "REJECT"):
            yield status

    def equals(self, expected, actual):
        """Compare the production result with the authored contract."""
        return expected == actual
