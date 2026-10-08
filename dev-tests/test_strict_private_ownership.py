"""QG149 private ownership must behave as a hard static boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.src.config import _PROFILE_NONBLOCKING_PATHS

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


class TestStrictPrivateOwnership(unittest.TestCase):
    def _scan(self, sources: dict[str, str]):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, contents in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        config = GuardConfig(
            require_docstrings=False,
            require_docstring_sections=False,
            include_tests=True,
        )
        return [
            f
            for f in RepositoryScanner(root, config).scan().findings
            if f.code == "QG149"
        ]

    def test_private_class_reexport_through_init_is_critical(self):
        findings = self._scan(
            {
                "pkg/_implementation.py": "class _Engine: pass\n",
                "pkg/__init__.py": "from ._implementation import _Engine as Engine\n",
            }
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "critical")
        self.assertEqual(findings[0].evidence["local_name"], "Engine")

    def test_private_module_reexport_through_init_is_critical(self):
        findings = self._scan(
            {
                "pkg/__init__.py": "from . import _implementation as implementation\n",
                "pkg/_implementation.py": "VALUE = 3\n",
            }
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "critical")

    def test_public_import_through_init_is_not_private(self):
        findings = self._scan(
            {
                "pkg/__init__.py": "from .service import Service as ExportedService\n",
                "pkg/service.py": "class Service: pass\n",
            }
        )
        self.assertEqual(findings, [])

    def test_parent_private_member_is_forbidden_via_self_or_super(self):
        findings = self._scan(
            {
                "pkg/base.py": "class Base:\n    def _secret(self): pass\n",
                "pkg/derived.py": "from .base import Base\nclass Derived(Base):\n"
                "    def use(self):\n        self._secret()\n        super()._secret()\n",
            }
        )
        self.assertEqual(len(findings), 2)
        self.assertTrue(findings[0].evidence["inherited_private"])
        self.assertTrue(findings[1].evidence["super_access"])
        self.assertTrue(all(f.severity == "critical" for f in findings))

    def test_same_class_private_and_explicit_private_override_are_allowed(self):
        findings = self._scan(
            {
                "pkg/base.py": "class Base:\n    def _secret(self): pass\n"
                "    def own(self): self._secret()\n",
                "pkg/derived.py": "from .base import Base\nclass Derived(Base):\n"
                "    def _secret(self): return 1\n"
                "    def own(self): return self._secret()\n",
            }
        )
        self.assertEqual(findings, [])

    def test_multilevel_inheritance_private_member_is_blocked(self):
        findings = self._scan(
            {
                "pkg/model.py": "class Base:\n    def _secret(self): pass\n"
                "class Middle(Base):\n    pass\n"
                "class Leaf(Middle):\n    def run(self): self._secret()\n",
            }
        )
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].evidence["inherited_private"])

    def test_typed_other_instance_of_same_class_is_legal(self):
        findings = self._scan(
            {
                "pkg/model.py": "class Model:\n"
                "    def __init__(self): self._value = 1\n"
                "    def compare(self, other: 'Model'): return other._value\n"
            }
        )
        self.assertEqual(findings, [])

    def test_untyped_other_instance_is_not_assumed_same_owner(self):
        findings = self._scan(
            {
                "pkg/model.py": "class Model:\n"
                "    def __init__(self): self._value = 1\n"
                "    def compare(self, other): return other._value\n"
            }
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "critical")

    def test_inherited_private_field_not_method_only(self):
        findings = self._scan(
            {
                "pkg/model.py": "class Base:\n"
                "    def __init__(self): self._state = 1\n"
                "class Child(Base):\n"
                "    def work(self): return self._state\n",
            }
        )
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].evidence["inherited_private"])

    def test_private_module_attribute_remains_blocked(self):
        findings = self._scan(
            {
                "pkg/one.py": "def _operation(): pass\n",
                "pkg/two.py": "from . import one\none._operation()\n",
            }
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "critical")

    def test_tests_are_warning_not_critical(self):
        findings = self._scan(
            {
                "pkg/_core.py": "class _Engine: pass\n",
                "tests/test_core.py": "from pkg._core import _Engine\n",
            }
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warning")

    def test_dunder_private_export_is_critical(self):
        findings = self._scan(
            {
                "pkg/api.py": "def _secret(): pass\n__all__ = ['_secret']\n",
            }
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "critical")
        self.assertEqual(findings[0].evidence["export"], "__all__")

    def test_module_private_class_cannot_be_publicly_aliased(self):
        findings = self._scan(
            {
                "pkg/api.py": "class _Engine: pass\nEngine = _Engine\n",
            }
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "critical")

    def test_profile_explicit_nonblocking_path_is_warning(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "utils").mkdir()
        (root / "utils/tracing.py").write_text(
            "from pkg._internal import _Profiler\n", encoding="utf-8"
        )
        with patch.dict(
            _PROFILE_NONBLOCKING_PATHS, {"strict-private-test": ("utils/tracing.py",)}
        ):
            config = GuardConfig(
                project_name="strict-private-test",
                include_tests=True,
                require_docstrings=False,
                require_docstring_sections=False,
            )
            findings = [
                f
                for f in RepositoryScanner(root, config).scan().findings
                if f.code == "QG149"
            ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warning")

    def test_dunder_protocol_is_not_private(self):
        findings = self._scan(
            {
                "pkg/one.py": "class C:\n    def __init__(self): pass\n"
                "    def __iter__(self): return iter(())\n",
                "pkg/two.py": "from .one import C\nC().__iter__()\n",
            }
        )
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
