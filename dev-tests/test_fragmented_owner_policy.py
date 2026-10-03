from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


class TestFragmentedOwnerPolicy(unittest.TestCase):
    def _scan(self, source: str):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "sample.py").write_text(source, encoding="utf-8")
        config = GuardConfig(
            require_docstrings=False,
            require_docstring_sections=False,
            include_tests=True,
        )
        return RepositoryScanner(root, config).scan().findings

    def test_five_one_shot_private_helpers_trigger_semantic_qg013(self) -> None:
        source = "class Worker:\n"
        for name in "abcde":
            source += f"    def _{name}(self, value):\n        return value + 1\n\n"
        source += (
            "    def run(self, value):\n"
            "        return (self._a(value) + self._b(value) + self._c(value) + "
            "self._d(value) + self._e(value))\n"
        )
        findings = self._scan(source)
        qg013 = [item for item in findings if item.code == "QG013"]
        self.assertEqual(len(qg013), 1)
        self.assertEqual(qg013[0].severity, "info")
        self.assertEqual(qg013[0].evidence["ephemeral_helpers"], 5)
        self.assertTrue(qg013[0].evidence["semantic_review_required"])

    def test_many_tiny_public_methods_do_not_trigger_qg013(self) -> None:
        source = "class Resource:\n"
        for index in range(10):
            source += f"    def value_{index}(self):\n        return {index}\n\n"
        findings = self._scan(source)
        self.assertFalse(any(item.code == "QG013" for item in findings))

    def test_shared_private_primitives_do_not_trigger_qg013(self) -> None:
        source = "class Resource:\n"
        for name in "abcde":
            source += f"    def _{name}(self, value):\n        return value + 1\n\n"
        source += (
            "    def first(self, value):\n"
            "        return self._a(value)+self._b(value)+self._c(value)+self._d(value)+self._e(value)\n\n"
            "    def second(self, value):\n"
            "        return self._a(value)+self._b(value)+self._c(value)+self._d(value)+self._e(value)\n"
        )
        findings = self._scan(source)
        self.assertFalse(any(item.code == "QG013" for item in findings))


if __name__ == "__main__":
    unittest.main()
