from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.call_chain_rules import single_use_chain_findings
from runtime.src.config import GuardConfig
from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology


class TestSingleUseChainTopology(unittest.TestCase):
    def _findings(self, source: str):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "sample.py").write_text(source, encoding="utf-8")
        config = GuardConfig(
            require_docstrings=False,
            require_docstring_sections=False,
            include_tests=True,
        )
        snapshot = directory_analysis_snapshot(root, config)
        topology = normalized_topology(
            RepositoryRelationGraph(snapshot),
            StaticPythonDependencyResolver.from_environment(),
        )
        return single_use_chain_findings(root, config, topology)

    def test_three_ephemeral_helpers_form_qg168_chain(self) -> None:
        findings = self._findings(
            "def public(value):\n"
            "    return _first(value)\n\n"
            "def _first(value):\n"
            "    return _second(value)\n\n"
            "def _second(value):\n"
            "    return _third(value)\n\n"
            "def _third(value):\n"
            "    return value + 1\n"
        )
        qg168 = [item for item in findings if item.code == "QG168"]
        self.assertEqual(len(qg168), 1)
        self.assertEqual(qg168[0].evidence["helper_count"], 3)
        self.assertEqual(qg168[0].evidence["edge_kind"], "direct_call")
        self.assertEqual(qg168[0].evidence["topology_resolution"], "normalized")

    def test_callable_reference_breaks_helper_chain(self) -> None:
        findings = self._findings(
            "def consume(fn):\n"
            "    return fn\n\n"
            "def register():\n"
            "    return consume(_second)\n\n"
            "def public(value):\n"
            "    return _first(value)\n\n"
            "def _first(value):\n"
            "    return _second(value)\n\n"
            "def _second(value):\n"
            "    return _third(value)\n\n"
            "def _third(value):\n"
            "    return value + 1\n"
        )
        self.assertFalse(any(item.code == "QG168" for item in findings))

    def test_shared_private_primitive_breaks_helper_chain(self) -> None:
        findings = self._findings(
            "def public(value):\n"
            "    return _first(value)\n\n"
            "def alternate(value):\n"
            "    return _second(value)\n\n"
            "def _first(value):\n"
            "    return _second(value)\n\n"
            "def _second(value):\n"
            "    return _third(value)\n\n"
            "def _third(value):\n"
            "    return value + 1\n"
        )
        self.assertFalse(any(item.code == "QG168" for item in findings))

    def test_framework_marker_breaks_helper_chain(self) -> None:
        findings = self._findings(
            "def callback(fn):\n"
            "    return fn\n\n"
            "def public(value):\n"
            "    return _first(value)\n\n"
            "def _first(value):\n"
            "    return _second(value)\n\n"
            "@callback\n"
            "def _second(value):\n"
            "    return _third(value)\n\n"
            "def _third(value):\n"
            "    return value + 1\n"
        )
        self.assertFalse(any(item.code == "QG168" for item in findings))


if __name__ == "__main__":
    unittest.main()
