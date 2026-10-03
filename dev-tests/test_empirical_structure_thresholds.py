from __future__ import annotations

import unittest
from pathlib import Path

from runtime.src.config import GuardConfig
from runtime.src.facts import ModuleFacts
from runtime.src.model import Definition
from runtime.src.rules import RuleEvaluator


class TestEmpiricalStructureThresholds(unittest.TestCase):
    """Verify two-level size gates derived from historical repository calibration."""

    def _class_findings(self, methods: int):
        definition = Definition(
            module="sample",
            qualname="Resource",
            name="Resource",
            kind="class",
            path=Path("sample.py"),
            line=1,
            end_line=10,
            column=0,
            code_line_count=10,
            direct_method_count=methods,
            public_method_count=methods,
        )
        evaluator = RuleEvaluator(GuardConfig(require_docstrings=False))
        return [
            item
            for item in evaluator.evaluate([definition], [])
            if item.code == "QG008"
        ]

    def _module_findings(self, lines: int):
        source = "\n".join("x = 1" for _ in range(lines))
        facts = ModuleFacts(path=Path("sample.py"), module="sample", source=source)
        evaluator = RuleEvaluator(GuardConfig(require_docstrings=False))
        return [
            item for item in evaluator.evaluate([], [facts]) if item.code == "QG019"
        ]

    def test_qg008_uses_20_review_50_hard_limit(self) -> None:
        self.assertEqual(self._class_findings(20), [])
        review_21 = self._class_findings(21)
        self.assertEqual(len(review_21), 1)
        self.assertEqual(review_21[0].severity, "info")
        self.assertTrue(review_21[0].evidence["semantic_review_required"])
        review_50 = self._class_findings(50)
        self.assertEqual(len(review_50), 1)
        self.assertEqual(review_50[0].severity, "info")
        hard_51 = self._class_findings(51)
        self.assertEqual(len(hard_51), 1)
        self.assertEqual(hard_51[0].severity, "critical")
        self.assertNotIn("semantic_review_required", hard_51[0].evidence)

    def test_qg019_uses_1000_review_2000_hard_limit(self) -> None:
        self.assertEqual(self._module_findings(1000), [])
        review_1001 = self._module_findings(1001)
        self.assertEqual(len(review_1001), 1)
        self.assertEqual(review_1001[0].severity, "info")
        self.assertTrue(review_1001[0].evidence["semantic_review_required"])
        review_2000 = self._module_findings(2000)
        self.assertEqual(len(review_2000), 1)
        self.assertEqual(review_2000[0].severity, "info")
        hard_2001 = self._module_findings(2001)
        self.assertEqual(len(hard_2001), 1)
        self.assertEqual(hard_2001[0].severity, "critical")
        self.assertNotIn("semantic_review_required", hard_2001[0].evidence)


if __name__ == "__main__":
    unittest.main()
