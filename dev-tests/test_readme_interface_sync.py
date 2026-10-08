"""README synchronization must match an exported API change, not a file-touch proxy."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime.src.cli import _readme_sync_finding

ROOT = Path(__file__).resolve().parents[1]


def _change(qualname: str):
    symbol = SimpleNamespace(qualname=qualname, line=5)
    return SimpleNamespace(
        change="added",
        kind="function",
        path="runtime/src/example.py",
        symbol=qualname,
        before=None,
        after=symbol,
    )


class TestReadmeInterfaceSync(unittest.TestCase):
    def _finding(self, changes: tuple, readme_patch: str = ""):
        target = SimpleNamespace(root=ROOT, git_enabled=True)
        args = argparse.Namespace(staged=False, diff_base="HEAD")
        responses = [
            SimpleNamespace(returncode=0, stdout="README.md\n"),
            SimpleNamespace(returncode=0, stdout=""),
            SimpleNamespace(returncode=0, stdout=readme_patch),
        ]
        with patch("runtime.src.cli.run_readonly_git", side_effect=responses) as run:
            result = _readme_sync_finding(
                target, args, SimpleNamespace(changes=changes), "qg-example-profile"
            )
        self.assertEqual(run.call_count, 2 if not changes else 3)
        return result

    def test_unrelated_readme_edit_does_not_silence_public_api_change(self):
        finding = self._finding(
            (_change("public_api"),), "+new heading unrelated to API\n"
        )
        self.assertIsNotNone(finding)
        self.assertEqual(finding.code, "QG161")
        self.assertEqual(
            finding.evidence["public_changes"],
            ["added:function:runtime/src/example.py:public_api"],
        )

    def test_matching_readme_api_update_satisfies_gate(self):
        self.assertIsNone(
            self._finding(
                (_change("public_api"),), "+`public_api` adds validated behavior\n"
            )
        )

    def test_private_class_member_is_not_exported_public_surface(self):
        target = SimpleNamespace(root=ROOT, git_enabled=True)
        args = argparse.Namespace(staged=False, diff_base="HEAD")
        with patch("runtime.src.cli.run_readonly_git") as git:
            self.assertIsNone(
                _readme_sync_finding(
                    target,
                    args,
                    SimpleNamespace(changes=(_change("_PrivateClass.public_field"),)),
                    "qg-example-profile",
                )
            )
            git.assert_not_called()

    def test_standalone_bootstrap_help_imports_without_pythonpath(self):
        with tempfile.TemporaryDirectory() as outside:
            process = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "runtime" / "install_dependencies.py"),
                    "--help",
                ],
                cwd=outside,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn("--offline", process.stdout)


if __name__ == "__main__":
    unittest.main()
