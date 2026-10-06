#!/usr/bin/env python3
"""Build the canonical full-repository Repository Quality Guard ZIP."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
ROOT_NAME = "repository-quality-guard"
_DIR_EXCLUDES = {
    ".qg-work",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "equivalence-fixtures",
    "__pycache__",
}
_FILE_EXCLUDES = {"修改说明.md"}


def _ignore_source(directory: str, names: list[str]) -> set[str]:
    """Exclude only regenerable/session-local artifacts from the full snapshot."""
    ignored = _DIR_EXCLUDES.intersection(names)
    ignored.update(_FILE_EXCLUDES.intersection(names))
    ignored.update(name for name in names if name.endswith((".pyc", ".pyo")))
    if Path(directory).name == ".git":
        ignored.update({"COMMIT_EDITMSG", "index.lock"}.intersection(names))
    return ignored


def _load_integrity(root: Path):
    """Load the source tree's integrity module without retaining it globally.

    Args:
        root: Repository root containing the release integrity implementation.

    Returns:
        Imported integrity module used to validate the source snapshot.
    """
    path = root / "runtime" / "src" / "integrity.py"
    spec = importlib.util.spec_from_file_location("_rqg_build_integrity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import release integrity module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def _validate_offline_payload(source: Path) -> None:
    """Require the local ignored payload that makes the canonical ZIP self-contained."""
    node_archive = source / "offline" / "node_modules.zip"
    wheelhouse = source / "offline" / "wheelhouse"
    missing: list[str] = []
    if not node_archive.is_file():
        missing.append("offline/node_modules.zip")
    lock_path = source / "runtime" / "dependencies.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock["python"]
    if not isinstance(packages, dict):
        raise RuntimeError("offline release payload dependency lock is invalid")
    wheels = tuple(wheelhouse.glob("*.whl")) if wheelhouse.is_dir() else ()
    for package, version in sorted(packages.items()):
        prefix = f"{str(package).replace('-', '_')}-{version}-"
        if not any(path.name.startswith(prefix) for path in wheels):
            missing.append(f"offline/wheelhouse/{prefix}*.whl")
    if missing:
        raise RuntimeError("offline release payload missing: " + ", ".join(missing))


def _validate_source(source: Path) -> None:
    """Validate that the source is a sealed, self-contained repository snapshot.

    Args:
        source: Repository root that will be copied byte-for-byte into the ZIP.

    Returns:
        None after repository metadata, Profile catalog, offline media, and release
        integrity have all been validated.

    Raises:
        ValueError: The supplied directory is not an RQG source tree.
        RuntimeError: Required repository/package state is missing or unsealed.
    """
    if not (source / "SKILL.md").is_file():
        raise ValueError(f"not a Repository Quality Guard source tree: {source}")
    if not (source / ".git").is_dir():
        raise RuntimeError(
            "canonical full-repository ZIP requires source .git metadata"
        )
    profiles = source / "profiles"
    if not (profiles / "qg-example-profile" / "profile.json").is_file():
        raise RuntimeError("canonical full-repository ZIP requires the Profile catalog")
    validation = (
        "import sys; "
        "from runtime.src.project_profiles import load_quality_profile; "
        "profile = load_quality_profile(sys.argv[1]); "
        "assert profile is not None; "
        "profile.build()"
    )
    for profile_dir in sorted(path for path in profiles.iterdir() if path.is_dir()):
        if not (profile_dir / "profile.json").is_file():
            continue
        subprocess.run(
            [sys.executable, "-c", validation, str(profile_dir)],
            cwd=source,
            check=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    _validate_offline_payload(source)

    integrity = _load_integrity(source)
    lock_path = source / integrity.RELEASE_LOCK_NAME
    lock: dict[str, str] = {}
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        lock[key.strip()] = value.strip()
    try:
        seal = lock["manifest_sha256"]
    except KeyError as error:
        raise RuntimeError("runtime/RELEASE.lock missing manifest_sha256") from error
    result = integrity.verify_release_integrity(
        {
            integrity.RELEASE_HOME_ENV: str(source),
            integrity.RELEASE_SEAL_ENV: seal,
        }
    )
    if not result.passed:
        raise RuntimeError(
            "source release integrity failed; seal the source tree before packaging: "
            + "; ".join(result.issues)
        )


def _zip_tree(tree: Path, destination: Path) -> None:
    """Write one deterministic ZIP from an already validated staging tree.

    Args:
        tree: Staging directory whose basename becomes the ZIP root directory.
        destination: Output ZIP path.

    Returns:
        None after every regular file has been written deterministically.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for path in sorted(tree.rglob("*")):
            relative = path.relative_to(tree.parent).as_posix()
            if path.is_dir():
                info = zipfile.ZipInfo(relative.rstrip("/") + "/", FIXED_ZIP_TIME)
                info.external_attr = ((stat.S_IFDIR | 0o755) & 0xFFFF) << 16 | 0x10
                info.create_system = 3
                info.flag_bits |= 0x800
                archive.writestr(info, b"")
                continue
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = path.stat().st_mode
            executable = bool(mode & stat.S_IXUSR)
            permissions = 0o755 if executable else 0o644
            info.external_attr = ((stat.S_IFREG | permissions) & 0xFFFF) << 16
            info.create_system = 3
            info.flag_bits |= 0x800
            archive.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def build(source: Path, destination: Path) -> Path:
    """Build the canonical full-repository ZIP from a sealed source tree.

    Args:
        source: Sealed repository root including `.git`, Profiles and offline media.
        destination: Output ZIP path.

    Returns:
        Resolved path to the completed deterministic ZIP.
    """
    source = source.resolve()
    _validate_source(source)
    with tempfile.TemporaryDirectory(prefix="rqg-full-repo-") as temp:
        tree = Path(temp) / ROOT_NAME
        shutil.copytree(source, tree, ignore=_ignore_source, symlinks=True)
        _zip_tree(tree, destination.resolve())
    return destination.resolve()


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments, build the canonical ZIP, and print its path.

    Args:
        argv: Optional argument vector; `None` reads the process command line.

    Returns:
        Process exit code `0` after a successful build.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", nargs="?", default=str(Path(__file__).resolve().parents[1])
    )
    parser.add_argument("output")
    args = parser.parse_args(argv)
    path = build(Path(args.source), Path(args.output))
    sys.stdout.write(f"{path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
