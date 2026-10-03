from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.scanner import RepositoryScanner
from runtime.src.topology_facts import ContractOwnership


class TestContractOwnershipFacts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.root = root / "project"
        self.deps = root / "deps"
        self.root.mkdir()
        (self.deps / "fakefw").mkdir(parents=True)
        (self.deps / "fakefw" / "__init__.pyi").write_text(
            "class Callback:\n"
            "    def on_train_begin(self): ...\n"
            "class OptionalSettings: ...\n",
            encoding="utf-8",
        )
        self.config = GuardConfig(
            require_docstrings=False,
            require_docstring_sections=False,
            include_tests=True,
            strict_get=True,
        )

    def _scan(self, name: str, source: str):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        snapshot = directory_analysis_snapshot(self.root, self.config)
        topology = normalized_topology(
            RepositoryRelationGraph(snapshot),
            StaticPythonDependencyResolver((self.deps,)),
        )
        scanner = RepositoryScanner(self.root, self.config, snapshot, topology)
        report = scanner.scan({path})
        assert scanner.topology is not None
        return report, scanner.topology

    @staticmethod
    def _finding(report, code: str):
        return next(item for item in report.findings if item.code == code)

    def test_internal_mapping_remains_strict(self) -> None:
        report, topology = self._scan(
            "service.py",
            "from typing import Mapping\n\n"
            "def read(payload: Mapping[str, object]):\n"
            "    return payload.get('required', 0)\n",
        )

        finding = self._finding(report, "QG004")
        self.assertEqual(finding.severity, "critical")
        self.assertEqual(
            finding.evidence["contract_ownership"],
            ContractOwnership.INTERNAL_FORMAL.value,
        )
        contract = next(item for item in topology.contracts if item.line == 4)
        self.assertEqual(contract.ownership, ContractOwnership.INTERNAL_FORMAL)
        self.assertEqual(contract.operation, "mapping_get_default")
        self.assertEqual(contract.selector, "required")

    def test_unresolved_mapping_stays_unknown_instead_of_strict_get_guessing(
        self,
    ) -> None:
        report, topology = self._scan(
            "service.py",
            "def read(payload):\n    return payload.get('optional', None)\n",
        )

        finding = self._finding(report, "QG026")
        self.assertEqual(finding.severity, "info")
        self.assertTrue(finding.evidence["semantic_review_required"])
        self.assertEqual(
            finding.evidence["contract_ownership"], ContractOwnership.UNKNOWN.value
        )
        contract = next(item for item in topology.contracts if item.line == 2)
        self.assertEqual(contract.ownership, ContractOwnership.UNKNOWN)

    def test_import_name_without_resolved_owner_stays_unknown(self) -> None:
        report, topology = self._scan(
            "service.py",
            "from fakefw import OptionalSettings\n\n"
            "def read(settings: OptionalSettings):\n"
            "    return settings.get('steps', None)\n",
        )

        finding = self._finding(report, "QG026")
        self.assertEqual(finding.severity, "info")
        self.assertEqual(
            finding.evidence["contract_ownership"], ContractOwnership.UNKNOWN.value
        )
        contract = next(item for item in topology.contracts if item.line == 4)
        self.assertEqual(contract.ownership, ContractOwnership.UNKNOWN)
        self.assertIn(
            "external_annotation_candidate:fakefw.OptionalSettings", contract.evidence
        )

    def test_external_framework_base_can_own_inherited_optional_mapping(self) -> None:
        report, topology = self._scan(
            "callback.py",
            "from fakefw import Callback\n\n"
            "class Metrics(Callback):\n"
            "    def on_train_begin(self):\n"
            "        return self.params.get('steps', None)\n",
        )

        finding = self._finding(report, "QG004")
        self.assertEqual(finding.severity, "info")
        self.assertEqual(
            finding.evidence["contract_ownership"],
            ContractOwnership.EXTERNAL_OPTIONAL.value,
        )
        contract = next(item for item in topology.contracts if item.line == 5)
        self.assertEqual(contract.ownership, ContractOwnership.EXTERNAL_OPTIONAL)
        self.assertIn("resolved_external_base:fakefw.Callback", contract.evidence)

    def test_local_state_on_external_subclass_remains_internal(self) -> None:
        report, topology = self._scan(
            "callback.py",
            "from fakefw import Callback\n\n"
            "class Metrics(Callback):\n"
            "    def __init__(self):\n"
            "        self.params: dict[str, object] = {}\n\n"
            "    def read(self):\n"
            "        return self.params.get('required', 0)\n",
        )

        finding = self._finding(report, "QG004")
        self.assertEqual(finding.severity, "critical")
        self.assertEqual(
            finding.evidence["contract_ownership"],
            ContractOwnership.INTERNAL_FORMAL.value,
        )
        contract = next(item for item in topology.contracts if item.line == 8)
        self.assertEqual(contract.ownership, ContractOwnership.INTERNAL_FORMAL)

    def test_hasattr_external_hook_is_distinct_from_internal_shape_guessing(
        self,
    ) -> None:
        report, topology = self._scan(
            "callback.py",
            "from fakefw import Callback\n\n"
            "class Metrics(Callback):\n"
            "    def external(self):\n"
            "        return hasattr(self, 'params')\n\n"
            "    def internal(self):\n"
            "        self.state = 1\n"
            "        return hasattr(self, 'state')\n",
        )

        findings = [item for item in report.findings if item.code == "QG005"]
        by_line = {item.line: item for item in findings}
        self.assertEqual(
            by_line[5].evidence["contract_ownership"],
            ContractOwnership.EXTERNAL_OPTIONAL.value,
        )
        self.assertEqual(by_line[5].severity, "info")
        self.assertEqual(
            by_line[9].evidence["contract_ownership"],
            ContractOwnership.INTERNAL_FORMAL.value,
        )
        self.assertEqual(by_line[9].severity, "error")
        contracts = {item.line: item for item in topology.contracts}
        self.assertEqual(contracts[5].ownership, ContractOwnership.EXTERNAL_OPTIONAL)
        self.assertEqual(contracts[9].ownership, ContractOwnership.INTERNAL_FORMAL)

    def test_explicit_boundary_records_dynamic_contract(self) -> None:
        report, topology = self._scan(
            "api/adapter.py",
            "from typing import Mapping\n\n"
            "def read(payload: Mapping[str, object]):\n"
            "    return payload.get('optional', None)\n",
        )

        finding = self._finding(report, "QG004")
        self.assertEqual(finding.severity, "info")
        self.assertEqual(
            finding.evidence["contract_ownership"],
            ContractOwnership.DYNAMIC_BOUNDARY.value,
        )
        contract = next(item for item in topology.contracts if item.line == 4)
        self.assertEqual(contract.ownership, ContractOwnership.DYNAMIC_BOUNDARY)


if __name__ == "__main__":
    unittest.main()
