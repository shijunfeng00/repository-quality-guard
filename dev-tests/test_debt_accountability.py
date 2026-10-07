from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from runtime.src.compact_report import (
    _debt_issues,
    render_compact_report,
)
from runtime.src.model import Finding, QualityBaseline, ScanReport
from runtime.src.report_contract import build_report_facts
from runtime.src.report_facts import (
    current_quality_findings,
    current_test_quality_findings,
)
from runtime.src.workflow import build_parser


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


class TestDebtAccountability(unittest.TestCase):
    def _repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        _git(root, "init", "-q")
        _git(root, "config", "user.name", "QG Test")
        _git(root, "config", "user.email", "qg@example.invalid")
        for name in ("touched.py", "untouched.py"):
            (root / name).write_text("VALUE = 1\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "base")
        return temp, root

    def _finding(
        self,
        path: str,
        code: str,
        *,
        message: str | None = None,
        evidence: dict[str, object] | None = None,
    ) -> Finding:
        return Finding(
            code=code,
            severity="warning",
            confidence="high",
            path=path,
            line=1,
            column=1,
            symbol="owner",
            message=message or f"{code} debt in {path}",
            suggestion="fix it",
            evidence={} if evidence is None else evidence,
        )

    def _baseline(self, *findings: Finding) -> QualityBaseline:
        count = len(findings)
        return QualityBaseline(
            revision="HEAD",
            files_scanned=2,
            definitions=0,
            documented_definitions=0,
            complete_docstrings=0,
            docstring_definitions=0,
            summary={"critical": 0, "error": 0, "warning": count, "info": 0},
            by_rule={},
            by_file={},
            finding_severities={item.fingerprint: item.severity for item in findings},
        )

    def _report(
        self,
        root: Path,
        findings: list[Finding],
        baseline: QualityBaseline | None,
    ) -> ScanReport:
        return ScanReport(
            root=root,
            files_scanned=2,
            findings=findings,
            definitions=0,
            baseline=baseline,
            comparison_target="WORKTREE",
        )

    def test_progressive_requires_all_touched_history_but_not_untouched_history(
        self,
    ) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        touched = self._finding("touched.py", "QG101")
        untouched = self._finding("untouched.py", "QG102")
        (root / "touched.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = self._report(
            root, [touched, untouched], self._baseline(touched, untouched)
        )

        facts = build_report_facts(report, "HEAD", debt_mode="progressive")

        self.assertEqual(
            [(item.finding.code, item.scope) for item in facts.historical_debt],
            [("QG101", "TOUCHED"), ("QG102", "UNTOUCHED")],
        )
        text = render_compact_report(report, revision="HEAD", debt_mode="progressive")
        touched_id = next(
            item.item_id for item in facts.historical_debt if item.scope == "TOUCHED"
        )
        self.assertIn(f"| {touched_id} | PENDING |", text)
        self.assertIn("`QG102`", text)
        self.assertNotIn("Historical 风险抽样", text)

    def test_touched_debt_requires_concrete_deferral_and_survives_regeneration(
        self,
    ) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        touched = self._finding("touched.py", "QG101")
        (root / "touched.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = self._report(root, [touched], self._baseline(touched))
        facts = build_report_facts(report, "HEAD", debt_mode="progressive")
        debt_id = facts.historical_debt[0].item_id
        text = render_compact_report(report, revision="HEAD", debt_mode="progressive")

        self.assertTrue(_debt_issues(text, facts))
        filled = text.replace(
            f"| {debt_id} | PENDING | PENDING | PENDING | PENDING |",
            f"| {debt_id} | DEFERRED | 修复该 owner 需要同步重构未在本次需求中的序列化调用链 | "
            "至少会新增 serializer.py 与两个消费方的非预期修改 | "
            "单独建立序列化重构任务并加入对应回归测试后关闭 |",
        )
        self.assertEqual(_debt_issues(filled, facts), [])

        regenerated = render_compact_report(
            report,
            revision="HEAD",
            existing=filled,
            debt_mode="progressive",
        )
        self.assertIn(
            f"| {debt_id} | DEFERRED | 修复该 owner 需要同步重构未在本次需求中的序列化调用链 |",
            regenerated,
        )

    def test_generic_historical_reason_is_not_sufficient(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        touched = self._finding("touched.py", "QG101")
        (root / "touched.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = self._report(root, [touched], self._baseline(touched))
        facts = build_report_facts(report, "HEAD", debt_mode="progressive")
        debt_id = facts.historical_debt[0].item_id
        text = render_compact_report(report, revision="HEAD", debt_mode="progressive")
        filled = text.replace(
            f"| {debt_id} | PENDING | PENDING | PENDING | PENDING |",
            f"| {debt_id} | DEFERRED | 历史债务 | "
            "修复会扩大到多个当前需求没有触及的生产 owner | "
            "建立独立重构任务并补齐调用方回归后关闭 |",
        )

        issues = _debt_issues(filled, facts)

        self.assertTrue(any("不能仅用" in issue for issue in issues))

    def test_cleanup_mode_requires_all_selected_history_to_disappear(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        touched = self._finding("touched.py", "QG101")
        untouched = self._finding("untouched.py", "QG102")
        (root / "touched.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = self._report(
            root, [touched, untouched], self._baseline(touched, untouched)
        )
        facts = build_report_facts(report, "HEAD", debt_mode="cleanup")
        text = render_compact_report(report, revision="HEAD", debt_mode="cleanup")

        issues = _debt_issues(text, facts)

        self.assertEqual(len(facts.historical_debt), 2)
        self.assertTrue(any("cleanup 模式" in issue for issue in issues))
        self.assertIn("cleanup-required remaining：2", text)
        self.assertNotIn(
            "| DEBT-", text.split("| DEBT | Decision", 1)[-1].split("###", 1)[0]
        )

    def test_no_baseline_treats_selected_scope_as_cleanup_responsibility(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        debt = self._finding("selected.py", "QG101")
        report = self._report(root, [debt], None)

        facts = build_report_facts(report, "HEAD", debt_mode="progressive")
        text = render_compact_report(report, revision="HEAD", debt_mode="progressive")

        self.assertEqual(facts.historical_debt[0].scope, "SELECTED_SCOPE")
        self.assertTrue(
            any("无 Git baseline" in issue for issue in _debt_issues(text, facts))
        )
        self.assertIn("no-baseline selected-scope remaining：1", text)

    def test_fixing_touched_history_removes_obligation_and_counts_paydown(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        touched = self._finding("touched.py", "QG101")
        untouched = self._finding("untouched.py", "QG102")
        (root / "touched.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = self._report(root, [untouched], self._baseline(touched, untouched))

        facts = build_report_facts(report, "HEAD", debt_mode="progressive")

        self.assertEqual(facts.legacy_debt_reduced, 1)
        self.assertFalse(any(item.scope == "TOUCHED" for item in facts.historical_debt))
        self.assertEqual(
            _debt_issues(
                render_compact_report(report, revision="HEAD", debt_mode="progressive"),
                facts,
            ),
            [],
        )

    def test_new_debt_remains_delta_not_historical(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        historical = self._finding("untouched.py", "QG102")
        introduced = self._finding("touched.py", "QG103")
        (root / "touched.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = self._report(
            root, [historical, introduced], self._baseline(historical)
        )

        facts = build_report_facts(report, "HEAD", debt_mode="progressive")

        self.assertEqual(
            [item.finding.code for item in facts.quality_deltas], ["QG103"]
        )
        self.assertEqual(
            [item.finding.code for item in facts.historical_debt], ["QG102"]
        )

    def test_qg179_exempt_only_deduplicates_gate_and_remains_ordinary_debt(
        self,
    ) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        warning = self._finding(
            "untouched.py",
            "QG020",
            evidence={"qg179_exempt": True},
        )
        report = self._report(root, [warning], self._baseline(warning))

        facts = build_report_facts(report, "HEAD", debt_mode="progressive")

        self.assertEqual(set(current_quality_findings(report)), {warning.fingerprint})
        self.assertEqual(facts.legacy_debt_reduced, 0)
        self.assertEqual(
            [item.finding.code for item in facts.historical_debt], ["QG020"]
        )

    def test_qg179_exempt_test_finding_remains_in_test_debt_inventory(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        warning = self._finding(
            "tests/test_sample.py",
            "QG020",
            evidence={"qg179_exempt": True},
        )
        report = self._report(root, [], None)
        report.test_findings = [warning]

        self.assertEqual(
            set(current_test_quality_findings(report)),
            {warning.fingerprint},
        )

    def test_workflow_cli_defaults_progressive_and_accepts_cleanup(self) -> None:
        parser = build_parser()

        default = parser.parse_args(["audit", "."])
        cleanup = parser.parse_args(["audit", ".", "--debt-mode", "cleanup"])

        self.assertEqual(default.debt_mode, "progressive")
        self.assertEqual(cleanup.debt_mode, "cleanup")
