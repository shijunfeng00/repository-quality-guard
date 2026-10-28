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
                (root / "tests/test_contract.py").write_text(
                    test_source, encoding="utf-8"
                )
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


class GraphConstructorIsolationCase(TestCase):
    """Distinguish constructor inputs from invocation of a declared instance method."""

    def get_enterpoint(self):
        """Exercise the real source-and-topology test contract analyzer."""
        return case_contract_findings

    def get_input_data(self):
        """Supply independent explicit-init and generated-init positive/negative fixtures."""
        constructors = (
            "class Calculator:\n"
            "    def __init__(self, c): self.c = c\n"
            "    def calculate(self, a, b): return (a+b)*self.c\n",
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Calculator:\n"
            "    c: int\n"
            "    def calculate(self, a, b): return (a+b)*self.c\n",
        )
        behaviors = (
            ("return ((Calculator(10), 2, 3), {})", "return 50"),
            ("return ((Calculator.calculate(Calculator(10), 2, 3),), {})", "return 50"),
            (
                "return ((Calculator(10), 2, 3), {})",
                "return Calculator.calculate(Calculator(10), 2, 3)",
            ),
            ("return ((Calculator(10), 2, 3), {})", "return 999"),
        )
        for production in constructors:
            for input_body, output_body in behaviors:
                with TemporaryDirectory(prefix="qg245-constructor-") as dirname:
                    root = Path(dirname)
                    (root / "calculator.py").write_text(production, encoding="utf-8")
                    (root / "tests").mkdir()
                    source = (
                        "from rqgtest import TestCase\n"
                        "from calculator import Calculator\n\n"
                        "class CalculatorMethodCase(TestCase):\n"
                        "    def get_enterpoint(self): return Calculator.calculate\n"
                        f"    def get_input_data(self): {input_body}\n"
                        f"    def get_output_data(self): {output_body}\n"
                        "    def equals(self, expected, actual): return expected == actual\n"
                    )
                    (root / "tests/test_calculator.py").write_text(
                        source, encoding="utf-8"
                    )
                    graph = normalized_topology(
                        RepositoryRelationGraph(
                            directory_analysis_snapshot(root, GuardConfig())
                        )
                    )
                    yield (
                        (Path("tests/test_calculator.py"), ast.parse(source)),
                        {"repository_root": root, "topology": graph},
                    )

    def get_output_data(self):
        """Require absence on constructor-only and deliberately wrong Oracle values."""
        for _ in range(2):
            yield {}
            yield {"QG245": 1}
            yield {"QG245": 1}
            yield {}

    def equals(self, expected, actual):
        """Compare complete QG240-QG246 findings against frozen positive/negative oracles."""
        return (
            Counter(finding.code for finding in actual) == Counter(expected)
            and all(finding.path == "tests/test_calculator.py" for finding in actual)
            and all(finding.confidence == "high" for finding in actual)
        )


class GraphPrematureEntrypointConstructionCase(TestCase):
    """QG240 blocks evaluating a constructor while declaring the entrypoint."""

    def get_enterpoint(self):
        return case_contract_findings

    def get_input_data(self):
        for returned in (
            "Calculator(10).calculate",
            "Calculator.__new__(Calculator).calculate",
        ):
            with TemporaryDirectory(prefix="qg240-entry-constructor-") as dirname:
                root = Path(dirname)
                (root / "calculator.py").write_text(
                    "class Calculator:\n"
                    "    def __init__(self, c): self.c = c\n"
                    "    def calculate(self, a, b): return (a+b)*self.c\n",
                    encoding="utf-8",
                )
                (root / "tests").mkdir()
                source = (
                    "from rqgtest import TestCase\n"
                    "from calculator import Calculator\n\n"
                    "class CalculatorCase(TestCase):\n"
                    f"    def get_enterpoint(self): return {returned}\n"
                    "    def get_input_data(self): return ((Calculator(10), 2, 3),)\n"
                    "    def get_output_data(self): return 50\n"
                    "    def equals(self, expected, actual): return expected == actual\n"
                )
                (root / "tests/test_calculator.py").write_text(source, encoding="utf-8")
                topology = normalized_topology(
                    RepositoryRelationGraph(
                        directory_analysis_snapshot(root, GuardConfig())
                    )
                )
                yield (
                    (Path("tests/test_calculator.py"), ast.parse(source)),
                    {"repository_root": root, "topology": topology},
                )

    def get_output_data(self):
        yield {"QG240": 1}
        yield {"QG240": 1}

    def equals(self, expected, actual):
        return Counter(finding.code for finding in actual) == Counter(expected)
