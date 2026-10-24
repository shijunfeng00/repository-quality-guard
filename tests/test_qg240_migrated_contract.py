"""QG240 frozen lineage tests migrated from the pre-rqgtest framework."""

import ast
import json
from collections import Counter
from pathlib import Path

from rqgtest import TestCase
from runtime.src.case_contract_rules import case_contract_findings


class CaseContractMigratedLegacyCase(TestCase):
    """Run independent preserved TP/TN contracts through the production analyzer."""

    def get_enterpoint(self):
        """Return the production QG detector shared by both legacy rule cohorts."""
        return case_contract_findings

    def get_input_data(self):
        """Provide the original independent QG240 and QG241–244 source fixtures."""
        for name in ("qg240-reviewed-legacy-v2.json", "qg241-244-reviewed-legacy-v2.json"):
            rows = json.loads((Path(__file__).parent / "cases" / name).read_text(encoding="utf-8"))
            for row in rows:
                yield (
                    (Path(row["path"]), ast.parse(row["source"])),
                    {"repository_root": Path(__file__).resolve().parents[1]},
                )

    def get_output_data(self):
        """Load the preserved TP/TN rule codes independently of scanner output."""
        for name in ("qg240-reviewed-legacy-v2.json", "qg241-244-reviewed-legacy-v2.json"):
            rows = json.loads((Path(__file__).parent / "cases" / name).read_text(encoding="utf-8"))
            for row in rows:
                yield {"codes": row["expected_codes"], "path": row["path"], "source": row["source"]}

    def equals(self, expected, actual):
        """Require the complete Finding multiset, origin and evidence fields."""
        return (
            Counter(finding.code for finding in actual) == Counter(expected["codes"])
            and all(finding.path == expected["path"] for finding in actual)
            and all(finding.confidence == "high" for finding in actual)
            and all(finding.source == "test-contract" for finding in actual)
            and all(1 <= finding.line <= max(1, len(expected["source"].splitlines())) for finding in actual)
            and all(finding.symbol for finding in actual)
            and all(finding.evidence["absolute_blocker"] is True for finding in actual)
        )
