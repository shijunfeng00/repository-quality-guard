from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.python_import_resolution import resolve_local_import_symbol
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.scanner import RepositoryScanner
from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
from runtime.src.topology_facts import UsageKind


class TestLocalReexportResolution(unittest.TestCase):
    def test_explicit_reexport_chain_resolves_without_simple_name_guessing(
        self,
    ) -> None:
        imports = {
            "pkg": {"Tiny": "pkg.api.Tiny"},
            "pkg.api": {"Tiny": "pkg.impl.Tiny"},
        }
        known = {"pkg.impl.Tiny"}
        self.assertEqual(
            resolve_local_import_symbol("pkg.Tiny", imports, known),
            "pkg.impl.Tiny",
        )
        self.assertIsNone(resolve_local_import_symbol("other.Tiny", imports, known))

    def test_reexport_cycle_remains_unknown(self) -> None:
        imports = {"a": {"Thing": "b.Thing"}, "b": {"Thing": "a.Thing"}}
        self.assertIsNone(resolve_local_import_symbol("a.Thing", imports, set()))

    def test_scanner_counts_calls_through_package_reexport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_reexport_fixture(root)
            findings = (
                RepositoryScanner(root, GuardConfig(require_docstrings=False))
                .scan()
                .findings
            )
            self.assertNotIn(
                "pkg.base.Tiny",
                {item.symbol for item in findings if item.code == "QG002"},
            )

    def test_relation_topology_links_calls_through_package_reexport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_reexport_fixture(root)
            config = GuardConfig(require_docstrings=False)
            snapshot = directory_analysis_snapshot(root, config)
            topology = normalized_topology(
                RepositoryRelationGraph(snapshot),
                StaticPythonDependencyResolver(()),
            )
            incoming = topology.incoming("pkg.base.Tiny", (UsageKind.DIRECT_CALL,))
            self.assertEqual(
                {edge.source_id for edge in incoming}, {"first.make", "second.make"}
            )

    @staticmethod
    def _write_reexport_fixture(root: Path) -> None:
        (root / "pkg").mkdir()
        (root / "pkg" / "base.py").write_text(
            'class Tiny:\n    value: int = 1\n    label: str = "tiny"\n',
            encoding="utf-8",
        )
        (root / "pkg" / "__init__.py").write_text(
            "from .base import Tiny\n", encoding="utf-8"
        )
        for module in ("first", "second"):
            (root / f"{module}.py").write_text(
                "from pkg import Tiny\n\ndef make():\n    return Tiny()\n",
                encoding="utf-8",
            )


if __name__ == "__main__":
    unittest.main()
