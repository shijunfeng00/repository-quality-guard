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

    def test_chinese_commit_category_is_accepted(self) -> None:
        text = self._report("修复：统一提交信息语言契约")
        self.assertEqual([], _commit_findings(text))
        self.assertEqual([], _commit_issues(text))

    def test_english_conventional_prefix_is_rejected(self) -> None:
        text = self._report("fix: 统一提交信息语言契约")
        self.assertTrue(_commit_findings(text))
        self.assertTrue(_commit_issues(text))


if __name__ == "__main__":
    unittest.main()
