"""Official installed audit must preserve a selected, sealed Profile identity."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime.deploy import deploy

ROOT = Path(__file__).resolve().parents[1]


class TestInstalledProfileOfficialAudit(unittest.TestCase):
    """Check end-to-end Profile provenance, not just the selector's return value."""

    def test_explicit_installed_profile_in_real_audit_and_verify(self) -> None:
        """An explicit installed Profile never becomes generic in the actual report."""
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "unrelated-repository-name"
            repo.mkdir()
            (repo / "sample.py").write_text(
                'def read_key(hit: dict[str, str]) -> str:\n    return hit["_id"]\n',
                encoding="utf-8",
            )
            installed = repo / ".agents" / "skills" / "repository-quality-guard"
            deploy(ROOT, installed, profile_reference="qg-example-profile")
            cli = installed / "scripts" / "quality_guard.py"
            command = [
                sys.executable,
                str(cli),
                "audit",
                str(repo),
                "--profile",
                "qg-example-profile",
                "--mode",
                "ignore-diff",
            ]
            audit = subprocess.run(
                command,
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            self.assertEqual(audit.returncode, 3, audit.stdout + audit.stderr)
            report = (repo / "修改说明.md").read_bytes()
            self.assertIn("source=sealed-installed-explicit", audit.stdout)
            self.assertIn("- Profile：`qg-example-profile`".encode(), report)
            self.assertIn(
                "- Profile 来源：`sealed-installed-explicit`".encode(), report
            )
            digest = hashlib.sha256(report).hexdigest()
            verify = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "verify",
                    str(repo),
                    "--profile",
                    "qg-example-profile",
                    "--mode",
                    "ignore-diff",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            self.assertEqual(verify.returncode, 3, verify.stdout + verify.stderr)
            self.assertEqual(
                hashlib.sha256((repo / "修改说明.md").read_bytes()).hexdigest(), digest
            )
            wrong = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "audit",
                    str(repo),
                    "--profile",
                    "non-authorized-profile",
                    "--mode",
                    "ignore-diff",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            self.assertNotEqual(wrong.returncode, 0)
            self.assertIn("not", wrong.stdout + wrong.stderr)
            self.assertEqual(
                hashlib.sha256((repo / "修改说明.md").read_bytes()).hexdigest(), digest
            )
