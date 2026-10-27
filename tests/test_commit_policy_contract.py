"""Independently frozen commit-policy TP/TN behavior in production validators."""
from __future__ import annotations
import json
from pathlib import Path
from rqgtest import TestCase
from runtime.src.compact_report import _commit_issues
from runtime.src.report_contract import _commit_findings
from runtime.src.model import ScanReport

class CommitCompactPolicyCase(TestCase):
    def get_enterpoint(self):
        return _commit_issues

    def get_input_data(self):
        rows = json.loads(
            (Path(__file__).parent / "cases/commit-policy-contract.json").read_text(
                encoding="utf-8"
            )
        )["cases"]
        for row in rows:
            bullets = row.get("bullets", [
                "- 覆盖完整变更范围与长期仓库边界",
                "- 保持质量门禁与回归验证语义不变",
            ])
            message = ('## 9. 提交 Commit\n\n```bash\n'
                       + f'git commit -m "{row["subject"]}\n\n'
                       + "\n".join(bullets) + '\n"\n```\n')
            kwargs = {"commit_policy": dict(row["policy"])} if "policy" in row else {}
            report = ScanReport(root=Path("."), files_scanned=0, findings=[], definitions=0, **kwargs)
            yield ((message, report), {})

    def get_output_data(self):
        rows = json.loads(
            (Path(__file__).parent / "cases/commit-policy-contract.json").read_text(
                encoding="utf-8"
            )
        )["cases"]
        for row in rows:
            yield row["accepted"]

    def equals(self, expected, actual):
        return expected is (not bool(actual))


class CommitFullReportPolicyCase(TestCase):
    def get_enterpoint(self):
        return _commit_findings

    def get_input_data(self):
        rows = json.loads(
            (Path(__file__).parent / "cases/commit-policy-contract.json").read_text(
                encoding="utf-8"
            )
        )["cases"]
        for row in rows:
            bullets = row.get("bullets", [
                "- 覆盖完整变更范围与长期仓库边界",
                "- 保持质量门禁与回归验证语义不变",
            ])
            message = ('## 9. 提交 Commit\n\n```bash\n'
                       + f'git commit -m "{row["subject"]}\n\n'
                       + "\n".join(bullets) + '\n"\n```\n')
            kwargs = {"commit_policy": dict(row["policy"])} if "policy" in row else {}
            report = ScanReport(root=Path("."), files_scanned=0, findings=[], definitions=0, **kwargs)
            yield ((message, report), {})

    def get_output_data(self):
        rows = json.loads(
            (Path(__file__).parent / "cases/commit-policy-contract.json").read_text(
                encoding="utf-8"
            )
        )["cases"]
        for row in rows:
            yield row["accepted"]

    def equals(self, expected, actual):
        return expected is (not bool(actual))
