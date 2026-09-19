from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.deploy import _create_host_agents_if_missing

ROOT = Path(__file__).resolve().parents[1]


class TestHostAgentsContract(unittest.TestCase):
    def test_existing_host_agents_is_preserved_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            target = repo / "AGENTS.md"
            target.write_bytes(b"host-owned\n\x00stable")
            template = repo / "template.md"
            template.write_text("qg template\n", encoding="utf-8")
            before = target.read_bytes()
            self.assertFalse(_create_host_agents_if_missing(repo, template))
            self.assertEqual(target.read_bytes(), before)

    def test_missing_host_agents_is_created_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            template = repo / "template.md"
            template.write_text("qg template\n", encoding="utf-8")
            self.assertTrue(_create_host_agents_if_missing(repo, template))
            self.assertEqual((repo / "AGENTS.md").read_text(encoding="utf-8"), "qg template\n")

    def test_agents_creation_race_fails_instead_of_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            template = repo / "template.md"
            template.write_text("qg template\n", encoding="utf-8")
            with patch("runtime.deploy.os.open", side_effect=FileExistsError("race")):
                with self.assertRaises(FileExistsError):
                    _create_host_agents_if_missing(repo, template)

    def test_skill_and_references_define_host_owned_semantics(self) -> None:
        texts = {
            path: (ROOT / path).read_text(encoding="utf-8")
            for path in ("SKILL.md", "references/CODING_GUIDE.md", "references/AUDIT.md")
        }
        self.assertIn("逐字节保留", texts["SKILL.md"])
        self.assertIn("create-if-missing", texts["references/CODING_GUIDE.md"])
        self.assertNotIn("Profile 模板覆盖", texts["references/CODING_GUIDE.md"])
        self.assertNotIn("Profile 模板覆盖", texts["references/AUDIT.md"])

    def test_deploy_cli_has_one_profile_switch_and_no_ignore(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "runtime" / "deploy.py"), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
            env={**__import__('os').environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertIn("--profile", result.stdout)
        self.assertNotIn("--agents-profile", result.stdout)
        self.assertNotIn("--ignore", result.stdout)

    def test_skill_describes_stable_commands_and_version(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        lock = (ROOT / "runtime" / "RELEASE.lock").read_text(encoding="utf-8")
        self.assertIn("version=0.20.0", lock)
        for command in ("doc-generate", "doc-search", "audit", "verify"):
            self.assertIn(command, text)
        self.assertIn("Portable", text)
        self.assertIn("Installed", text)
        self.assertIn("QualityGuardProfile", text)


if __name__ == "__main__":
    unittest.main()
