#!/usr/bin/env python3
"""Build deterministic public/internal Repository Quality Guard .skill.zip releases."""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
ROOT_NAME = "repository-quality-guard"
_SOURCE_EXCLUDES = {
    ".git",
    "profiles",
    ".qg-work",
    ".pytest_cache",
    ".ruff_cache",
    "equivalence-fixtures",
}
_FILE_EXCLUDES = {"修改说明.md"}


def _ignore_source(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in _SOURCE_EXCLUDES or name in _FILE_EXCLUDES:
            ignored.add(name)
        elif name == "__pycache__" or name.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored


def _load_integrity(root: Path):
    path = root / "runtime" / "src" / "integrity.py"
    spec = importlib.util.spec_from_file_location("_rqg_build_integrity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import release integrity module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return module
    except Exception:
        sys.modules.pop(spec.name, None)
        raise


def _clean_caches(root: Path) -> None:
    for directory in sorted(root.rglob("__pycache__"), reverse=True):
        shutil.rmtree(directory, ignore_errors=True)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".pyc", ".pyo"}:
            path.unlink()


def _stage_source(source: Path, staging: Path, *, internal: bool) -> Path:
    target = staging / ROOT_NAME
    shutil.copytree(source, target, ignore=_ignore_source)
    profiles = target / "profiles"
    if profiles.exists():
        shutil.rmtree(profiles)
    if internal:
        private_profiles = source / "profiles"
        if private_profiles.is_dir():
            shutil.copytree(
                private_profiles,
                profiles,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
    _clean_caches(target)
    integrity = _load_integrity(target)
    integrity.seal_release_tree(target, "skill")
    _clean_caches(target)
    # seal once more after importer cache cleanup; cleanup itself does not touch protected files.
    result = integrity.verify_release_integrity(
        {
            integrity.RELEASE_HOME_ENV: str(target),
            integrity.RELEASE_SEAL_ENV: integrity._sha256(target / integrity.MANIFEST_NAME),
        }
    )
    if not result.passed:
        raise RuntimeError("built release integrity failed: " + "; ".join(result.issues))
    return target


def _zip_tree(tree: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for path in sorted(p for p in tree.rglob("*") if p.is_file()):
            relative = path.relative_to(tree.parent).as_posix()
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = path.stat().st_mode
            executable = bool(mode & stat.S_IXUSR)
            info.external_attr = ((0o755 if executable else 0o644) & 0xFFFF) << 16
            info.create_system = 3
            info.flag_bits |= 0x800
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build(source: Path, destination: Path, *, internal: bool = False) -> Path:
    source = source.resolve()
    if not (source / "SKILL.md").is_file():
        raise ValueError(f"not a Repository Quality Guard source tree: {source}")
    with tempfile.TemporaryDirectory(prefix="rqg-release-") as temp:
        tree = _stage_source(source, Path(temp), internal=internal)
        _zip_tree(tree, destination.resolve())
    return destination.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("output")
    parser.add_argument("--internal", action="store_true", help="include ignored local dogfood Profiles")
    args = parser.parse_args(argv)
    path = build(Path(args.source), Path(args.output), internal=args.internal)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
