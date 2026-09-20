from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from runtime.deploy import _copy_agents_tree, _write_installed_policy
from runtime.src import integrity


class TestInstalledProfilePayload(unittest.TestCase):

    def test_bilingual_source_readmes_are_not_copied_to_installed_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "README.md").write_text("English docs\n", encoding="utf-8")
            (bundle / "README_zh.md").write_text("中文文档\n", encoding="utf-8")
            (bundle / "SKILL.md").write_text("skill\n", encoding="utf-8")
            (bundle / "scripts").mkdir()
            (bundle / "scripts" / "quality_guard.py").write_text("pass\n", encoding="utf-8")
            (bundle / "scripts" / "git_hook_install.py").write_text("pass\n", encoding="utf-8")
            (bundle / "runtime" / "src").mkdir(parents=True)
            (bundle / "runtime" / "install_dependencies.py").write_text("pass\n", encoding="utf-8")
            (bundle / "runtime" / "dependencies.lock.json").write_text("{}\n", encoding="utf-8")
            for name in ("workflow.py", "profile_api.py", "integrity.py"):
                (bundle / "runtime" / "src" / name).write_text("pass\n", encoding="utf-8")

            installed = root / "installed"
            _copy_agents_tree(bundle, installed)

            self.assertFalse((installed / "README.md").exists())
            self.assertFalse((installed / "README_zh.md").exists())


    def test_installed_profile_mutation_breaks_release_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "installed-skill"
            (root / "runtime").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "installed" / "profile").mkdir(parents=True)
            (root / "SKILL.md").write_text("skill\n", encoding="utf-8")
            (root / "runtime" / "RELEASE.lock").write_text(
                "schema=repository-quality-guard/release-v1\n"
                "version=1\n"
                "distribution=agents\n"
                "manifest_sha256=" + "0" * 64 + "\n"
                "protected_file_count=0\n",
                encoding="utf-8",
            )
            (root / "scripts" / "quality_guard.py").write_text(
                'RELEASE_SEAL = "' + "0" * 64 + '"\n',
                encoding="utf-8",
            )
            profile = root / "installed" / "profile" / "profile.json"
            profile.write_text('{"name":"sample"}\n', encoding="utf-8")

            seal = integrity.seal_release_tree(root, "agents")
            result = integrity.verify_release_integrity(
                {
                    integrity.RELEASE_HOME_ENV: str(root),
                    integrity.RELEASE_SEAL_ENV: seal,
                }
            )
            self.assertTrue(result.passed, result.issues)

            profile.write_text('{"name":"tampered"}\n', encoding="utf-8")
            tampered = integrity.verify_release_integrity(
                {
                    integrity.RELEASE_HOME_ENV: str(root),
                    integrity.RELEASE_SEAL_ENV: seal,
                }
            )
            self.assertFalse(tampered.passed)
            self.assertTrue(
                any("installed/profile/profile.json" in issue for issue in tampered.issues)
            )

    def test_authoring_assets_are_not_copied_into_installed_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "bundle"
            source = bundle / "profiles" / "sample"
            source.mkdir(parents=True)
            (source / "profile.json").write_text(
                json.dumps({"schema": "repository-quality-guard/profile-v1", "name": "sample", "entrypoint": "extension.py", "agents_file": "AGENTS.md"}),
                encoding="utf-8",
            )
            (source / "extension.py").write_text("VALUE = 1\n", encoding="utf-8")
            (source / "PROFILE.lock").write_text("{}\n", encoding="utf-8")
            (source / "AGENTS.md").write_text("profile instructions\n", encoding="utf-8")
            (source / "README.md").write_text("authoring docs\n", encoding="utf-8")
            (source / "README_zh.md").write_text("中文 authoring docs\n", encoding="utf-8")
            (source / "tests").mkdir()
            (source / "tests" / "test_profile.py").write_text("def test_x(): assert True\n", encoding="utf-8")

            staging = root / "staging"
            staging.mkdir()
            selection = SimpleNamespace(reference="sample", source="explicit-cli")
            profile = SimpleNamespace(
                name="sample",
                version="1",
                disabled_rules=(),
                capabilities=(),
            )
            _write_installed_policy(bundle, staging, selection, profile)

            installed = staging / "installed" / "profile"
            self.assertTrue((installed / "profile.json").is_file())
            self.assertTrue((installed / "extension.py").is_file())
            self.assertTrue((installed / "PROFILE.lock").is_file())
            self.assertFalse((installed / "AGENTS.md").exists())
            self.assertFalse((installed / "README.md").exists())
            self.assertFalse((installed / "README_zh.md").exists())
            self.assertFalse((installed / "tests").exists())


if __name__ == "__main__":
    unittest.main()
