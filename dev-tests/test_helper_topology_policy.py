from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


class TestHelperTopologyPolicy(unittest.TestCase):
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

    def test_private_one_shot_helper_is_qg001_candidate(self) -> None:
        findings = self._scan(
            "def _helper(value):\n"
            "    return value + 1\n\n"
            "def public(value):\n"
            "    return _helper(value)\n"
        )
        qg001 = [item for item in findings if item.code == "QG001"]
        self.assertEqual([item.symbol for item in qg001], ["sample._helper"])
        self.assertTrue(qg001[0].evidence["ephemeral_helper_candidate"])
        self.assertEqual(qg001[0].evidence["direct_callers"], 1)

    def test_shared_private_primitive_is_not_qg001_candidate(self) -> None:
        findings = self._scan(
            "def _normalize(value):\n"
            "    return value.strip()\n\n"
            "def first(value):\n"
            "    return _normalize(value)\n\n"
            "def second(value):\n"
            "    return _normalize(value)\n"
        )
        self.assertNotIn(
            "sample._normalize",
            {item.symbol for item in findings if item.code == "QG001"},
        )

    def test_repeated_calls_from_one_owner_are_real_reuse(self) -> None:
        findings = self._scan(
            "def _format(value):\n"
            "    return value.strip()\n\n"
            "def render(value):\n"
            "    return _format(value) + _format(value)\n"
        )
        self.assertNotIn(
            "sample._format", {item.symbol for item in findings if item.code == "QG001"}
        )

    def test_callback_reference_is_not_qg001_candidate(self) -> None:
        findings = self._scan(
            "def _callback(value):\n"
            "    return value + 1\n\n"
            "def consume(fn):\n"
            "    return fn\n\n"
            "def main():\n"
            "    return consume(_callback)\n"
        )
        self.assertNotIn(
            "sample._callback",
            {item.symbol for item in findings if item.code == "QG001"},
        )

    def test_public_short_function_is_not_described_as_ephemeral_helper(self) -> None:
        findings = self._scan("def public(value):\n    return value + 1\n")
        self.assertNotIn(
            "sample.public", {item.symbol for item in findings if item.code == "QG001"}
        )


if __name__ == "__main__":
    unittest.main()
