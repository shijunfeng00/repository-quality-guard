"""QG240 frozen lineage tests migrated from the pre-rqgtest framework."""

import ast
import json
import tempfile
from collections import Counter
from pathlib import Path

from rqgtest import CaseConfig, TestCase
from runtime.src.case_contract_rules import case_contract_findings
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


class CaseContractMigratedLegacyCase(TestCase):
    """Run independent preserved TP/TN contracts through the production analyzer."""

    def get_enterpoint(self):
        """Return the production QG detector shared by both legacy rule cohorts."""
        return case_contract_findings

    def get_input_data(self):
        """Provide the original independent QG240 and QG241–244 source fixtures."""
        for name in ("qg240-reviewed-legacy-v2.json", "qg241-244-reviewed-legacy-v2.json"):
            rows = json.loads((Path(__file__).parent / "cases" / name).read_text(encoding="utf-8"))
            for row in rows:
                yield (
                    (Path(row["path"]), ast.parse(row["source"])),
                    {"repository_root": Path(__file__).resolve().parents[1]},
                )

    def get_output_data(self):
        """Load the preserved TP/TN rule codes independently of scanner output."""
        for name in ("qg240-reviewed-legacy-v2.json", "qg241-244-reviewed-legacy-v2.json"):
            rows = json.loads((Path(__file__).parent / "cases" / name).read_text(encoding="utf-8"))
            for row in rows:
                yield {"codes": row["expected_codes"], "path": row["path"], "source": row["source"]}

    def equals(self, expected, actual):
        """Require the complete Finding multiset, origin and evidence fields."""
        return (
            Counter(finding.code for finding in actual) == Counter(expected["codes"])
            and all(finding.path == expected["path"] for finding in actual)
            and all(finding.confidence == "high" for finding in actual)
            and all(finding.source == "test-contract" for finding in actual)
            and all(1 <= finding.line <= max(1, len(expected["source"].splitlines())) for finding in actual)
            and all(finding.symbol for finding in actual)
            and all(finding.evidence["absolute_blocker"] is True for finding in actual)
        )


