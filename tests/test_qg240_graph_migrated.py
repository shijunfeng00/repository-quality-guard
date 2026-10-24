"""Source-bound graph provenance regression with frozen TP/TN data."""

import ast
import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from rqgtest import TestCase
from runtime.src.case_contract_rules import case_contract_findings
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology


class QG240GraphMigratedCase(TestCase):
    """Validate actual R1/R2/R3 source ownership decisions."""

    def get_enterpoint(self):
        """Return the production analyzer used for source-bound graph cases."""
        return case_contract_findings

    def get_input_data(self):
        """Build the graph for each independently specified re-export fixture."""
        rows = json.loads(
            (Path(__file__).parent / "cases/qg240-graph-reviewed-v2.json").read_text(encoding="utf-8")
        )
        for row in rows:
            with TemporaryDirectory(prefix="rqg-qg240-graph-") as temp_root:
                root = Path(temp_root)
                source = (
                    "from rqgtest import TestCase\n"
                    + row["entry_import"]
                    + "\nclass Example(TestCase):\n"
                    + "    def get_enterpoint(self): return "
                    + row["entry_return"]
                    + "\n    def get_input_data(self): return ((1,), {})\n"
                    + "    def get_output_data(self): return 2\n"
                    + "    def equals(self, expected, actual): return expected == actual\n"
                )
                for relative, code in {**row["modules"], "tests/test_case.py": source}.items():
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(code, encoding="utf-8")
                graph = normalized_topology(
                    RepositoryRelationGraph(directory_analysis_snapshot(root, GuardConfig()))
                )
                yield (
                    (Path("tests/test_case.py"), ast.parse(source)),
                    {"repository_root": root, "topology": graph, "test_roots": tuple(row["test_roots"])},
                )

    def get_output_data(self):
        """Load the reviewed expected source-ownership classifications."""
        rows = json.loads(
            (Path(__file__).parent / "cases/qg240-graph-reviewed-v2.json").read_text(encoding="utf-8")
        )
        for row in rows:
            yield ["QG240"] * row["expected"]

    def equals(self, expected, actual):
        """Compare all rule codes and authentic Finding source locations."""
        return (
            Counter(item.code for item in actual) == Counter(expected)
            and all(item.confidence == "high" for item in actual)
            and all(item.source == "test-contract" for item in actual)
            and all(item.path == "tests/test_case.py" for item in actual)
            and all(item.line >= 1 and item.symbol for item in actual)
        )
