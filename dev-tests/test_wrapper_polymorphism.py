from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from runtime.src.agent_rules import _protocol_like_method
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.scanner import RepositoryScanner
from runtime.src.topology_facts import OwnerKind, SymbolFact, Visibility
from runtime.src.topology_policy import symbol_is_externally_invoked


class TestWrapperPolymorphism(unittest.TestCase):
    def _wrapper_symbols(self, source: str) -> set[str]:
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
        topology = normalized_topology(RepositoryRelationGraph(snapshot))
        findings = (
            RepositoryScanner(
                root,
                config,
                analysis_snapshot=snapshot,
                topology=topology,
            )
            .scan()
            .findings
        )
        return {item.symbol for item in findings if item.code == "QG010"}

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

    def test_protocol_marker_helpers_require_exact_abstractmethod_leaf(self) -> None:
        explicit = SymbolFact(
            symbol_id="sample.Base.get_value",
            language="python",
            kind="method",
            owner_id="sample.Base",
            owner_kind=OwnerKind.CLASS,
            visibility=Visibility.PUBLIC,
            path=Path("sample.py"),
            line=1,
            end_line=2,
            lines=2,
            name="get_value",
            decorators=("abc.abstractmethod",),
        )
        lookalike = SymbolFact(
            symbol_id="sample.Base.other",
            language="python",
            kind="method",
            owner_id="sample.Base",
            owner_kind=OwnerKind.CLASS,
            visibility=Visibility.PUBLIC,
            path=Path("sample.py"),
            line=3,
            end_line=4,
            lines=2,
            name="other",
            decorators=("not_abstractmethod",),
        )
        tree = ast.parse("@not_abstractmethod\ndef get_value(self):\n    return 1\n")
        method = tree.body[0]
        self.assertIsInstance(method, ast.FunctionDef)

        self.assertTrue(symbol_is_externally_invoked(explicit))
        self.assertFalse(symbol_is_externally_invoked(lookalike))
        self.assertFalse(_protocol_like_method(method))

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
