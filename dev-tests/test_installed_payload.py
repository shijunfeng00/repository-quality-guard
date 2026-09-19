from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from runtime.deploy import _write_installed_policy


class TestInstalledProfilePayload(unittest.TestCase):
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
            self.assertFalse((installed / "tests").exists())


if __name__ == "__main__":
    unittest.main()
