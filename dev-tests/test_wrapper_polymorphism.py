from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.scanner import RepositoryScanner


class TestWrapperPolymorphism(unittest.TestCase):
    def _findings(self, source: str, *, state_types: tuple[str, ...] = ()):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "sample.py").write_text(source, encoding="utf-8")
        config = GuardConfig(
            require_docstrings=False,
            require_docstring_sections=False,
            include_tests=True,
            state_types=state_types,
        )
        snapshot = directory_analysis_snapshot(root, config)
        topology = normalized_topology(RepositoryRelationGraph(snapshot))
        return (
            RepositoryScanner(
                root,
                config,
                analysis_snapshot=snapshot,
                topology=topology,
            )
            .scan()
            .findings
        )

    def _wrapper_symbols(self, source: str) -> set[str]:
        return {item.symbol for item in self._findings(source) if item.code == "QG010"}

    def test_explicit_abstractmethod_is_exempt_but_similar_name_is_not(self) -> None:
        abstract_symbols = self._wrapper_symbols(
            "from abc import ABC, abstractmethod\n"
            "def make_value(): return 1\n"
            "class Base(ABC):\n"
            "    @abstractmethod\n"
            "    def get_value(self):\n"
            "        return make_value()\n"
        )
        lookalike_symbols = self._wrapper_symbols(
            "def not_abstractmethod(fn): return fn\n"
            "def make_value(): return 1\n"
            "class Base:\n"
            "    @not_abstractmethod\n"
            "    def get_value(self):\n"
            "        return make_value()\n"
        )

        self.assertNotIn("sample.Base.get_value", abstract_symbols)
        self.assertIn("sample.Base.get_value", lookalike_symbols)

    def test_dependency_scope_uses_exact_abstractmethod_contract(self) -> None:
        state = "class State:\n    value: int\n    other: int\n"
        explicit = self._findings(
            "from abc import abstractmethod\n" + state + "class Base:\n"
            "    @abstractmethod\n"
            "    def read(self, state: State):\n"
            "        return state.value\n",
            state_types=("State",),
        )
        lookalike = self._findings(
            "def not_abstractmethod(fn): return fn\n" + state + "class Base:\n"
            "    @not_abstractmethod\n"
            "    def read(self, state: State):\n"
            "        return state.value\n",
            state_types=("State",),
        )

        explicit_qg128 = [item for item in explicit if item.code == "QG128"]
        lookalike_qg128 = [item for item in lookalike if item.code == "QG128"]
        self.assertEqual([item.severity for item in explicit_qg128], ["info"])
        self.assertEqual([item.severity for item in lookalike_qg128], ["warning"])

    def test_graph_proven_root_polymorphic_slot_is_not_a_useless_wrapper(self) -> None:
        symbols = self._wrapper_symbols(
            "def make_value(): return 1\n"
            "class Base:\n"
            "    def get_value(self):\n"
            "        return make_value()\n"
            "class Child(Base):\n"
            "    def get_value(self):\n"
            "        return self.value + 1\n"
        )

        self.assertNotIn("sample.Base.get_value", symbols)
        self.assertNotIn("sample.Child.get_value", symbols)

    def test_override_wrapper_does_not_inherit_parent_slot_exemption(self) -> None:
        symbols = self._wrapper_symbols(
            "def make_value(): return 1\n"
            "class Base:\n"
            "    def get_value(self):\n"
            "        return make_value()\n"
            "class Child(Base):\n"
            "    def get_value(self):\n"
            "        return make_value()\n"
        )

        self.assertNotIn("sample.Base.get_value", symbols)
        self.assertIn("sample.Child.get_value", symbols)

    def test_override_chain_cannot_launder_intermediate_or_leaf_wrappers(self) -> None:
        symbols = self._wrapper_symbols(
            "def make_value(): return 1\n"
            "class Base:\n"
            "    def get_value(self):\n"
            "        return make_value()\n"
            "class Mid(Base):\n"
            "    def get_value(self):\n"
            "        return make_value()\n"
            "class Leaf(Mid):\n"
            "    def get_value(self):\n"
            "        return make_value()\n"
        )

        self.assertNotIn("sample.Base.get_value", symbols)
        self.assertIn("sample.Mid.get_value", symbols)
        self.assertIn("sample.Leaf.get_value", symbols)


if __name__ == "__main__":
    unittest.main()