class FrozenRepositoryDirectoryLayoutCase(TestCase):
    """Prove QG240 accepts the frozen tree and rejects extra directory levels."""

    def __init__(self):
        super().__init__(config=CaseConfig(process="main"))

    def get_enterpoint(self):
        return RepositoryScanner.scan

    def get_input_data(self):
        source_root = Path(__file__).resolve().parents[1]
        cases = (
            (None, ()),
            ("tests_support", ("tests_support", "unexpected_directory")),
            ("tests/data", ("tests/data", "unexpected_tests_child")),
            ("tests/cases/release", ("tests/cases/release", "unexpected_directory")),
            ("tests/cases/nested", ("tests/cases/nested", "unexpected_directory")),
            ("tests/dataloaders/nested", ("tests/dataloaders/nested", "unexpected_directory")),
            ("tests/helper.py", ("tests/helper.py", "unexpected_tests_file")),
            ("tests/dataloaders/extra.json", ("tests/dataloaders/extra.json", "non_python_dataloader_asset")),
            ("profiles/council-of-harnesses/tests/support", ("profiles/council-of-harnesses/tests/support", "unexpected_tests_child")),
            ("profiles/council-of-harnesses/tests/cases/nested", ("profiles/council-of-harnesses/tests/cases/nested", "unexpected_directory")),
            ("profiles/council-of-harnesses/tests/helper.py", ("profiles/council-of-harnesses/tests/helper.py", "unexpected_tests_file")),
            ("profiles/council-of-harnesses/tests/dataloaders/extra.json", ("profiles/council-of-harnesses/tests/dataloaders/extra.json", "non_python_dataloader_asset")),
            (".ruff_cache", ()),
        )
        for extra, _ in cases:
            with tempfile.TemporaryDirectory(prefix="rqg-qg240-layout-") as directory:
                root = Path(directory)
                for path in source_root.rglob("*"):
                    if not path.is_dir():
                        continue
                    relative = path.relative_to(source_root)
                    if ".git" in relative.parts or any(part in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache"} for part in relative.parts):
                        continue
                    (root / relative).mkdir(parents=True, exist_ok=True)
                if extra is not None:
                    target = root / extra
                    if target.suffix:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text("contract violation fixture", encoding="utf-8")
                    else:
                        target.mkdir(parents=True, exist_ok=True)
                config = GuardConfig(project_name="repository-quality-guard", require_docstrings=False, require_docstring_sections=False)
                yield ((RepositoryScanner(root, config),), {})

    def get_output_data(self):
        yield ()
        yield (("tests_support", "unexpected_directory"),)
        yield (("tests/data", "unexpected_directory"), ("tests/data", "unexpected_tests_child"))
        yield (("tests/cases/release", "unexpected_directory"),)
        yield (("tests/cases/nested", "unexpected_directory"),)
        yield (("tests/dataloaders/nested", "unexpected_directory"),)
        yield (("tests/helper.py", "unexpected_tests_file"),)
        yield (("tests/dataloaders/extra.json", "non_python_dataloader_asset"),)
        yield (("profiles/council-of-harnesses/tests/support", "unexpected_tests_child"),)
        yield (("profiles/council-of-harnesses/tests/cases/nested", "unexpected_directory"),)
        yield (("profiles/council-of-harnesses/tests/helper.py", "unexpected_tests_file"),)
        yield (("profiles/council-of-harnesses/tests/dataloaders/extra.json", "non_python_dataloader_asset"),)
        yield ()

    def equals(self, expected, actual):
        observed = tuple(sorted(
            (finding.evidence["relative_path"], finding.evidence["violation"])
            for finding in actual.findings
            if finding.code == "QG240" and finding.evidence.get("kind") == "RQG_DIRECTORY_LAYOUT"
        ))
        return observed == expected


class TestOnlySupportConsumerGraphCase(TestCase):
    """Prove QG240 uses resolved module/symbol consumers, not helper-name guesses."""

    def get_enterpoint(self):
        return RepositoryScanner.scan

    def get_input_data(self):
        source_root = Path(__file__).resolve().parents[1]
        scenarios = (
            "public_class_test_only",
            "public_class_with_production_consumer",
            "private_class_in_shared_module",
            "private_function_only_module",
        )
        for scenario in scenarios:
            with tempfile.TemporaryDirectory(prefix="rqg-qg240-consumer-graph-") as directory:
                root = Path(directory)
                for path in source_root.rglob("*"):
                    if not path.is_dir():
                        continue
                    relative = path.relative_to(source_root)
                    if ".git" in relative.parts or any(part in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache"} for part in relative.parts):
                        continue
                    (root / relative).mkdir(parents=True, exist_ok=True)
                (root / "runtime" / "__init__.py").write_text("", encoding="utf-8")
                (root / "runtime" / "src" / "__init__.py").write_text("", encoding="utf-8")
                if scenario in {"public_class_test_only", "public_class_with_production_consumer"}:
                    (root / "runtime" / "src" / "isolated.py").write_text(
                        "class PubliclyNamedNode:\n    def execute(self):\n        return None\n",
                        encoding="utf-8",
                    )
                    imported_name = "PubliclyNamedNode"
                elif scenario == "private_class_in_shared_module":
                    (root / "runtime" / "src" / "isolated.py").write_text(
                        "class PublicProductionNode:\n    def execute(self):\n        return None\n\n"
                        "class _OnlyTestSupportClass:\n    pass\n",
                        encoding="utf-8",
                    )
                    imported_name = "_OnlyTestSupportClass"
                else:
                    (root / "runtime" / "src" / "isolated.py").write_text(
                        "def _only_test_support():\n    return None\n",
                        encoding="utf-8",
                    )
                    imported_name = "_only_test_support"
                (root / "tests" / "test_consumer.py").write_text(
                    "from rqgtest import TestCase\n"
                    f"from runtime.src.isolated import {imported_name}\n\n"
                    "class ConsumerCase(TestCase):\n"
                    f"    def get_enterpoint(self):\n        return {imported_name}\n\n"
                    "    def get_input_data(self):\n        yield ()\n\n"
                    "    def get_output_data(self):\n        yield ()\n\n"
                    "    def equals(self, expected, actual):\n        return True\n",
                    encoding="utf-8",
                )
                if scenario in {"public_class_with_production_consumer", "private_class_in_shared_module"}:
                    production_target = "PublicProductionNode" if scenario == "private_class_in_shared_module" else "PubliclyNamedNode"
                    (root / "runtime" / "src" / "production_consumer.py").write_text(
                        f"from runtime.src.isolated import {production_target}\n\n"
                        f"def create_node():\n    return {production_target}()\n",
                        encoding="utf-8",
                    )
                config = GuardConfig(
                    project_name="repository-quality-guard",
                    require_docstrings=False,
                    require_docstring_sections=False,
                )
                yield ((RepositoryScanner(root, config),), {})

    def get_output_data(self):
        yield (("runtime/src/isolated.py", "test_only_class_consumer"),)
        yield ()
        yield (("runtime/src/isolated.py", "test_only_class_consumer"),)
        yield (("runtime/src/isolated.py", "test_only_helper_module"),)

    def equals(self, expected, actual):
        observed = tuple(sorted({
            (finding.path, finding.evidence["violation"])
            for finding in actual.findings
            if finding.code == "QG240"
            and finding.evidence.get("kind") == "RQG_TEST_ONLY_SUPPORT_GRAPH"
        }))
        return observed == expected


class ProfileTestModuleContractCase(TestCase):
    """Require private Profile tests to use the same rqgtest Case contract."""

    def get_enterpoint(self):
        return RepositoryScanner.scan

    def get_input_data(self):
        with tempfile.TemporaryDirectory(prefix="rqg-profile-test-contract-") as directory:
            root = Path(directory)
            (root / "profiles" / "council-of-harnesses" / "tests").mkdir(parents=True)
            (root / "profiles" / "council-of-harnesses" / "tests" / "test_helper.py").write_text(
                "def helper():\n    return None\n", encoding="utf-8"
            )
            config = GuardConfig(
                project_name="repository-quality-guard",
                require_docstrings=False,
                require_docstring_sections=False,
            )
            yield ((RepositoryScanner(root, config),), {})

    def get_output_data(self):
        yield "profiles/council-of-harnesses/tests/test_helper.py"

    def equals(self, expected, actual):
        return any(
            finding.code == "QG240"
            and finding.path == expected
            and finding.source == "test-contract"
            and finding.evidence.get("kind") != "RQG_DIRECTORY_LAYOUT"
            for finding in actual.findings
        )
