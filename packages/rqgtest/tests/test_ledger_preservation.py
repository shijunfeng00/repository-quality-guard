"""Public CLI regressions for preserving observed samples across invalid Cases."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class LedgerPreservation(unittest.TestCase):
    def test_partial_observations_and_worker_death_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "product.py").write_text(
                textwrap.dedent("""
                import os
                def add_one(value):
                    return value + 1
                def crash():
                    os._exit(67)
                def rejected():
                    raise ValueError('rejected')
            """),
                encoding="utf-8",
            )
            (root / "tests" / "test_ledger.py").write_text(
                textwrap.dedent("""
                from rqgtest import TestCase, Outcome, CaseConfig
                from product import add_one, crash, rejected

                class MismatchCase(TestCase):
                    def __init__(self): super().__init__(config=CaseConfig(process='main'))
                    def get_enterpoint(self): return add_one
                    def get_input_data(self):
                        yield ((1,), {})
                        yield ((2,), {})
                    def get_output_data(self):
                        yield 2
                    def equals(self, expected, actual): return expected == actual

                class ExceptionCase(TestCase):
                    def __init__(self): super().__init__(type=Outcome.EXCEPTION, config=CaseConfig(process='main'))
                    def get_enterpoint(self): return rejected
                    def get_input_data(self): return ((), {})
                    def get_output_data(self): return ValueError
                    def equals(self, expected, actual): return type(actual) is expected

                class CrashCase(TestCase):
                    def get_enterpoint(self): return crash
                    def get_input_data(self): return ((), {})
                    def get_output_data(self): return None
                    def equals(self, expected, actual): return False
            """),
                encoding="utf-8",
            )
            report = root / "result.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "rqgtest.run",
                    "--tests-root",
                    "tests",
                    "--report",
                    str(report),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                },
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            result = json.loads(report.read_text(encoding="utf-8"))
            labels = [
                (r["name"].split("::")[-1], r["status"]) for r in result["results"]
            ]
            self.assertIn(("MismatchCase[1]", "PASS"), labels)
            self.assertIn(("MismatchCase", "INVALID"), labels)
            self.assertIn(("ExceptionCase", "PASS"), labels)
            self.assertIn(("CrashCase", "INVALID"), labels)
            self.assertFalse(result["succeeded"])
            self.assertEqual(result["total"], len(result["results"]))
            self.assertEqual(result["passed"], 2)


if __name__ == "__main__":
    unittest.main()
