from __future__ import annotations

import hashlib
import json
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
    def test_release_binary_payload_policy_is_declared_and_git_respects_it(
        self,
    ) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/offline/wheelhouse/*.whl", gitignore)
        self.assertIn("/offline/node_modules.zip", gitignore)
        if (ROOT / ".git").exists():
            result = subprocess.run(
                ["git", "ls-files", "--", "offline"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            tracked = {
                line.strip() for line in result.stdout.splitlines() if line.strip()
            }
            binaries = {
                path
                for path in tracked
                if path.endswith(".whl") or path == "offline/node_modules.zip"
            }
            self.assertEqual(set(), binaries)

    def test_integrity_manifest_does_not_bind_release_only_binary_payloads(
        self,
    ) -> None:
        manifest = (ROOT / "runtime" / "MANIFEST.sha256").read_text(encoding="utf-8")
        self.assertNotIn("  offline/", manifest)

    def test_cross_platform_git_checkout_preserves_release_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(
                    ".git",
                    "offline",
                    "__pycache__",
                    ".pytest_cache",
                    ".ruff_cache",
                    ".mypy_cache",
                    "*.pyc",
                    "*.pyo",
                ),
            )
            for profile in (source / "profiles").iterdir():
                if profile.name != "qg-example-profile":
                    shutil.rmtree(profile)
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(
                ["git", "config", "user.name", "RQG Test"], cwd=source, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "rqg@example.invalid"],
                cwd=source,
                check=True,
            )
            subprocess.run(["git", "add", "-A"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=source, check=True)

            clones: dict[str, Path] = {}
            for mode, autocrlf in (("windows", "true"), ("linux", "false")):
                clone = Path(temp) / mode
                subprocess.run(
                    [
                        "git",
                        "-c",
                        f"core.autocrlf={autocrlf}",
                        "clone",
                        "-q",
                        str(source),
                        str(clone),
                    ],
                    check=True,
                )
                clones[mode] = clone

            manifest_entries = [
                line.split("  ", 1)[1]
                for line in (source / "runtime" / "MANIFEST.sha256")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            expected = {
                relative: _sha(clones["linux"] / relative)
                for relative in manifest_entries
            }
            actual = {
                relative: _sha(clones["windows"] / relative)
                for relative in manifest_entries
            }
            self.assertEqual(expected, actual)

            for clone in clones.values():
                sys.path.insert(0, str(clone))
                try:
                    from runtime.src import integrity

                    result = integrity.verify_release_integrity(
                        {
                            integrity.RELEASE_HOME_ENV: str(clone),
                            integrity.RELEASE_SEAL_ENV: hashlib.sha256(
                                (clone / integrity.MANIFEST_NAME).read_bytes()
                            ).hexdigest(),
                        }
                    )
                finally:
                    sys.path.pop(0)
                    for name in tuple(sys.modules):
                        if name == "runtime" or name.startswith("runtime."):
                            sys.modules.pop(name, None)
                self.assertTrue(result.passed, result.issues)

    def test_canonical_zip_contains_local_offline_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "canonical.zip"
            self._build(out)
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
            prefix = "repository-quality-guard/offline/"
            self.assertIn(prefix + "node_modules.zip", names)
            self.assertTrue(
                any(
                    name.startswith(prefix + "wheelhouse/") and name.endswith(".whl")
                    for name in names
                )
            )

    def test_builder_refuses_source_without_local_offline_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(
                    "offline", "__pycache__", ".pytest_cache", ".ruff_cache"
                ),
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

    @staticmethod
    def _seed_release_media(source: Path) -> None:
        offline = source / "offline"
        wheelhouse = offline / "wheelhouse"
        wheelhouse.mkdir(parents=True, exist_ok=True)
        node_archive = offline / "node_modules.zip"
        if not node_archive.exists():
            with zipfile.ZipFile(node_archive, "w") as archive:
                archive.writestr("node_modules/.rqg-test-fixture", b"fixture")
        lock = json.loads(
            (source / "runtime" / "dependencies.lock.json").read_text(encoding="utf-8")
        )
        for package, version in lock["python"].items():
            prefix = f"{str(package).replace('-', '_')}-{version}-"
            if not any(wheelhouse.glob(prefix + "*.whl")):
                (wheelhouse / f"{prefix}py3-none-any.whl").write_bytes(b"fixture")

    def _build(self, output: Path, *extra: str, source: Path | None = None) -> None:
        build_source = source or ROOT
        if not (build_source / "offline" / "node_modules.zip").is_file():
            with tempfile.TemporaryDirectory() as temp:
                staged = Path(temp) / "source"
                shutil.copytree(
                    build_source,
                    staged,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", ".pytest_cache", ".ruff_cache"
                    ),
                )
                self._seed_release_media(staged)
                self._run_build(staged, output, *extra)
            return
        self._run_build(build_source, output, *extra)

    @staticmethod
    def _run_build(source: Path, output: Path, *extra: str) -> None:
        subprocess.run(
            [
                sys.executable,
                str(source / "tools" / "build_release.py"),
                str(source),
                str(output),
                *extra,
            ],
            cwd=source,
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

    def test_canonical_full_repo_zip_is_byte_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / "a.zip", Path(temp) / "b.zip"
            self._build(a)
            self._build(b)
            self.assertEqual(_sha(a), _sha(b))
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_canonical_zip_ignores_git_commit_message_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(
                    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"
                ),
            )
            self._seed_release_media(source)
            commit_message = source / ".git" / "COMMIT_EDITMSG"
            first = root / "first.zip"
            second = root / "second.zip"

            commit_message.write_text("first session message\n", encoding="utf-8")
            self._build(first, source=source)
            commit_message.write_text("different session message\n", encoding="utf-8")
            self._build(second, source=source)

            self.assertEqual(_sha(first), _sha(second))
            with zipfile.ZipFile(first) as archive:
                self.assertNotIn(
                    "repository-quality-guard/.git/COMMIT_EDITMSG",
                    set(archive.namelist()),
                )

    def test_canonical_zip_keeps_git_profiles_and_authoring_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "canonical.zip"
            self._build(out)
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
                prefix = "repository-quality-guard/"
                self.assertIn(prefix + ".git/HEAD", names)
                self.assertIn(prefix + "README.md", names)
                self.assertIn(prefix + "README_zh.md", names)
                self.assertTrue(
                    any(name.startswith(prefix + "dev-tests/") for name in names)
                )
                self.assertTrue(
                    any(name.startswith(prefix + "tools/") for name in names)
                )
                self.assertIn(
                    prefix + "profiles/qg-example-profile/profile.json", names
                )
                source_profiles = [
                    item.name for item in (ROOT / "profiles").iterdir() if item.is_dir()
                ]
                for profile_name in source_profiles:
                    self.assertTrue(
                        any(
                            name.startswith(prefix + f"profiles/{profile_name}/")
                            for name in names
                        ),
                        profile_name,
                    )
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

    def test_canonical_zip_extracts_to_a_real_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "canonical.zip"
            self._build(out)
            extracted = Path(temp) / "extracted"
            with zipfile.ZipFile(out) as archive:
                archive.extractall(extracted)
            root = extracted / "repository-quality-guard"
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual("true", result.stdout.strip())

    def test_runtime_integrity_ignores_regenerable_python_and_tool_caches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "canonical.zip"
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


if __name__ == "__main__":
    unittest.main()
