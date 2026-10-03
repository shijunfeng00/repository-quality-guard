from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


class TestReturnContractPolicy(unittest.TestCase):
    def _scan(self, source: str):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.py").write_text(source, encoding="utf-8")
            config = GuardConfig(
                require_docstrings=False,
                require_docstring_sections=False,
                include_tests=True,
            )
            return RepositoryScanner(root, config).scan().findings

    def test_pep604_none_annotation_allows_implicit_none_path(self) -> None:
        findings = self._scan(
            "def resolve(value: str) -> str | None:\n"
            "    if value:\n"
            "        return value\n"
        )

        self.assertFalse(any(item.code == "QG046" for item in findings))

    def test_nonnullable_annotation_still_reports_implicit_none_path(self) -> None:
        findings = self._scan(
            "def resolve(value: str) -> str:\n    if value:\n        return value\n"
        )

        self.assertTrue(any(item.code == "QG046" for item in findings))


if __name__ == "__main__":
    unittest.main()
