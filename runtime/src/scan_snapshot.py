"""当前工作树完整扫描快照：以强指纹安全复用同一 release 的 ScanReport。"""

from __future__ import annotations

import hashlib
import os
import pickle
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import cli
from .config import GuardConfig
from .git_utils import run_readonly_git_bytes
from .model import ScanReport
from .source_inputs import source_language

SNAPSHOT_SCHEMA = "repository-quality-guard/current-scan-v2"
_RELEVANT_EXACT = {
    "README.md",
    "pyproject.toml",
    ".quality/semantic-heuristic-authorizations.toml",
}


@dataclass(slots=True, frozen=True)
class _SnapshotKey:
    """保存一次扫描快照的稳定比较维度。"""

    profile: str
    base_revision: str
    staged: bool
    focus_files: tuple[str, ...]
    release_seal: str
    fingerprint: str


def _private_cache_dir(name: str) -> Path:
    """创建仅当前用户可写的持久缓存子目录，避免加载其他用户可替换的 pickle。"""
    cache_key = "REPO_QUALITY_GUARD_CACHE_DIR"
    cache_root = (
        Path(os.environ[cache_key])
        if cache_key in os.environ
        else Path(tempfile.gettempdir()) / "repository-quality-guard"
    )
    path = cache_root / name
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        status = path.stat()
        if status.st_uid != os.getuid():
            raise RuntimeError(f"扫描缓存目录不属于当前用户：{path}")
        if status.st_mode & 0o077:
            path.chmod(0o700)
    return path


def _git_commit(root: Path, revision: str) -> str:
    """解析 commit-ish 为不可变提交 ID。"""
    result = run_readonly_git_bytes(
        root, "rev-parse", "--verify", f"{revision}^{{commit}}"
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"无法解析 Git revision: {revision}")
    return result.stdout.decode("ascii").strip()


def _relevant_worktree_paths(root: Path, config: GuardConfig) -> tuple[str, ...]:
    """列出会影响现有扫描、配置、README 同步和 AGENTS 唯一性规则的输入。"""
    listed = run_readonly_git_bytes(
        root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
    )
    if listed.returncode != 0:
        detail = listed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "无法列出 Git 工作树文件")
    paths = {
        value.decode("utf-8", errors="surrogateescape")
        for value in listed.stdout.split(b"\0")
        if value
    }
    relevant = {
        path
        for path in paths
        if source_language(path, config)
        or path in _RELEVANT_EXACT
        or Path(path).name.lower() == "agents.md"
    }
    # QG187 故意检查被 Git ignore 的 AGENTS.md，因此这里不能只依赖 ls-files。
    for current_root, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name != ".git"]
        current = Path(current_root)
        relevant.update(
            (current / filename).relative_to(root).as_posix()
            for filename in filenames
            if filename.lower() == "agents.md"
        )
    return tuple(sorted(relevant))


def _worktree_fingerprint(
    root: Path,
    profile: str,
    base_revision: str,
    staged: bool,
    focus_files: tuple[str, ...],
    release_seal: str,
    config: GuardConfig,
) -> str:
    """计算 release、Git 比较契约与全部相关工作树输入的强 SHA-256。"""
    hasher = hashlib.sha256()
    metadata = (
        SNAPSHOT_SCHEMA,
        str(root.resolve()),
        _git_commit(root, "HEAD"),
        _git_commit(root, base_revision),
        profile,
        "staged" if staged else "worktree",
        release_seal,
        *focus_files,
    )
    for value in metadata:
        hasher.update(value.encode("utf-8"))
        hasher.update(b"\0")
    if staged:
        index = run_readonly_git_bytes(root, "ls-files", "-s", "-z")
        if index.returncode != 0:
            detail = index.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "无法读取 Git index")
        hasher.update(index.stdout)
    # staged 只改变接口 diff 目标；现有静态/架构/Ruff 仍读取工作树，故始终覆盖工作树。
    for relative in _relevant_worktree_paths(root, config):
        hasher.update(relative.encode("utf-8", errors="surrogateescape"))
        hasher.update(b"\0")
        path = root / relative
        if not path.is_file():
            hasher.update(b"<MISSING>\0")
            continue
        hasher.update(str(path.stat().st_size).encode("ascii"))
        hasher.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        hasher.update(b"\0")
    return hasher.hexdigest()


def _snapshot_path(root: Path, key: _SnapshotKey) -> Path:
    """返回当前仓库/比较契约唯一缓存文件路径。"""
    identity = hashlib.sha256(
        "\0".join(
            (str(root.resolve()), key.profile, key.base_revision, str(key.staged))
        ).encode("utf-8")
    ).hexdigest()[:24]
    return _private_cache_dir("current-scan-v2") / f"{identity}.pkl"


