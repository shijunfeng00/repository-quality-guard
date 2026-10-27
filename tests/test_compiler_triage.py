"""Public diagnostic classification and source-staleness contracts."""

from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase, Outcome
from runtime.src.compiler_provider_audit import triage


class CompilerTriageCurrentCase(TestCase):
    """Classify source-authenticated Pyright observations by capability group."""

    def get_enterpoint(self):
        """Return the real public diagnostic triage function."""
        return triage

    def get_input_data(self):
        """Construct the source-bound frozen report for the current source."""
        import atexit
        import hashlib
        import shutil
        import tempfile
        from pathlib import Path

        for row in next(
            iter(JSONDataLoader("tests/cases/compiler-triage-public.json"))
        )["cases"]:
            _case_arg_1_stale = False
            root = Path(tempfile.mkdtemp(prefix="rqg-compiler-triage-v02-"))
            atexit.register(shutil.rmtree, root, ignore_errors=True)
            source = root / "source.py"
            source.write_text(
                "import missing_dependency\nvalue = missing_dependency.convert(1)\ntask = lambda: value\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            report = {
                "schema": "rqg.compiler_observation.v1",
                "provider": "pyright",
                "version": "fixture",
                "source_inventory_sha256": "fixture",
                "observations": [
                    {
                        "source_path": "source.py",
                        "source_sha256": digest,
                        "line": line,
                        "native_rule": rule,
                        "native_severity": "error",
                        "message": rule,
                    }
                    for line, rule in (
                        (1, "reportMissingImports"),
                        (2, "reportCallIssue"),
                        (3, "reportPossiblyUnboundVariable"),
                    )
                ],
            }
            if _case_arg_1_stale:
                source.write_text("pass\n", encoding="utf-8")
            _case_value_1 = ((report, root), {})
            yield _case_value_1

    def get_output_data(self):
        """Use the authored classification and grouped-counter predictions."""
        for row in next(
            iter(JSONDataLoader("tests/cases/compiler-triage-public.json"))
        )["cases"]:
            yield row["expected"]

    def equals(self, expected, actual):
        """Compare group totals and source-fact classifications without proxies."""
        observed = {
            "total": actual["total"],
            "groups": actual["groups"],
            "context_line": actual["rows"][2]["context"][-1]["line"],
            "all_unverified_source": all(
                (
                    record["status"] == "UNVERIFIED_SOURCE_CANDIDATE"
                    for record in actual["rows"]
                )
            ),
        }
        return observed == expected


class CompilerTriageStaleCase(TestCase):
    """Require rejected diagnostics when the original source witness changes."""

    def __init__(self):
        """Declare an actual raised-exception contract."""
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        """Invoke the real triage owner without a test wrapper."""
        return triage

    def get_input_data(self):
        """Supply a generated report with a deliberately stale on-disk source."""
        import atexit
        import hashlib
        import shutil
        import tempfile
        from pathlib import Path

        for row in next(
            iter(JSONDataLoader("tests/cases/compiler-triage-public.json"))
        )["cases"]:
            _case_arg_2_stale = True
            root = Path(tempfile.mkdtemp(prefix="rqg-compiler-triage-v02-"))
            atexit.register(shutil.rmtree, root, ignore_errors=True)
            source = root / "source.py"
            source.write_text(
                "import missing_dependency\nvalue = missing_dependency.convert(1)\ntask = lambda: value\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            report = {
                "schema": "rqg.compiler_observation.v1",
                "provider": "pyright",
                "version": "fixture",
                "source_inventory_sha256": "fixture",
                "observations": [
                    {
                        "source_path": "source.py",
                        "source_sha256": digest,
                        "line": line,
                        "native_rule": rule,
                        "native_severity": "error",
                        "message": rule,
                    }
                    for line, rule in (
                        (1, "reportMissingImports"),
                        (2, "reportCallIssue"),
                        (3, "reportPossiblyUnboundVariable"),
                    )
                ],
            }
            if _case_arg_2_stale:
                source.write_text("pass\n", encoding="utf-8")
            _case_value_2 = ((report, root), {})
            yield _case_value_2

    def get_output_data(self):
        """Expect a ValueError containing the source-integrity failure."""
        for row in next(
            iter(JSONDataLoader("tests/cases/compiler-triage-public.json"))
        )["cases"]:
            yield (ValueError, "stale compiler diagnostic")

    def equals(self, expected, actual):
        """Check both exception class and required stale-diagnostic text."""
        error_type, message = expected
        return type(actual) is error_type and message in str(actual)
