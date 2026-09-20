from __future__ import annotations

import hashlib
import os
import shutil
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
    def test_git_source_does_not_track_release_binary_payloads(self) -> None:
        result = subprocess.run(
            ["git", "ls-files", "--", "offline"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        tracked = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        binaries = {
            path
            for path in tracked
            if path.endswith(".whl") or path == "offline/node_modules.zip"
        }
        self.assertEqual(set(), binaries)

    def test_integrity_manifest_does_not_bind_release_only_binary_payloads(self) -> None:
        manifest = (ROOT / "runtime" / "MANIFEST.sha256").read_text(encoding="utf-8")
        self.assertNotIn("  offline/", manifest)

    def test_release_still_contains_local_offline_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "public.zip"
            self._build(out)
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
            prefix = "repository-quality-guard/offline/"
            self.assertIn(prefix + "node_modules.zip", names)
            self.assertTrue(
                any(name.startswith(prefix + "wheelhouse/") and name.endswith(".whl") for name in names)
            )

    def test_builder_refuses_source_without_local_offline_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(".git", "offline", "__pycache__", ".pytest_cache", ".ruff_cache"),
            )
            output = Path(temp) / "missing-offline.zip"
            result = subprocess.run(
                [
                    sys.executable,
                    str(source / "tools" / "build_release.py"),
                    str(source),
                    str(output),
                ],
                cwd=source,
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("offline release payload", result.stderr + result.stdout)
            self.assertFalse(output.exists())

    def _build(self, output: Path, *extra: str) -> None:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "build_release.py"),
                str(ROOT),
                str(output),
                *extra,
            ],
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

    def test_public_release_excludes_private_profiles_and_keeps_authoring_assets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "public.zip"
            self._build(out)
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
                joined = "\n".join(sorted(names))
                self.assertNotIn("profiles/", joined)
                self.assertTrue(any("dev-tests/" in name for name in names))
                self.assertTrue(any("tools/" in name for name in names))
                self.assertIn("repository-quality-guard/README.md", names)
                self.assertIn("repository-quality-guard/README_zh.md", names)
                self.assertFalse(any("profiles/" in name for name in names))
                self.assertFalse(
                    any(
                        any(
                            part in name.split("/")
                            for part in (
                                "__pycache__",
                                ".pytest_cache",
                                ".ruff_cache",
                                ".mypy_cache",
                            )
                        )
                        or name.endswith((".pyc", ".pyo"))
                        for name in names
                    )
                )

    def test_internal_release_includes_private_profiles_without_caches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "internal.zip"
            self._build(out, "--internal")
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
                self.assertIn("repository-quality-guard/README.md", names)
                self.assertIn("repository-quality-guard/README_zh.md", names)
                self.assertTrue(any("dev-tests/" in name for name in names))
                self.assertTrue(any("tools/" in name for name in names))
                private_names = sorted(
                    item.name
                    for item in (ROOT / "profiles").iterdir()
                    if item.is_dir() and (item / "profile.json").is_file()
                )
                self.assertTrue(private_names)
                for private_name in private_names:
                    prefix = f"repository-quality-guard/profiles/{private_name}/"
                    self.assertIn(prefix + "profile.json", names)
                    self.assertFalse(
                        any(
                            any(
                                part in name.split("/")
                                for part in (
                                    "__pycache__",
                                    ".pytest_cache",
                                    ".ruff_cache",
                                    ".mypy_cache",
                                )
                            )
                            or name.endswith((".pyc", ".pyo"))
                            for name in names
                        )
                    )
                    profile_root = ROOT / "profiles" / private_name
                    agents_file = profile_root / "AGENTS.md"
                    if agents_file.is_file():
                        self.assertIn(prefix + "AGENTS.md", names)
                    readme = profile_root / "README.md"
                    if readme.is_file():
                        self.assertIn(prefix + "README.md", names)
                    tests_dir = profile_root / "tests"
                    if tests_dir.is_dir():
                        self.assertTrue(
                            any(name.startswith(prefix + "tests/") for name in names)
                        )

    def test_runtime_integrity_ignores_regenerable_python_and_tool_caches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "public.zip"
            self._build(out)
            extracted = Path(temp) / "extracted"
            with zipfile.ZipFile(out) as archive:
                archive.extractall(extracted)
            root = extracted / "repository-quality-guard"

            cache_files = {
                root
                / "runtime"
                / "src"
                / "__pycache__"
                / "sample.cpython-312.pyc": b"cache",
                root / ".pytest_cache" / "v" / "cache" / "nodeids": b"[]",
                root / ".ruff_cache" / "0.14.0" / "cache": b"cache",
                root / ".mypy_cache" / "3.12" / "meta.json": b"{}",
            }
            for path, content in cache_files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            sys.path.insert(0, str(root))
            try:
                from runtime.src import integrity

                result = integrity.verify_release_integrity(
                    {
                        integrity.RELEASE_HOME_ENV: str(root),
                        integrity.RELEASE_SEAL_ENV: hashlib.sha256(
                            (root / integrity.MANIFEST_NAME).read_bytes()
                        ).hexdigest(),
                    }
                )
            finally:
                sys.path.pop(0)
                for name in tuple(sys.modules):
                    if name == "runtime" or name.startswith("runtime."):
                        sys.modules.pop(name, None)

            self.assertTrue(result.passed, result.issues)

    def test_public_release_manifest_does_not_leak_private_profile_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "public.zip"
            self._build(out)
            with zipfile.ZipFile(out) as archive:
                manifest = archive.read(
                    "repository-quality-guard/runtime/MANIFEST.sha256"
                ).decode("utf-8")
                private_names = [
                    item.name for item in (ROOT / "profiles").iterdir() if item.is_dir()
                ]
                for private_name in private_names:
                    self.assertNotIn(private_name, manifest)


if __name__ == "__main__":
    unittest.main()
