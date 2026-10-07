from __future__ import annotations

import unittest
from pathlib import Path

from runtime.src.compact_report import _commit_issues
from runtime.src.model import ScanReport
from runtime.src.report_contract import _commit_findings

PROFILE_POLICY = {
    "subject_regex": r"^(?:fix|feature|perf|refactor|test|docs|chore|build|release|revert)(?:\([^)]+\))?:\s+.*[\u4e00-\u9fff].*$",
    "subject_example": "fix: 修复问题",
    "min_body_bullets": 2,
    "body_bullet_regex": r"^- .*[\u4e00-\u9fff].*$",
}


class TestCommitMessageContract(unittest.TestCase):
    def _text(self, subject: str, bullets: tuple[str, ...] | None = None) -> str:
        body = bullets or (
            "- 覆盖完整变更范围与长期仓库边界",
            "- 保持质量门禁与回归验证语义不变",
        )
        return (
            "## 9. 提交 Commit\n\n"
            "```bash\n"
            f'git commit -m "{subject}\n\n' + "\n".join(body) + '\n"\n'
            "```\n"
        )

    def _report(self, policy: dict[str, object] | None = None) -> ScanReport:
        kwargs = {} if policy is None else {"commit_policy": dict(policy)}
        return ScanReport(
            root=Path("."),
            files_scanned=0,
            findings=[],
            definitions=0,
            **kwargs,
        )

    def _assert_valid(self, text: str, policy: dict[str, object] | None = None) -> None:
        report = self._report(policy)
        self.assertEqual([], _commit_findings(text, report))
        self.assertEqual([], _commit_issues(text, report))

    def _assert_invalid(self, text: str, policy: dict[str, object]) -> None:
        report = self._report(policy)
        self.assertTrue(_commit_findings(text, report))
        self.assertTrue(_commit_issues(text, report))

    def test_profile_can_require_english_type_with_chinese_subject(self) -> None:
        for subject in (
            "fix: 统一提交信息语言契约",
            "feature: 增加提交契约能力",
            "perf: 优化提交契约校验",
            "refactor(parser): 收敛提交解析职责",
        ):
            with self.subTest(subject=subject):
                self._assert_valid(self._text(subject), PROFILE_POLICY)

    def test_profile_can_reject_feat_alias_and_chinese_type(self) -> None:
        for subject in ("feat: 增加提交契约能力", "修复：统一提交信息语言契约"):
            with self.subTest(subject=subject):
                self._assert_invalid(self._text(subject), PROFILE_POLICY)

    def test_generic_policy_does_not_impose_one_company_subject_style(self) -> None:
        self._assert_valid(self._text("feat: add capability"))
        self._assert_valid(self._text("[BUG-123] repair parser"))

    def test_profile_can_define_a_different_company_convention(self) -> None:
        policy = {
            "subject_regex": r"^ACME-\d+ \| (?:FIX|FEATURE) \| .+$",
            "subject_example": "ACME-123 | FIX | repair parser",
            "min_body_bullets": 0,
            "body_bullet_regex": "",
        }
        self._assert_valid(
            self._text("ACME-123 | FIX | repair parser", ()),
            policy,
        )
        self._assert_invalid(self._text("fix: 修复解析器"), policy)


if __name__ == "__main__":
    unittest.main()
