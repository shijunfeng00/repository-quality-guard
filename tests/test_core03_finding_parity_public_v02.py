"""Public ordered Finding-ledger equivalence and fail-closed input contracts."""
from __future__ import annotations

from rqgtest import Outcome, TestCase
from tools.compare_finding_ledgers import compare_finding_ledgers
import json
from pathlib import Path



class Core03FindingParityPublicCase(TestCase):
    """Require ordered full-field equivalence, except the explicitly retired baseline-only rule."""

    def get_enterpoint(self):
        """Return the actual public ledger comparator without a wrapper."""
        return compare_finding_ledgers

    def get_input_data(self):
        """Supply all valid frozen-input pairs as direct positional inputs."""
        for row in json.loads((Path(__file__).resolve().parent / "cases/core03-finding-parity-v1.json").read_text(encoding="utf-8"))["cases"]:
            if "equivalent" in row["expected"]:
                yield ((row["baseline"], row["current"]), {})

    def get_output_data(self):
        """Read originally authored equivalence and mismatch-count expectations."""
        for row in json.loads((Path(__file__).resolve().parent / "cases/core03-finding-parity-v1.json").read_text(encoding="utf-8"))["cases"]:
            if "equivalent" in row["expected"]:
                yield row["expected"]

    def equals(self, expected, actual):
        """Compare exact equivalence, retired-baseline removal and ordered mismatch count."""
        return (
            actual["equivalent"] == expected["equivalent"]
            and actual["removed_retired_baseline_findings"] == expected["removed_retired_baseline_findings"]
            and len(actual["differences"]) == expected["differences"]
        )


class Core03FindingInvalidLedgerPublicCase(TestCase):
    """Demand a precise validation failure for every malformed frozen input."""

    def __init__(self):
        """Select the frozen framework's genuine expected-exception execution."""
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        """Return the real public production comparator without indirection."""
        return compare_finding_ledgers

    def get_input_data(self):
        """Supply malformed or forged inputs without executing the comparator."""
        for row in json.loads((Path(__file__).resolve().parent / "cases/core03-finding-parity-v1.json").read_text(encoding="utf-8"))["cases"]:
            if "exception" in row["expected"]:
                yield ((row["baseline"], row["current"]), {})

    def get_output_data(self):
        """Require the original independently authored ValueError contract."""
        for row in json.loads((Path(__file__).resolve().parent / "cases/core03-finding-parity-v1.json").read_text(encoding="utf-8"))["cases"]:
            if "exception" in row["expected"]:
                yield ValueError

    def equals(self, expected, actual):
        """Reject unrelated exceptions and silently accepted forged ledgers."""
        return type(actual) is expected
