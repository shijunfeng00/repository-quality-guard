"""QG246 positive/negative samples tested against the actual production analyzer."""

import ast
import json
from collections import Counter
from pathlib import Path

from rqgtest import TestCase
from runtime.src.case_contract_rules import case_contract_findings


class QG246RunnerContractCase(TestCase):
    """Reject self-managed test execution while permitting official CLI dispatch."""
    def get_enterpoint(self):
        """Use the production analyzer to classify TestUnit ownership."""
        return case_contract_findings

    def get_input_data(self):
        """Parse fixed positive and negative TestUnit-origin examples."""
        for row in json.loads((Path(__file__).parent / "cases/qg246-contract-cases.json").read_text()):
            yield ((Path("tests/test_runner_fixture.py"), ast.parse(row["source"])), {})

    def get_output_data(self):
        """Read independently reviewed full Finding codes and locations."""
        for row in json.loads((Path(__file__).parent / "cases/qg246-contract-cases.json").read_text()):
            yield {"codes": row["expected_codes"], "locations": row["expected_locations"]}

    def equals(self, expected, actual):
        """Check exact rule multiplicities and the attached certified locations."""
        seen = Counter(finding.code for finding in actual)
        return (seen == Counter(expected["codes"])
                and (expected["locations"] is None or sorted((f.code, f.line, f.symbol) for f in actual) == sorted(tuple(x) for x in expected["locations"]))
                and all(finding.confidence == "high" for finding in actual)
                and all(finding.path == "tests/test_runner_fixture.py" for finding in actual))
