from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestReleaseBuild(unittest.TestCase):
    def _build(self, output: Path, *extra: str) -> None:
        subprocess.run(
            [sys.executable, str(ROOT / "tools" / "build_release.py"), str(ROOT), str(output), *extra],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_builder_can_import_integrity_on_current_python(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "one.zip"
            self._build(out)
            self.assertTrue(out.is_file())

    def test_public_release_is_byte_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / "a.zip", Path(temp) / "b.zip"
            self._build(a)
            self._build(b)
            self.assertEqual(_sha(a), _sha(b))
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_public_release_excludes_private_profiles_and_source_only_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "public.zip"
            self._build(out)
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
                joined = "\n".join(sorted(names))
                self.assertNotIn("profiles/", joined)
                self.assertFalse(any("dev-tests/" in name for name in names))
                self.assertFalse(any("tools/" in name for name in names))
                self.assertFalse(any("profiles/" in name for name in names))
                self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))

    def test_internal_release_includes_private_profiles_without_caches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "internal.zip"
            self._build(out, "--internal")
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
                private_names = sorted(
                    item.name
                    for item in (ROOT / "profiles").iterdir()
                    if item.is_dir() and (item / "profile.json").is_file()
                )
                self.assertTrue(private_names)
                for private_name in private_names:
                    prefix = f"repository-quality-guard/profiles/{private_name}/"
                    self.assertIn(prefix + "profile.json", names)
                    self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
                    self.assertNotIn(prefix + "AGENTS.md", names)
                    template = ROOT / "profiles" / private_name / "AGENTS.template.md"
                    if template.is_file():
                        self.assertIn(prefix + "AGENTS.template.md", names)

    def test_public_release_manifest_does_not_leak_private_profile_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "public.zip"
            self._build(out)
            with zipfile.ZipFile(out) as archive:
                manifest = archive.read("repository-quality-guard/runtime/MANIFEST.sha256").decode("utf-8")
                private_names = [
                    item.name
                    for item in (ROOT / "profiles").iterdir()
                    if item.is_dir()
                ]
                for private_name in private_names:
                    self.assertNotIn(private_name, manifest)


if __name__ == "__main__":
    unittest.main()
