from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.src.project_profiles import get_project_profile, resolve_profile_reference

ROOT = Path(__file__).resolve().parents[1]


def _write_profile(root: Path, name: str) -> None:
    p = root / "profiles" / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "profile.json").write_text(
        json.dumps(
            {
                "schema": "repository-quality-guard/profile-v1",
                "name": name,
                "entrypoint": "extension.py",
            }
        ),
        encoding="utf-8",
    )
    (p / "extension.py").write_text(
        f"from runtime.src.profile_api import QualityGuardProfile\nclass Profile(QualityGuardProfile):\n    name={name!r}\n",
        encoding="utf-8",
    )


def _write_lock(root: Path, distribution: str) -> None:
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "RELEASE.lock").write_text(
        "schema=repository-quality-guard/release-v1\n"
        "version=0.20.1\n"
        f"distribution={distribution}\n"
        "manifest_sha256=" + "0" * 64 + "\n"
        "protected_file_count=0\n",
        encoding="utf-8",
    )


class TestProfileResolution(unittest.TestCase):
    def test_portable_explicit_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / "release"
            repo = Path(temp) / "repo"
            repo.mkdir()
            _write_lock(release, "skill")
            _write_profile(release, "alpha")
            selected = resolve_profile_reference(repo, "alpha", release_root=release)
            self.assertEqual(
                (selected.name, selected.source), ("alpha", "explicit-cli")
            )

    def test_portable_exact_directory_name_auto_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / "release"
            repo = Path(temp) / "alpha"
            repo.mkdir()
            _write_lock(release, "skill")
            _write_profile(release, "alpha")
            selected = resolve_profile_reference(repo, release_root=release)
            self.assertEqual(
                (selected.name, selected.source), ("alpha", "auto-directory-name")
            )

    def test_portable_does_not_guess_from_marker_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / "release"
            repo = Path(temp) / "ordinary-repo"
            (repo / "agents").mkdir(parents=True)
            (repo / "agents" / "react_agent.py").write_text(
                "# marker\n", encoding="utf-8"
            )
            _write_lock(release, "skill")
            _write_profile(release, "alpha")
            selected = resolve_profile_reference(repo, release_root=release)
            self.assertEqual((selected.name, selected.source), ("", "generic"))

    def test_installed_profile_comes_only_from_sealed_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / "release"
            repo = Path(temp) / "repo"
            repo.mkdir()
            _write_lock(release, "agents")
            installed = release / "installed"
            profile = installed / "profile"
            profile.mkdir(parents=True)
            (installed / "POLICY.json").write_text(
                json.dumps(
                    {
                        "schema": "repository-quality-guard/installed-policy-v1",
                        "profile_name": "alpha",
                        "profile_version": "1",
                        "profile_source": "explicit-cli",
                    }
                ),
                encoding="utf-8",
            )
            (profile / "profile.json").write_text(
                json.dumps(
                    {
                        "schema": "repository-quality-guard/profile-v1",
                        "name": "alpha",
                        "entrypoint": "extension.py",
                    }
                ),
                encoding="utf-8",
            )
            (profile / "extension.py").write_text(
                "from runtime.src.profile_api import QualityGuardProfile\nclass Profile(QualityGuardProfile):\n    name='alpha'\n",
                encoding="utf-8",
            )
            selected = resolve_profile_reference(repo, release_root=release)
            self.assertEqual(
                (selected.name, selected.source), ("alpha", "sealed-installed")
            )
            explicit = resolve_profile_reference(
                repo, "alpha", release_root=release
            )
            self.assertEqual(
                (explicit.name, explicit.source),
                ("alpha", "sealed-installed-explicit"),
            )
            by_name = get_project_profile("alpha", release_root=release)
            by_path = get_project_profile(str(profile), release_root=release)
            self.assertEqual(by_name.name if by_name else "", "alpha")
            self.assertEqual(by_path.name if by_path else "", "alpha")
            with self.assertRaises(ValueError):
                get_project_profile("other", release_root=release)
            with self.assertRaises(ValueError):
                resolve_profile_reference(repo, "other", release_root=release)

    def test_installed_generic_is_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / "release"
            repo = Path(temp) / "repo"
            repo.mkdir()
            _write_lock(release, "agents")
            installed = release / "installed"
            installed.mkdir(parents=True)
            (installed / "POLICY.json").write_text(
                json.dumps(
                    {
                        "schema": "repository-quality-guard/installed-policy-v1",
                        "profile_name": "",
                        "profile_version": "",
                        "profile_source": "generic",
                    }
                ),
                encoding="utf-8",
            )
            selected = resolve_profile_reference(repo, release_root=release)
            self.assertEqual(
                (selected.name, selected.source), ("", "sealed-installed-generic")
            )
            with self.assertRaises(ValueError):
                resolve_profile_reference(repo, "alpha", release_root=release)


if __name__ == "__main__":
    unittest.main()
