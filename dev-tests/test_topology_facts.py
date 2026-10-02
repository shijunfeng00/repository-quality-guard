from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner
from runtime.src.topology_facts import RepositoryTopology, UsageKind, Visibility


_SAMPLE = """\
class Worker:
    def _helper(self):
        return 1

    def run(self):
        return self._helper()


def callback(value):
    return value


def consume(fn):
    return fn(1)


def main():
    return consume(callback)
"""


class TestNormalizedTopologyFacts(unittest.TestCase):
    def _repository(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "sample.py").write_text(_SAMPLE, encoding="utf-8")
        return temporary, root

    @staticmethod
    def _config() -> GuardConfig:
        return GuardConfig(
            require_docstrings=False,
            require_docstring_sections=False,
            include_tests=True,
        )

    def test_scanner_exposes_normalized_python_usage_topology(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)

        scanner = RepositoryScanner(root, self._config())
        scanner.scan()

        topology = scanner.topology
        self.assertIsNotNone(topology)
        assert topology is not None
        symbols = {symbol.symbol_id: symbol for symbol in topology.symbols}
        self.assertEqual(
            symbols["sample.Worker._helper"].visibility, Visibility.INTERNAL
        )
        self.assertEqual(symbols["sample.Worker._helper"].owner_id, "sample.Worker")

        helper_edges = topology.incoming(
            "sample.Worker._helper", (UsageKind.DIRECT_CALL,)
        )
        self.assertEqual(
            [(edge.source_id, edge.target_id) for edge in helper_edges],
            [("sample.Worker.run", "sample.Worker._helper")],
        )
        callback_edges = topology.incoming(
            "sample.callback", (UsageKind.CALLABLE_REFERENCE,)
        )
        self.assertEqual(
            [(edge.source_id, edge.target_id) for edge in callback_edges],
            [("sample.main", "sample.callback")],
        )

    def test_topology_result_is_not_a_policy_input_in_phase_one(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)

        baseline = RepositoryScanner(root, self._config()).scan()
        with patch(
            "runtime.src.scanner.python_topology",
            return_value=RepositoryTopology(symbols=(), owners=(), edges=()),
        ):
            comparison = RepositoryScanner(root, self._config()).scan()

        self.assertEqual(
            [finding.to_dict() for finding in baseline.findings],
            [finding.to_dict() for finding in comparison.findings],
        )
        self.assertEqual(baseline.definitions, comparison.definitions)
        self.assertEqual(
            baseline.complete_docstrings,
            comparison.complete_docstrings,
        )


if __name__ == "__main__":
    unittest.main()
