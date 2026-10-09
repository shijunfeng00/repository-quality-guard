"""无 .git 与忽略 diff 具有相同的当前源码事实，但不同来源身份。"""

from __future__ import annotations

import shutil
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from runtime.src.cli import build_parser as build_scan_parser, scan_target
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.cpp_build_contract import (
    CompilerFamily,
    NativeSyntaxState,
    check_native_syntax,
    load_compilation_database,
)
from runtime.src.compact_report import (
    _debt_issues,
    render_compact_report,
    semantic_items,
    validate_compact_report,
)
from runtime.src.gate_status import code_status
from runtime.src.report_facts import build_report_facts
from runtime.src.workflow import (
    _resolve_audit_mode,
    _run_audit,
    _run_verify,
    build_parser,
)


class TestNoGitAuditMode(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.plain = Path(self.directory.name) / "plain"
        self.repo = Path(self.directory.name) / "repo"
        self.plain.mkdir()
        (self.plain / "model.py").write_text(
            "class Public:\n"
            "    def process(self, record: dict[str, int]) -> int:\n"
            "        return record.get('value', 0)\n",
            encoding="utf-8",
        )
        shutil.copytree(self.plain, self.repo)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "model.py"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )

    def scan(self, path: Path, mode: str = "normal"):
        args = build_scan_parser().parse_args([str(path), "--mode", mode])
        return scan_target(args)[1]

    def test_gitless_normal_equals_git_ignore_diff_current_facts(self) -> None:
        plain = self.scan(self.plain)
        git_ignored = self.scan(self.repo, "ignore-diff")

        def normalize(report):
            return sorted(
                (f.code, f.severity, f.path, f.symbol, f.message)
                for f in report.findings
                if not f.code.startswith(("QG98", "QG99"))
            )

        self.assertEqual(normalize(plain), normalize(git_ignored))
        self.assertEqual(code_status(plain), code_status(git_ignored))
        self.assertEqual(
            plain.relation_graph["current"], git_ignored.relation_graph["current"]
        )
        self.assertEqual(plain.relation_graph["base"], "N/A")
        self.assertIsNone(plain.baseline)
        self.assertIsNone(git_ignored.baseline)
        for mode, report in (("normal", plain), ("ignore-diff", git_ignored)):
            doc = render_compact_report(report, revision="", audit_mode=mode)
            self.assertIn(f"audit_mode: {mode}", doc)
            self.assertIn("baseline_revision: N/A", doc)
            self.assertIn("- Git 基线可用：NO", doc)
            self.assertIn("| WARNING | N/A |", doc) if any(
                f.severity == "warning" and not f.code.startswith("QG98")
                for f in report.findings
            ) else self.assertIn("| INFO | N/A |", doc)
            self.assertIn("- 本轮 Delta：N/A", doc)
            self.assertEqual(sum(item.delta for item in semantic_items(report)), 0)
            self.assertEqual(build_report_facts(report, "").quality_deltas, ())
            self.assertFalse(
                any(
                    "必须清零" in i
                    for i in _debt_issues(doc, build_report_facts(report, ""))
                )
            )

    def test_git_baseline_static_positive_delta_is_signed(self) -> None:
        """Git comparisons retain an explicit positive sign, unlike N/A snapshots."""
        from runtime.src.model import QualityBaseline

        report = self.scan(self.repo, "ignore-diff")
        report.baseline = QualityBaseline(
            revision="HEAD",
            files_scanned=1,
            definitions=0,
            documented_definitions=0,
            complete_docstrings=0,
            docstring_definitions=0,
            summary={
                severity: 0 for severity in ("critical", "error", "warning", "info")
            },
            by_rule={},
            by_file={},
        )
        report.baseline_error = ""
        # Force a positive count regardless of the fixture's independent rules.
        from runtime.src.model import Finding

        report.findings.append(
            Finding(
                code="QG026",
                severity="warning",
                confidence="low",
                path="model.py",
                line=1,
                column=1,
                message="测试正向静态增量数值格式",
            )
        )
        document = render_compact_report(report, revision="HEAD")
        self.assertIn("| WARNING | 0 |", document)
        self.assertIn("| WARNING | 0 | 2 | +2 |", document)

    def test_real_audit_writes_report_for_gitless_and_ignore_diff(self) -> None:
        """Persisted audit reports must not need .git or fabricate history."""
        reports = []
        for root, requested in ((self.plain, None), (self.repo, "ignore-diff")):
            output = Path(self.directory.name) / (root.name + "-audit.md")
            arguments = ["audit", str(root), "--report-output", str(output)]
            if requested is not None:
                arguments.extend(("--mode", requested))
            # The report initially requires human review, not an invalid scan.
            self.assertEqual(_run_audit(build_parser().parse_args(arguments)), 3)
            self.assertTrue(output.is_file())
            content = output.read_text(encoding="utf-8")
            self.assertIn("baseline_revision: N/A", content)
            self.assertIn("- Git 基线可用：NO", content)
            self.assertIn("- 本轮 Delta：N/A", content)
            self.assertIn("audit_mode: " + (requested or "normal"), content)
            self.assertIn("<!-- RQG:AUTO:BEGIN relation_impact -->", content)
            scan_report = self.scan(root, requested or "normal")
            actual_validation = validate_compact_report(
                content, scan_report, revision="", audit_mode=requested or "normal"
            )
            self.assertFalse(
                any(
                    "报告 Git baseline 已过期" in issue
                    for issue in actual_validation.issues
                )
            )
            reports.append(content)
        # Same source facts, different explicitly visible requested modes.
        self.assertIn("| INFO | N/A |", reports[0])
        self.assertIn("| INFO | N/A |", reports[1])

    def test_normal_git_still_uses_baseline(self) -> None:
        args = build_scan_parser().parse_args([str(self.repo), "--diff-base", "HEAD"])
        with patch.dict(os.environ, {"REPO_QUALITY_GUARD_RELEASE_SEAL": "test-seal"}):
            current = scan_target(args)[1]
        self.assertIsNotNone(current.baseline)
        self.assertTrue(current.comparison_mode)
        self.assertNotEqual(current.relation_graph.get("base"), "N/A")

    def test_cleanup_strict_gate_does_not_allow_review(self) -> None:
        current = self.scan(self.plain, "cleanup")
        self.assertIsNone(current.baseline)
        self.assertIn("current", current.relation_graph)
        self.assertEqual(
            code_status(current), code_status(self.scan(self.repo, "ignore-diff"))
        )

    @unittest.skipUnless(shutil.which("g++"), "GCC unavailable")
    def test_gitless_gcc_contract_matches_ignore_diff_and_reports_real_failure(
        self,
    ) -> None:
        """GCC build facts work without .git or Clang, including native FAIL evidence."""
        for root in (self.plain, self.repo):
            source = root / "syntax.cpp"
            source.write_text(
                "int syntax() { return missing_name; }\n", encoding="utf-8"
            )
            (root / "compile_commands.json").write_text(
                json.dumps(
                    [
                        {
                            "directory": str(root),
                            "file": str(source),
                            "arguments": ["g++", "-std=c++20", "-c", str(source)],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            snapshot = directory_analysis_snapshot(root, GuardConfig())
            specs = load_compilation_database(snapshot)
            self.assertEqual(specs["syntax.cpp"].family, CompilerFamily.GCC)
            result = check_native_syntax(specs["syntax.cpp"])
            self.assertEqual(result.state, NativeSyntaxState.FAIL)
            self.assertTrue(result.command[0].endswith("g++"))
            self.assertIn("missing_name", result.stderr)

        plain = self.scan(self.plain)
        ignored = self.scan(self.repo, "ignore-diff")
        self.assertEqual(plain.multilang_summary, ignored.multilang_summary)
        self.assertEqual(plain.multilang_summary["cpp_compiler_families"], {"gcc": 1})
        self.assertEqual(code_status(plain), code_status(ignored))

    def test_no_git_absolute_private_boundary_still_rejects(self) -> None:
        (self.plain / "private_owner.py").write_text(
            "class _PrivateClass:\n    pass\n", encoding="utf-8"
        )
        (self.plain / "consumer.py").write_text(
            "from private_owner import _PrivateClass\n", encoding="utf-8"
        )
        result = self.scan(self.plain)
        self.assertIn("QG149", {finding.code for finding in result.findings})
        self.assertEqual(code_status(result), "REJECT")

    def test_cleanup_verify_has_no_review_required_exit(self) -> None:
        report_file = self.plain / "report.md"
        report_file.write_text("fixture report", encoding="utf-8")
        options = build_parser().parse_args(
            [
                "verify",
                str(self.plain),
                "--mode",
                "cleanup",
                "--report-output",
                str(report_file),
            ]
        )
        report = self.scan(self.plain, "cleanup")
        mock_validation = SimpleNamespace(issues=(), semantic_status="ACCEPT")
        with (
            patch(
                "runtime.src.workflow._scan_for_workflow",
                return_value=(SimpleNamespace(root=self.plain), report, None, ""),
            ),
            patch(
                "runtime.src.workflow.validate_compact_report",
                return_value=mock_validation,
            ),
            patch("runtime.src.workflow.code_status", return_value="REVIEW_REQUIRED"),
        ):
            self.assertEqual(_run_verify(options), 1)

    def test_different_audit_verify_mode_rejected(self) -> None:
        report = self.scan(self.plain)
        body = render_compact_report(report, revision="", audit_mode="normal")
        result = validate_compact_report(
            body, report, revision="", audit_mode="ignore-diff"
        )
        self.assertTrue(any("审计模式" in x for x in result.issues))

    def test_conflicts_cannot_silently_ignore_git(self) -> None:
        parser = build_parser()
        options = parser.parse_args(["audit", str(self.repo)])
        options = _resolve_audit_mode(options)
        self.assertEqual(options.mode, "normal")
        for args in (
            ["audit", str(self.repo), "--mode", "ignore-diff", "--diff-base", "HEAD"],
            ["verify", str(self.repo), "--mode", "cleanup", "--staged"],
            [
                "audit",
                str(self.repo),
                "--mode",
                "ignore-diff",
                "--debt-mode",
                "cleanup",
            ],
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                _resolve_audit_mode(parser.parse_args(args))
