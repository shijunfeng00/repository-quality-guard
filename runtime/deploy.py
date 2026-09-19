"""Atomically install a sealed Repository Quality Guard runtime into `.agents`."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

if __name__ == "__main__" and not sys.flags.dont_write_bytecode:
    os.execv(
        sys.executable,
        [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:]],
    )


def _load_module(path: Path, stem: str) -> tuple[str, object]:
    """Load one release utility module from an exact path."""
    if not path.is_file():
        raise RuntimeError(f"release file missing: {path}")
    name = f"_{stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load release module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return name, module


def _release_identity(bundle: Path) -> str:
    """Verify the source Skill and return its manifest seal."""
    module_name, integrity = _load_module(
        bundle / "runtime" / "src" / "integrity.py", "rqg_integrity"
    )
    try:
        manifest = bundle / "runtime" / "MANIFEST.sha256"
        if not manifest.is_file():
            raise RuntimeError("source Skill is missing runtime/MANIFEST.sha256")
        digest = hashlib.sha256()
        with manifest.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        seal = digest.hexdigest()
        result = integrity.verify_release_integrity(
            {
                integrity.RELEASE_HOME_ENV: str(bundle),
                integrity.RELEASE_SEAL_ENV: seal,
            }
        )
        if not result.passed:
            raise RuntimeError("source Skill integrity failed: " + "; ".join(result.issues))
        return str(seal)
    finally:
        del sys.modules[module_name]


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _repo_for_destination(destination: Path) -> Path:
    """Validate the fixed `.agents/skills/repository-quality-guard` layout."""
    if destination.name != "repository-quality-guard":
        raise RuntimeError("destination must end with repository-quality-guard")
    if destination.parent.name != "skills" or destination.parent.parent.name != ".agents":
        raise RuntimeError(
            "destination must be <repo>/.agents/skills/repository-quality-guard"
        )
    return destination.parent.parent.parent


def _profile_runtime(bundle: Path) -> Any:
    """Import the verified bundle's generic Profile loader."""
    bundle_text = str(bundle)
    inserted = bundle_text not in sys.path
    if inserted:
        sys.path.insert(0, bundle_text)
    try:
        return importlib.import_module("runtime.src.project_profiles")
    finally:
        if inserted:
            sys.path.remove(bundle_text)


def _profile_selection(bundle: Path, repo: Path, explicit: str) -> tuple[Any, Any | None]:
    """Resolve install-time Profile using the same Portable precedence."""
    profiles = _profile_runtime(bundle)
    selection = profiles.resolve_profile_reference(
        repo,
        explicit or None,
        release_root=bundle,
    )
    if selection.source == "explicit-cli":
        print(f"[QG] Install profile selected: '{selection.name}' (source=explicit-cli).")
    elif selection.source == "auto-directory-name":
        print(
            f"[QG] Auto-selected install profile '{selection.name}' because repository "
            "directory name matches an available profile. (source=auto-directory-name)"
        )
    else:
        print("[QG] No install profile selected; freezing generic policy. (source=generic)")
    profile = profiles.get_project_profile(selection.reference)
    return selection, profile


def _copy_agents_tree(bundle: Path, target: Path) -> None:
    """Copy first-party runtime but exclude distribution-only/profile-catalog media."""
    shutil.copytree(
        bundle,
        target,
        ignore=shutil.ignore_patterns(
            ".git",
            ".hg",
            ".svn",
            "*.whl",
            "*.pyc",
            "*.pyo",
            "__pycache__",
            ".ruff_cache",
            ".pytest_cache",
            "tests",
            "templates",
            "profiles",
            "installed",
            "offline",
            "node_modules",
            "vendor",
            "wheels",
        ),
    )
    forbidden = [
        target / "offline",
        target / "profiles",
        target / "tests",
        target / "runtime" / "releases",
        target / "runtime" / "CURRENT",
        target / "runtime" / "src" / "repo_quality_guard",
    ]
    existing = [path for path in forbidden if path.exists()]
    if existing:
        raise RuntimeError(f"installed tree contains distribution-only path: {existing[0]}")
    if any(target.rglob("*.whl")) or any(target.rglob("*.pyc")):
        raise RuntimeError("installed tree must not contain wheels or Python bytecode")
    if any(path.name == "node_modules" for path in target.rglob("*")):
        raise RuntimeError("installed tree must not contain node_modules")
    required = (
        "SKILL.md",
        "scripts/quality_guard.py",
        "scripts/git_hook_install.py",
        "runtime/install_dependencies.py",
        "runtime/dependencies.lock.json",
        "runtime/src/workflow.py",
        "runtime/src/profile_api.py",
        "runtime/src/integrity.py",
    )
    missing = [relative for relative in required if not (target / relative).is_file()]
    if missing:
        raise RuntimeError(f"installed tree is missing required runtime: {missing[0]}")


def _profile_source_dir(bundle: Path, reference: str) -> Path:
    candidate = Path(reference).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    return (bundle / "profiles" / reference).resolve()


def _write_installed_policy(
    bundle: Path,
    staging: Path,
    selection: Any,
    profile: Any | None,
) -> Path:
    """Copy only the selected Profile and write immutable Installed Policy metadata."""
    installed = staging / "installed"
    installed.mkdir(parents=True, exist_ok=True)
    if profile is not None:
        if not selection.reference:
            raise RuntimeError("profile selection lost its source reference")
        source_dir = _profile_source_dir(bundle, str(selection.reference))
        if not (source_dir / "profile.json").is_file():
            raise RuntimeError(f"selected profile payload missing: {source_dir}")
        shutil.copytree(
            source_dir,
            installed / "profile",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "AGENTS.template.md"),
        )
    payload = {
        "schema": "repository-quality-guard/installed-policy-v1",
        "profile_name": profile.name if profile is not None else "",
        "profile_version": profile.version if profile is not None else "",
        "profile_source": selection.source,
        "disabled_rules": list(profile.disabled_rules) if profile is not None else [],
        "capabilities": list(profile.capabilities) if profile is not None else [],
    }
    policy = installed / "POLICY.json"
    policy.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return policy


