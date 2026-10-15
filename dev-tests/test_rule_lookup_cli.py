"""规则检索命令的真实输出与失败行为回归。"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rule_lookup.py"


class RuleLookupTests(unittest.TestCase):
    """验证真实 RULES.md 中的定义、修复方向和 CLI 退出行为。"""

    def run_lookup(self, *args: str) -> subprocess.CompletedProcess[str]:
        """执行真实 CLI，独立检查 stdout/stderr 与返回码。"""
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_batch_returns_both_rules_and_specific_family_cards(self) -> None:
        """一次查询字段规则和异常规则，必须返回两类不同的修复方向。"""
        result = self.run_lookup("-QG153", "-QG176")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## QG153", result.stdout)
        self.assertIn("## QG176", result.stdout)
        self.assertIn("内部字段 / Mapping 契约", result.stdout)
        self.assertIn("异常、失败与 fallback", result.stdout)
        self.assertIn("producer", result.stdout.lower())
        self.assertIn("禁止做法", result.stdout)

    def test_source_bound_chapter_rules_are_real_searchable_contracts(self) -> None:
        """Actual cross-repo QG213 must return its source-backed fix contract."""
        for code, evidence in (
            ("QG210", "构造"),
            ("QG212", "编译"),
            ("QG213", "部分提交"),
            ("QG214", "异常"),
            ("QG215", "宽异常"),
            ("QG216", "TypedDict"),
        ):
            with self.subTest(code=code):
                result = self.run_lookup(f"-{code}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"## {code}\n", result.stdout)
                self.assertIn("专项契约原文", result.stdout)
                self.assertIn(evidence, result.stdout)

    def test_every_authoritative_chapter_rule_is_queryable(self) -> None:
        """A chapter is a rule definition even without a primary table row."""
        import re

        rules = (ROOT / "references" / "RULES.md").read_text(encoding="utf-8")
        sections = re.findall(r"^### (QG\d{3}(?: / QG\d{3})*) ·", rules, re.M)
        self.assertGreaterEqual(len(sections), 5)
        codes = [code for section in sections for code in section.split(" / ")]
        result = self.run_lookup(*(f"-{code}" for code in codes))
        self.assertEqual(result.returncode, 0, result.stderr)
        for code in codes:
            self.assertEqual(result.stdout.count(f"## {code}\n"), 1)

    def test_retired_rule_numbers_remain_unavailable(self) -> None:
        """退休 QG161、QG177 不得复活为有效规则或残留查询帮助。"""
        for code in ("QG161", "QG177"):
            with self.subTest(code=code):
                result = self.run_lookup(f"-{code}")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(code, result.stderr)

    def test_requested_examples_fail_for_missing_rule_without_partial_success(
        self,
    ) -> None:
        """用户传入确实不存在的 QG456，必须显式拒绝而不是杜撰。"""
        result = self.run_lookup("-QG123", "-QG456")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("QG456", result.stderr)

    def test_prints_authoritative_global_contract_verbatim(self) -> None:
        """必须携带真实 RULES.md 修复方向原文，而非只有简化的 QG 消息。"""
        rules = (ROOT / "references" / "RULES.md").read_text(encoding="utf-8")
        global_rule = (
            rules.split("## 修复方向契约\n", 1)[1]
            .split("### 常见规则族修复卡", 1)[0]
            .strip()
        )
        result = self.run_lookup("-QG176")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(global_rule, result.stdout)

    def test_help_and_non_qg_format_rule(self) -> None:
        """帮助不应返回报错；正式非 QG 规则也必须能直接检索。"""
        help_result = self.run_lookup("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("-QG123", help_result.stdout)
        format_result = self.run_lookup("-RUF:FORMAT")
        self.assertEqual(format_result.returncode, 0, format_result.stderr)
        self.assertIn("## RUF:FORMAT", format_result.stdout)

    def test_exact_code_does_not_match_other_rule_definitions(self) -> None:
        """一个编号不会因为包含关系而把其他规则的全文混入。"""
        result = self.run_lookup("-QG123")
        self.assertEqual(result.returncode, 0)
        self.assertIn("## QG123", result.stdout)
        self.assertNotIn("## QG124", result.stdout)
        self.assertIn("旧检测已停止", result.stdout)

    def test_duplicates_are_deduplicated_and_output_is_deterministic(self) -> None:
        """重复编号只显示一张卡，稳定输出不依赖运行环境。"""
        first = self.run_lookup("-QG060", "-QG060")
        second = self.run_lookup("-QG060")
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stdout.count("## QG060"), 1)

    def test_retired_test_shape_rules_fail_closed(self) -> None:
        """退役编号不得仅因旧测试存在而恢复有效性。"""
        for code in ("QG170", "QG171", "QG172", "QG173", "QG174", "QG175", "QG192", "QG193", "QG194"):
            with self.subTest(code=code):
                result = self.run_lookup(f"-{code}")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_invalid_argument_is_not_silently_ignored(self) -> None:
        """错误的编号与任意未声明参数均不可被解释成有效查询。"""
        for args in [(), ("--bad",), ("-QG17",), ("-QG99999",)]:
            with self.subTest(args=args):
                result = self.run_lookup(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_all_indexed_rules_have_consistent_lookup_output(self) -> None:
        """逐个遍历 RULES.md 的真实规则索引，所有规则均可查询。"""
        import re

        text = (ROOT / "references" / "RULES.md").read_text(encoding="utf-8")
        codes = re.findall(r"^\| `(QG\d{3}|RUF:FORMAT)` \|", text, flags=re.MULTILINE)
        self.assertGreater(len(codes), 100)
        result = self.run_lookup(*(f"-{code}" for code in codes))
        self.assertEqual(result.returncode, 0, result.stderr)
        for code in codes:
            self.assertEqual(result.stdout.count(f"## {code}\n"), 1, code)


if __name__ == "__main__":
    unittest.main()
