from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime.deploy import _agents_source, _create_host_agents_if_missing

ROOT = Path(__file__).resolve().parents[1]


class TestHostAgentsContract(unittest.TestCase):
    def test_profile_agents_file_is_selected_from_profile_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            profile_dir = bundle / "profiles" / "sample"
            profile_dir.mkdir(parents=True)
            expected = profile_dir / "AGENTS.md"
            expected.write_text("profile instructions\n", encoding="utf-8")
            selection = SimpleNamespace(reference="sample")
            profile = SimpleNamespace(agents_file="AGENTS.md")
            self.assertEqual(_agents_source(bundle, selection, profile), expected.resolve())

    def test_generic_agents_source_uses_bootstrap_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            templates = bundle / "templates"
            templates.mkdir(parents=True)
            expected = templates / "AGENTS.template.md"
            expected.write_text("generic instructions\n", encoding="utf-8")
            selection = SimpleNamespace(reference="")
            self.assertEqual(_agents_source(bundle, selection, None), expected)

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


    def test_bilingual_readmes_present_human_facing_project_overview(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_zh = (ROOT / "README_zh.md").read_text(encoding="utf-8")
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

        self.assertIn("[简体中文](README_zh.md)", readme)
        self.assertIn("[English](README.md)", readme_zh)
        for heading in (
            "# Language support",
            "## What Repository Quality Guard changes",
            "# Quick start",
            "# How RQG protects the quality of Agent-written code",
            "# `修改说明.md`: the delivery answer sheet an Agent cannot skip",
            "# Project customization: make RQG fit your repository",
        ):
            self.assertIn(heading, readme)
        for heading in (
            "# 支持语言",
            "## 这正是 Repository Quality Guard 要解决的问题",
            "# 快速开始",
            "# RQG 如何确保 Agent 产出的代码质量",
            "# `修改说明.md`：Agent 不能跳过的交付答卷",
            "# 项目定制：让 RQG 适配你的仓库",
        ):
            self.assertIn(heading, readme_zh)
        self.assertIn("```mermaid", readme)
        self.assertIn("```mermaid", readme_zh)
        self.assertIn("project **Profile**", readme)
        self.assertIn("项目增加一个 **Profile**", readme_zh)
        self.assertIn("semantic review", readme)
        self.assertIn("语义审计", readme_zh)
        self.assertIn("qg-example-profile", readme)
        self.assertIn("qg-example-profile", readme_zh)
        self.assertIn("REVIEW_REQUIRED", readme)
        self.assertIn("REVIEW_REQUIRED", readme_zh)
        self.assertIn("Git delivery quality gate", readme)
        self.assertIn("Git 交付质量门禁", readme_zh)
        self.assertIn("git_hook_install.py", readme)
        self.assertIn("git_hook_install.py", readme_zh)
        self.assertIn("human confirmation", readme)
        self.assertIn("人工确认", readme_zh)
        self.assertIn("doc-generate", readme)
        self.assertIn("doc-search", readme)
        self.assertIn("doc-generate", readme_zh)
        self.assertIn("doc-search", readme_zh)
        self.assertIn("/profiles/*", ignore)
        self.assertIn("!/profiles/qg-example-profile/", ignore)
        self.assertIn("!/profiles/qg-example-profile/**", ignore)

    def test_managed_pre_push_gate_is_part_of_agent_contract(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        agents = (ROOT / "templates" / "AGENTS.template.md").read_text(
            encoding="utf-8"
        )
        for text in (skill, agents):
            self.assertIn("pre-push", text)
            self.assertIn("git_hook_install.py", text)
            self.assertIn("--no-verify", text)

    def test_rule_tables_have_markdown_headers(self) -> None:
        lines = (ROOT / "references" / "RULES.md").read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(lines):
            if not line.startswith("| `QG"):
                continue
            if index > 0 and lines[index - 1].startswith("| `QG"):
                continue
            self.assertGreaterEqual(index, 2)
            self.assertEqual("| 规则 | 检查内容 |", lines[index - 2])
            self.assertEqual("|---|---|", lines[index - 1])

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

    def test_skill_frontmatter_matches_agent_skills_spec(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        frontmatter = text.split("---\n", 2)[1]
        top_level = {
            line.split(":", 1)[0]
            for line in frontmatter.splitlines()
            if line and not line[0].isspace() and ":" in line
        }
        allowed = {
            "name",
            "description",
            "license",
            "compatibility",
            "metadata",
            "allowed-tools",
        }
        self.assertLessEqual(top_level, allowed)
        self.assertIn('  version: "0.20.4"', frontmatter)
        self.assertIn("  status: stable", frontmatter)

    def test_skill_describes_stable_commands_and_version(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        lock = (ROOT / "runtime" / "RELEASE.lock").read_text(encoding="utf-8")
        self.assertIn("version=0.20.4", lock)
        for command in ("doc-generate", "doc-search", "audit", "verify"):
            self.assertIn(command, text)
        self.assertIn("Portable", text)
        self.assertIn("Installed", text)
        self.assertIn("QualityGuardProfile", text)


if __name__ == "__main__":
    unittest.main()