def _agents_template(bundle: Path, selection: Any, profile: Any | None) -> Path:
    """Return Profile AGENTS override or the fixed generic template."""
    if profile is not None and profile.agents_template:
        if not selection.reference:
            raise RuntimeError("profile AGENTS template has no source reference")
        source = _profile_source_dir(bundle, str(selection.reference)) / profile.agents_template
        if not source.is_file():
            raise RuntimeError(f"selected Profile AGENTS template missing: {source}")
        return source
    source = bundle / "templates" / "AGENTS.template.md"
    if not source.is_file():
        raise RuntimeError("source Skill is missing generic templates/AGENTS.template.md")
    return source


def _seal_agents_tree(target: Path) -> str:
    """Seal and immediately verify the dependency-media-free installed tree."""
    module_name, integrity = _load_module(
        target / "runtime" / "src" / "integrity.py", "rqg_installed_integrity"
    )
    try:
        seal = str(integrity.seal_release_tree(target, "agents"))
        result = integrity.verify_release_integrity(
            {
                integrity.RELEASE_HOME_ENV: str(target),
                integrity.RELEASE_SEAL_ENV: seal,
            }
        )
        if not result.passed:
            raise RuntimeError("installed integrity failed: " + "; ".join(result.issues))
        return seal
    finally:
        del sys.modules[module_name]
        shutil.rmtree(target / "runtime" / "src" / "__pycache__", ignore_errors=True)


def _git_persistence_preflight(repo: Path, destination: Path) -> None:
    """Reject ignore rules that would prevent runtime/AGENTS persistence."""
    probe = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return
    relative = destination.relative_to(repo)
    required = [
        relative / "SKILL.md",
        relative / "scripts" / "quality_guard.py",
        relative / "scripts" / "git_hook_install.py",
        relative / "runtime" / "install_dependencies.py",
        relative / "runtime" / "src" / "workflow.py",
        relative / "runtime" / "src" / "integrity.py",
        relative / "installed" / "POLICY.json",
    ]
    ignored = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "check-ignore",
            "--no-index",
            "-v",
            "--",
            *(path.as_posix() for path in required),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if ignored.returncode not in {0, 1}:
        raise RuntimeError(
            "cannot validate Git persistence ignore state: "
            + (ignored.stderr.strip() or ignored.stdout.strip())
        )
    if ignored.returncode == 0:
        raise RuntimeError(
            "Git ignore/exclude would prevent Repository Quality Guard persistence: "
            + ignored.stdout.strip()
        )


def _create_host_agents_if_missing(repo: Path, source: Path) -> bool:
    """Create root AGENTS.md only when absent; never overwrite shared host instructions."""
    target = repo / "AGENTS.md"
    if target.exists():
        return False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(target, flags, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return True


def _activate_release(staging: Path, destination: Path, repo: Path, agents_source: Path) -> None:
    """Atomically activate `.agents`; root AGENTS.md is host-owned/create-if-missing only."""
    old = destination.with_name(f".{destination.name}.old-{uuid.uuid4().hex}")
    moved_old = destination.exists()
    installed_new = False
    created_agents = False
    success = False
    host_agents_preexisted = (repo / "AGENTS.md").exists()
    try:
        if moved_old:
            os.replace(destination, old)
        os.replace(staging, destination)
        installed_new = True
        if not host_agents_preexisted:
            # O_EXCL closes the race: another actor creating AGENTS.md aborts install
            # rather than letting QG overwrite or silently claim ownership.
            created_agents = _create_host_agents_if_missing(repo, agents_source)
        success = True
    finally:
        _remove_tree(staging)
        if success:
            _remove_tree(old)
        else:
            if installed_new:
                _remove_tree(destination)
            if moved_old and old.exists():
                os.replace(old, destination)
            if created_agents:
                (repo / "AGENTS.md").unlink(missing_ok=True)


def deploy(bundle: Path, destination: Path, profile_reference: str = "") -> str:
    """Atomically install QG and freeze exactly one generic/Profile policy."""
    bundle = bundle.resolve()
    destination = destination.resolve()
    _release_identity(bundle)
    repo = _repo_for_destination(destination)
    selection, profile = _profile_selection(bundle, repo, profile_reference)
    agents_source = _agents_template(bundle, selection, profile)
    _git_persistence_preflight(repo, destination)

    module_name, installer = _load_module(
        bundle / "runtime" / "install_dependencies.py", "rqg_dependencies"
    )
    try:
        installer.prepare_runtime_dependencies(bundle, prune_managed=True)
    finally:
        del sys.modules[module_name]

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".repository-quality-guard.new-", dir=destination.parent)
    )
    _remove_tree(staging)
    _copy_agents_tree(bundle, staging)
    _write_installed_policy(bundle, staging, selection, profile)
    seal = _seal_agents_tree(staging)
    _activate_release(staging, destination, repo, agents_source)
    return seal[:20]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle")
    parser.add_argument("destination")
    parser.add_argument(
        "--profile",
        default="",
        metavar="PROFILE",
        help=(
            "install-time Profile name/path; omitted uses exact repository-directory-name "
            "auto-match or generic policy"
        ),
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="compatibility no-op; installation is always full staging + atomic replacement",
    )
    args = parser.parse_args()
    print(deploy(Path(args.bundle), Path(args.destination), args.profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
