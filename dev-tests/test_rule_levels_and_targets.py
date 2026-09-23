from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.src.cli import _apply_profile_rule_policy, build_parser, resolve_target
from runtime.src.compact_report import _absolute_blocker_rows
from runtime.src.config import GuardConfig
from runtime.src.gate_status import code_status
from runtime.src.model import Finding, ScanReport
from runtime.src.project_profiles import load_quality_profile
from runtime.src.scan_snapshot import _relevant_worktree_paths, scan_target_cached
from runtime.src.scan_worker import _legacy_args, build_parser as build_worker_parser


class TestRuleLevelsAndTargets(unittest.TestCase):
    def _finding(self, code: str, severity: str = "info") -> Finding:
        return Finding(
            code=code,
            severity=severity,  # type: ignore[arg-type]
            confidence="high",
            path="src/example.py",
            line=1,
            column=1,
            message="example",
        )

    def _report(self, findings: list[Finding], *, baseline: object | None = None) -> ScanReport:
        return ScanReport(
            root=Path("."),
            files_scanned=1,
            findings=findings,
            definitions=0,
            baseline=baseline,  # type: ignore[arg-type]
        )

    def test_json_only_profile_can_define_rule_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile_dir = Path(temp)
            (profile_dir / "profile.json").write_text(
                json.dumps(
                    {
                        "schema": "repository-quality-guard/profile-v1",
                        "name": "json-only",
                        "rules": {"levels": {"QG203": "CRITICAL", "QG205": "SEMANTIC"}},
                    }
                ),
                encoding="utf-8",
            )

            profile = load_quality_profile(str(profile_dir))
            built = profile.build() if profile is not None else None

            self.assertIsNotNone(built)
            self.assertEqual(dict(built.rule_levels), {"QG203": "critical", "QG205": "semantic"})

    def test_semantic_level_moves_finding_out_of_static_quality_gate(self) -> None:
        report = self._report([self._finding("QG205", "error")], baseline=object())
        config = GuardConfig(profile_rule_levels=(("QG205", "semantic"),))

        _apply_profile_rule_policy(report, config)

        self.assertEqual(report.findings[0].severity, "info")
        self.assertTrue(report.findings[0].evidence.get("semantic_review_required"))
        self.assertEqual(code_status(report), "ACCEPT")

    def test_critical_remains_history_aware(self) -> None:
        report = self._report([self._finding("QG203", "critical")], baseline=object())

        self.assertEqual(code_status(report), "ACCEPT")

    def test_blocker_rejects_even_when_historical_baseline_exists(self) -> None:
        report = self._report([self._finding("QG205", "info")], baseline=object())
        config = GuardConfig(profile_rule_levels=(("QG205", "blocker"),))

        _apply_profile_rule_policy(report, config)

        self.assertEqual(report.findings[0].severity, "critical")
        self.assertTrue(report.findings[0].evidence.get("absolute_blocker"))
        self.assertEqual(code_status(report), "REJECT")

    def test_profile_blocker_is_visible_in_absolute_blocker_report(self) -> None:
        report = self._report([self._finding("QG205", "info")], baseline=object())
        config = GuardConfig(profile_rule_levels=(("QG205", "blocker"),))

        _apply_profile_rule_policy(report, config)
        rendered = "\n".join(_absolute_blocker_rows(report))

        self.assertIn("QG205", rendered)
        self.assertIn("profile-rule=BLOCKER", rendered)

    def test_non_git_directory_target_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = build_parser().parse_args([str(root)])

            target = resolve_target(args)

            self.assertFalse(target.git_enabled)
            self.assertEqual(target.root, root.resolve())

    def test_single_file_positional_target_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "example.toml"
            path.write_text("version='1.2.3'\n", encoding="utf-8")
            args = build_parser().parse_args([str(path)])

            target = resolve_target(args)

            self.assertEqual(target.root, path.parent.resolve())
            self.assertEqual(target.focus_files, frozenset({path.resolve()}))

    def test_worker_preserves_positional_single_file_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            focus = root / "focus.py"
            focus.write_text("VALUE = 1\n", encoding="utf-8")
            sibling = root / ("artifact_v" + "2.8-rc" + "3.txt")
            sibling.write_text("release evidence\n", encoding="utf-8")
            args = build_parser().parse_args([str(focus)])

            target, report, _interface, _revision, status = scan_target_cached(args)

            self.assertEqual(status, "BYPASS")
            self.assertEqual(target.focus_files, frozenset({focus.resolve()}))
            self.assertFalse(any(item.path == sibling.name for item in report.findings))
            self.assertFalse(any(item.code == "QG203" for item in report.findings))

    def test_worker_carries_resolved_profile_without_replaying_user_cli_selection(self) -> None:
        options = build_worker_parser().parse_args(
            [
                "--path",
                "/tmp/repo",
                "--output",
                "/tmp/out.pkl",
                "--resolved-profile-reference",
                "/tmp/release/installed/profile",
                "--resolved-profile-name",
                "geek-ai-agent",
                "--resolved-profile-source",
                "sealed-installed",
            ]
        )

        args = _legacy_args(options)

        self.assertIsNone(args.profile)
        self.assertEqual(
            args.resolved_profile_reference, "/tmp/release/installed/profile"
        )
        self.assertEqual(args.resolved_profile_name, "geek-ai-agent")
        self.assertEqual(args.resolved_profile_source, "sealed-installed")


    def test_external_profile_path_survives_worker_and_contract_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "target-repo"
            profile = root / "private-profile"
            repo.mkdir()
            profile.mkdir()
            (profile / "profile.json").write_text(
                json.dumps(
                    {
                        "schema": "repository-quality-guard/profile-v1",
                        "name": "private-profile",
                    }
                ),
                encoding="utf-8",
            )
            (repo / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess = __import__("subprocess")
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "add", "example.py"], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=RQG Test",
                    "-c",
                    "user.email=rqg-test@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "baseline",
                ],
                cwd=repo,
                check=True,
            )
            args = build_parser().parse_args(
                [str(repo), "--profile", str(profile), "--diff-base", "HEAD"]
            )

            with patch.dict(
                __import__("os").environ,
                {"REPO_QUALITY_GUARD_RELEASE_SEAL": "test-seal"},
            ):
                _target, report, _interface, _revision, _status = scan_target_cached(args)

            self.assertEqual(report.project_name, "private-profile")
            self.assertEqual(report.profile_source, "explicit-cli")

    def test_chinese_readme_participates_in_snapshot_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            __import__("subprocess").run(["git", "init", "-q"], cwd=root, check=True)
            (root / "README_zh.md").write_text("中文说明\n", encoding="utf-8")

            relevant = _relevant_worktree_paths(root, GuardConfig())

            self.assertIn("README_zh.md", relevant)

    def test_files_option_accepts_non_python_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text("version='1.2.3'\n", encoding="utf-8")
            args = build_parser().parse_args(["--files", str(path)])

            target = resolve_target(args)

            self.assertIn(path.resolve(), target.focus_files)


if __name__ == "__main__":
    unittest.main()