def _load_snapshot(root: Path, key: _SnapshotKey) -> tuple[ScanReport, str] | None:
    """读取并严格验证同 release 强指纹扫描缓存；不匹配时返回 cache miss。"""
    path = _snapshot_path(root, key)
    if not path.is_file():
        return None
    try:
        with path.open("rb") as stream:
            payload = pickle.load(stream)
    except ModuleNotFoundError as error:
        missing = error.name or ""
        if missing == "repo_quality_guard" or missing.startswith("repo_quality_guard."):
            return None
        raise
    except (OSError, EOFError, pickle.UnpicklingError):
        return None
    if type(payload) is not dict:
        return None
    expected = {
        "schema": SNAPSHOT_SCHEMA,
        "profile": key.profile,
        "base_revision": key.base_revision,
        "staged": key.staged,
        "focus_files": key.focus_files,
        "release_seal": key.release_seal,
        "fingerprint": key.fingerprint,
    }
    if set(payload) != {*expected, "report", "revision"}:
        return None
    if any(payload[name] != value for name, value in expected.items()):
        return None
    report = payload["report"]
    revision = payload["revision"]
    if type(report) is not ScanReport or type(revision) is not str:
        return None
    return report, revision


def _save_snapshot(
    root: Path, report: ScanReport, revision: str, key: _SnapshotKey
) -> None:
    """把完整扫描结果原子写入仓库外缓存；已激活 release 内容保持只读。"""
    path = _snapshot_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "profile": key.profile,
        "base_revision": key.base_revision,
        "staged": key.staged,
        "focus_files": key.focus_files,
        "release_seal": key.release_seal,
        "fingerprint": key.fingerprint,
        "revision": revision,
        "report": report,
    }
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as stream:
            pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)
            stream.flush()
            os.fsync(stream.fileno())
        replaced = temporary.replace(path)
        if replaced != path:
            raise RuntimeError("扫描快照原子替换返回了异常目标路径。")
    finally:
        temporary.unlink(missing_ok=True)


def _run_worker(args: Any, target: cli.AuditTarget) -> tuple[ScanReport, str]:
    """在短生命周期子进程执行原始 scan_target，并返回完整报告与实际 revision。"""
    worker_dir = _private_cache_dir("scan-worker")
    with tempfile.NamedTemporaryFile(
        prefix="scan-", suffix=".pkl", dir=worker_dir, delete=False
    ) as stream:
        output = Path(stream.name)
    command = [
        sys.executable,
        "-m",
        "runtime.src.scan_worker",
        "--path",
        str(target.root),
        "--output",
        str(output),
    ]
    if args.profile:
        command.extend(["--profile", args.profile])
    if args.diff_base:
        command.extend(["--diff-base", args.diff_base])
    if args.staged:
        command.append("--staged")
    if args.files:
        command.extend(["--files", args.files])
    environment = os.environ.copy()
    skill_root = str(Path(__file__).resolve().parents[2])
    # worker 只需要 Guard 自身源码；覆盖继承的 PYTHONPATH，避免外部模块污染扫描运行时。
    environment["PYTHONPATH"] = skill_root
    try:
        completed = subprocess.run(command, env=environment, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                f"完整扫描 worker 失败，退出码 {completed.returncode}；规则未执行完整，拒绝继续。"
            )
        with output.open("rb") as stream:
            report, revision = pickle.load(stream)
    finally:
        output.unlink(missing_ok=True)
    if report.interface_diff is None:
        raise RuntimeError("完整扫描 worker 缺少 interface diff，拒绝使用不完整扫描。")
    return report, revision


def scan_target_cached(
    args: Any,
) -> tuple[cli.AuditTarget, ScanReport, cli.InterfaceDiffReport, str, str]:
    """
    在强指纹完全一致时复用当前扫描，否则以隔离 worker 执行原始完整扫描。

    Args:
        args: 已由保留扫描内核 parser 解析的参数命名空间；动态 baseline 会保守绕过缓存。

    Returns:
        审计目标、完整 ScanReport、InterfaceDiffReport、实际 baseline revision 和缓存状态。
    """
    target = cli.resolve_target(args)
    if not args.diff_base or not target.git_enabled:
        report, revision = _run_worker(args, target)
        return target, report, report.interface_diff, revision, "BYPASS"

    profile = args.profile or "auto"
    focus_files = tuple(sorted(str(path.resolve()) for path in target.focus_files))
    release_seal = os.environ["REPO_QUALITY_GUARD_RELEASE_SEAL"]
    source_config = cli.api_catalog_config(target, args.profile)
    fingerprint = _worktree_fingerprint(
        target.root,
        profile,
        args.diff_base,
        args.staged,
        focus_files,
        release_seal,
        source_config,
    )
    snapshot_key = _SnapshotKey(
        profile, args.diff_base, args.staged, focus_files, release_seal, fingerprint
    )
    cached = _load_snapshot(target.root, snapshot_key)
    if cached is not None:
        report, revision = cached
        if report.interface_diff is None:
            raise RuntimeError("扫描快照缺少 interface diff，拒绝使用不完整缓存。")
        return (
            target,
            report,
            report.interface_diff,
            revision,
            f"HIT:{fingerprint[:12]}",
        )

    report, revision = _run_worker(args, target)
    _save_snapshot(target.root, report, revision, snapshot_key)
    return target, report, report.interface_diff, revision, f"MISS:{fingerprint[:12]}"
