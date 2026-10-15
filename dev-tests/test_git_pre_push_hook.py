"""Managed Git pre-push integration tests using a local bare remote (no network)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "git_hook_install.py"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        text=True,
        capture_output=True,
    )


class TestManagedPrePush(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        work = Path(self.temp.name)
        self.repo = work / "repo"
        self.remote = work / "remote.git"
        _git(work, "init", "-q", "--bare", str(self.remote))
        _git(work, "init", "-q", "-b", "main", str(self.repo))
        _git(self.repo, "config", "user.name", "Fixture User")
        _git(self.repo, "config", "user.email", "fixture@example.invalid")
        # Stub only the external quality executable, not the hook itself.
        qg = (
            self.repo
            / ".agents/skills/repository-quality-guard/scripts/quality_guard.py"
        )
        qg.parent.mkdir(parents=True)
        qg.write_text(
            "import os, sys\n"
            "with open(os.environ['RQG_PREPUSH_TEST_RECORD'], 'a') as output:\n"
            "    output.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(int(os.environ['RQG_PREPUSH_TEST_EXIT']))\n",
            encoding="utf-8",
        )
        (self.repo / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "feature: 初始测试提交")
        _git(self.repo, "remote", "add", "origin", str(self.remote))
        self.record = work / "calls.txt"

    def _install(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALLER), str(self.repo)],
            text=True,
            capture_output=True,
        )

    def _push(self, result: int, mode: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo), "push", "origin", "HEAD:refs/heads/main"],
            env={
                **os.environ,
                "RQG_PREPUSH_TEST_RECORD": str(self.record),
                "RQG_PREPUSH_TEST_EXIT": str(result),
                "RQG_PREPUSH_AUDIT_MODE": mode or "",
            },
            text=True,
            capture_output=True,
        )

    def test_install_is_idempotent_and_executable(self) -> None:
        self.assertEqual(self._install().returncode, 0)
        self.assertEqual(self._install().returncode, 0)
        hook = self.repo / ".git/hooks/pre-push"
        self.assertTrue(hook.is_file())
        self.assertTrue(os.access(hook, os.X_OK))

    def test_existing_nonexecutable_hook_is_repaired(self) -> None:
        self.assertEqual(self._install().returncode, 0)
        hook = self.repo / ".git/hooks/pre-push"
        hook.chmod(0o644)
        self.assertFalse(os.access(hook, os.X_OK))
        self.assertEqual(self._install().returncode, 0)
        self.assertTrue(os.access(hook, os.X_OK))
        self.assertNotEqual(self._push(3).returncode, 0)
        self.assertIn("verify", self.record.read_text(encoding="utf-8"))

    def test_explicit_ignore_diff_requires_matching_report_and_real_verify(self) -> None:
        self.assertEqual(self._install().returncode, 0)
        report = self.repo / "修改说明.md"
        # The opt-in is not allowed to silently reinterpret a normal-mode report.
        self.assertNotEqual(self._push(0, mode="ignore-diff").returncode, 0)
        self.assertFalse(self.record.exists())
        report.write_text("audit_mode: ignore-diff # 忽略diff模式\n", encoding="utf-8")
        push = self._push(5, mode="ignore-diff")
        self.assertEqual(push.returncode, 0, push.stderr)
        self.assertIn("verify", self.record.read_text(encoding="utf-8"))
        self.assertIn("--mode ignore-diff", self.record.read_text(encoding="utf-8"))
        (self.repo / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "fix: 更新受测值")
        self.assertNotEqual(self._push(3, mode="ignore-diff").returncode, 0)
        self.assertNotEqual(self._push(0, mode="bogus").returncode, 0)

    def test_other_hooks_are_not_overwritten(self) -> None:
        hook = self.repo / ".git/hooks/pre-push"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        result = self._install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(hook.read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")

    def test_accept_and_review_both_permit_real_push(self) -> None:
        self.assertEqual(self._install().returncode, 0)
        self.assertEqual(self._push(5).returncode, 0)
        self.assertIn("verify", self.record.read_text(encoding="utf-8"))
        (self.repo / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "fix: 更新测试内容")
        self.assertEqual(self._push(0).returncode, 0)
        self.assertEqual(len(self.record.read_text(encoding="utf-8").splitlines()), 2)

    def test_reject_and_invalid_report_block_push(self) -> None:
        self.assertEqual(self._install().returncode, 0)
        for code in (1, 2, 3, 4):
            with self.subTest(code=code):
                push = self._push(code)
                self.assertNotEqual(push.returncode, 0, push.stderr)
                self.assertIn("拒绝 push", push.stderr)
                self.assertNotEqual(
                    _git(
                        self.remote,
                        "show-ref",
                        "--verify",
                        "refs/heads/main",
                        check=False,
                    ).returncode,
                    0,
                )

    def test_uncommitted_tracked_edits_block_push(self) -> None:
        self.assertEqual(self._install().returncode, 0)
        (self.repo / "source.py").write_text("VALUE = 5\n", encoding="utf-8")
        push = self._push(0)
        self.assertNotEqual(push.returncode, 0)
        self.assertIn("未提交 tracked", push.stderr)

    def test_own_source_checkout_uses_portable_cli(self) -> None:
        qg = (
            self.repo
            / ".agents/skills/repository-quality-guard/scripts/quality_guard.py"
        )
        qg.unlink()
        own_qg = self.repo / "scripts/quality_guard.py"
        own_qg.parent.mkdir(parents=True, exist_ok=True)
        own_qg.write_text(
            "import os, sys\n"
            "with open(os.environ['RQG_PREPUSH_TEST_RECORD'], 'a') as output:\n"
            "    output.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(int(os.environ['RQG_PREPUSH_TEST_EXIT']))\n",
            encoding="utf-8",
        )
        (self.repo / "SKILL.md").write_text("fixture\n", encoding="utf-8")
        (self.repo / "runtime").mkdir()
        (self.repo / "runtime/RELEASE.lock").write_text(
            "version=test\n", encoding="utf-8"
        )
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "fix: 校验可移植根目录入口")
        self.assertEqual(self._install().returncode, 0)
        result = self._push(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verify", self.record.read_text(encoding="utf-8"))

    def test_installer_refuses_non_git_directory(self) -> None:
        bare = Path(self.temp.name) / "ordinary"
        bare.mkdir()
        result = subprocess.run(
            [sys.executable, str(INSTALLER), str(bare)],
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((bare / ".git/hooks/pre-push").exists())


if __name__ == "__main__":
    unittest.main()
