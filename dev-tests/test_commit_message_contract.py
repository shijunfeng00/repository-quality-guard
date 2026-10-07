from __future__ import annotations

import unittest

from runtime.src.compact_report import _commit_issues
from runtime.src.report_contract import _commit_findings


class TestCommitMessageContract(unittest.TestCase):
    def _report(self, subject: str) -> str:
        return (
            "## 9. 提交 Commit\n\n"
            "```bash\n"
            f'git commit -m "{subject}\n\n'
            "- 覆盖完整变更范围与长期仓库边界\n"
            "- 保持质量门禁与回归验证语义不变\n"
            '"\n'
            "```\n"
        )

    def test_english_type_with_chinese_subject_is_accepted(self) -> None:
        for subject in (
            "fix: 统一提交信息语言契约",
            "feature: 增加提交契约能力",
            "perf: 优化提交契约校验",
            "refactor(parser): 收敛提交解析职责",
        ):
            with self.subTest(subject=subject):
                text = self._report(subject)
                self.assertEqual([], _commit_findings(text))
                self.assertEqual([], _commit_issues(text))

    def test_feat_alias_is_rejected(self) -> None:
        text = self._report("feat: 增加提交契约能力")
        self.assertTrue(_commit_findings(text))
        self.assertTrue(_commit_issues(text))

    def test_chinese_type_is_rejected(self) -> None:
        text = self._report("修复：统一提交信息语言契约")
        self.assertTrue(_commit_findings(text))
        self.assertTrue(_commit_issues(text))


if __name__ == "__main__":
    unittest.main()
