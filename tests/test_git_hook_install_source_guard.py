"""The consuming-project hook installer must reject the RQG source layout."""

import shutil
import subprocess
import sys
import tempfile
import weakref
from pathlib import Path

from rqgtest import TestCase


class GitHookInstallerSourceGuardCase(TestCase):
    """Invoke the real installer CLI against a minimal RQG-shaped source tree."""

    def get_enterpoint(self):
        return subprocess.run

    def get_input_data(self):
        project = Path(__file__).resolve().parents[1]
        target = Path(tempfile.mkdtemp(prefix="rqg-source-guard-"))
        (target / "SKILL.md").write_text("# source marker\n", encoding="utf-8")
        (target / "runtime").mkdir()
        (target / "runtime" / "RELEASE.lock").write_text(
            "schema=repository-quality-guard/release-v1\nversion=0.22.6\n",
            encoding="utf-8",
        )
        (target / "scripts").mkdir()
        (target / "scripts" / "verify_release.py").write_text(
            "# current release verifier marker\n", encoding="utf-8"
        )
        weakref.finalize(self, shutil.rmtree, target, ignore_errors=True)
        command = [
            sys.executable,
            "-B",
            str(project / "scripts" / "git_hook_install.py"),
            str(target),
        ]
        return ((command,), {
            "cwd": str(project),
            "text": True,
            "capture_output": True,
            "check": False,
            "timeout": 10,
        })

    def get_output_data(self):
        return {"returncode": 2, "stderr_contains": "目标是 RQG 源码仓库"}

    def equals(self, expected, actual):
        return (
            actual.returncode == expected["returncode"]
            and expected["stderr_contains"] in actual.stderr
            and actual.stdout == ""
        )
