"""Real shared-R1/R2/R3 topology witnesses for production target isolation."""
from __future__ import annotations


import ast
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from rqgtest import TestCase
from runtime.src.case_contract_rules import case_contract_findings
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology


class GraphIndirectInvocationCase(TestCase):
    """Distinguish a proven indirect invocation from an unused helper."""

    def get_enterpoint(self):
        """Return the real Case rule analyzer for topology witnesses."""
        return case_contract_findings

    def get_input_data(self):
        """Construct the actual call graph for a direct and an unused helper."""
        for method_body in (
            "return helper(2)",
            "return 3",
        ):
            with TemporaryDirectory(prefix="qg245-graph-fixture-") as dirname:
                root = Path(dirname)
                production = (
                    "def target(value):\n"
                    "    return value + 1\n\n"
                    "def helper(value):\n"
                    "    return target(value)\n"
                )
                test_source = (
                    "from rqgtest import TestCase\n"
                    "from prod import target, helper\n\n"
                    "class ObserveCase(TestCase):\n"
                    "    def get_enterpoint(self): return target\n"
                    "    def get_input_data(self): return ((2,), {})\n"
                    "    def get_output_data(self): " + method_body + "\n"
                    "    def equals(self, expected, actual): return expected == actual\n"
                )
                (root / "prod.py").write_text(production, encoding="utf-8")
                (root / "tests").mkdir()
                (root / "tests/test_contract.py").write_text(test_source, encoding="utf-8")
                graph = normalized_topology(
                    RepositoryRelationGraph(
                        directory_analysis_snapshot(root, GuardConfig())
                    )
                )
                yield (
                    (Path("tests/test_contract.py"), ast.parse(test_source)),
                    {"repository_root": root, "topology": graph},
                )

    def get_output_data(self):
        """Keep the required positive and negative graph results independent."""
        yield {"QG245": 1}
        yield {}

    def equals(self, expected, actual):
        """Require exact QG245 multiplicity and source-bound severity evidence."""
        return (
            Counter(finding.code for finding in actual) == Counter(expected)
            and all(finding.confidence == "high" for finding in actual)
            and all(finding.path == "tests/test_contract.py" for finding in actual)
        )
