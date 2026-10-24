"""Source-qualified local imports and lexical shadows for QG245."""

import ast
import json
from collections import Counter
from pathlib import Path

from rqgtest import TestCase
from runtime.src.case_contract_rules import case_contract_findings


class QG245SourceScopeCase(TestCase):
    """Distinguish real production identity from same-spelled local names."""

    def get_enterpoint(self):
        """Use the real production QG analyzer as the test entrypoint."""
        return case_contract_findings

    def get_input_data(self):
        """Provide fixed ASTs whose source bindings are independently known."""
        rows = json.loads((Path(__file__).parent / "cases/qg245-source-scope.json").read_text(encoding="utf-8"))
        for row in rows:
            yield ((Path("tests/test_source_fixture.py"), ast.parse(row["source"])), {})

    def get_output_data(self):
        """Read separately reviewed, exact QG Finding identities and sites."""
        rows = json.loads((Path(__file__).parent / "cases/qg245-source-scope.json").read_text(encoding="utf-8"))
        for row in rows:
            yield row

    def equals(self, expected, actual):
        """Require exact Finding multiplicity and locations with no extras."""
        return (
            Counter(item.code for item in actual) == Counter(expected["expected_codes"])
            and sorted((item.code, item.line, item.symbol) for item in actual)
            == sorted(tuple(item) for item in expected["expected_locations"])
            and all(item.source == "test-contract" for item in actual)
            and all(item.confidence == "high" for item in actual)
            and all(item.path == "tests/test_source_fixture.py" for item in actual)
        )
