"""Source-bound QG240/241/245 regression against real production rules."""
from __future__ import annotations


import ast
import json
from pathlib import Path

from rqgtest import TestCase
from runtime.src.case_contract_rules import case_contract_findings


class QG245ProductionContractCase(TestCase):
    """Validate positive and negative witnesses with independently frozen oracles."""

    def get_enterpoint(self):
        """Return the real contract analyzer, never a wrapper."""
        return case_contract_findings

    def get_input_data(self):
        """Parse fixed code snippets into real ASTs for the analyzer."""
        rows = json.loads(
            (Path(__file__).parent / "cases" / "qg240-245-case-contracts.json").read_text(
                encoding="utf-8"
            )
        )
        for row in rows:
            yield (
                (Path("tests/test_qg245_fixture.py"), ast.parse(row["source"])),
                {},
            )

    def get_output_data(self):
        """Read frozen, independently reviewed complete expected findings."""
        rows = json.loads(
            (Path(__file__).parent / "cases" / "qg240-245-case-contracts.json").read_text(
                encoding="utf-8"
            )
        )
        for row in rows:
            yield {"codes": row["expected_codes"], "source": row["source"], "locations": row["expected_locations"]}

    def equals(self, expected, actual):
        """Compare verified codes and multiplicities, including expected absence."""
        from collections import Counter

        observed = Counter(finding.code for finding in actual)
        locations = sorted((f.code, f.line, f.symbol) for f in actual)
        allowed = {"QG240", "QG241", "QG242", "QG243", "QG244", "QG245", "QG246"}
        return (
            observed == Counter(expected["codes"])
            and (expected["locations"] is None or locations == sorted(tuple(item) for item in expected["locations"]))
            and all(finding.code in allowed for finding in actual)
            and all(finding.confidence == "high" for finding in actual)
            and all(finding.source == "test-contract" for finding in actual)
            and all(finding.path == "tests/test_qg245_fixture.py" for finding in actual)
            and all(1 <= finding.line <= len(expected["source"].splitlines()) for finding in actual)
            and all(finding.symbol for finding in actual)
            and all(finding.evidence["absolute_blocker"] is True for finding in actual)
        )
