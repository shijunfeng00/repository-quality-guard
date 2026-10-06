"""当前工作树完整扫描快照：以强指纹安全复用同一 release 的 ScanReport。"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import cli
from .config import GuardConfig
from .git_utils import run_readonly_git_bytes
from .model import ScanReport
from .process_lifecycle import process_alive
from .source_inputs import source_language

SNAPSHOT_SCHEMA = "repository-quality-guard/current-scan-v2"
_RELEVANT_EXACT = {
    "README.md",
    "README_zh.md",
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
        root, ("rev-parse", "--verify", f"{revision}^{{commit}}")
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"无法解析 Git revision: {revision}")
    return result.stdout.decode("ascii").strip()


def _relevant_worktree_paths(root: Path, config: GuardConfig) -> tuple[str, ...]:
    """列出会影响现有扫描、配置、README 同步和 AGENTS 唯一性规则的输入。"""
    listed = run_readonly_git_bytes(
        root, ("ls-files", "--cached", "--others", "--exclude-standard", "-z")
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
        index = run_readonly_git_bytes(root, ("ls-files", "-s", "-z"))
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


def _lock_owner_pid(path: Path) -> int | None:
    """Read and validate the PID recorded by a scan single-flight lock owner."""
    owner = path / "owner.json"
    try:
        payload = json.loads(owner.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        type(payload) is not dict
        or "pid" not in payload
        or type(payload["pid"]) is not int
    ):
        raise RuntimeError(f"scan single-flight owner 协议非法: {owner}")
    return payload["pid"] if payload["pid"] > 0 else None


def _acquire_single_flight(
    root: Path, key: _SnapshotKey
) -> tuple[Path, bool, tuple[ScanReport, str] | None]:
    """Serialize cold scans for one repo/profile/baseline and reuse the winner snapshot."""
    identity = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:24]
    lock = _private_cache_dir("scan-single-flight") / f"{identity}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    waited = False
    last_log = 0.0
    started = time.monotonic()
    while True:
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError:
            owner_pid = _lock_owner_pid(lock)
            if owner_pid is None:
                try:
                    lock_age = time.time() - lock.stat().st_mtime
                except FileNotFoundError:
                    continue
                if lock_age > 5:
                    shutil.rmtree(lock, ignore_errors=True)
                    sys.stderr.write(
                        "[QG] 清理无 owner 的失效 scan single-flight 锁。\n"
                    )
                    sys.stderr.flush()
                    continue
            if owner_pid is not None and not process_alive(owner_pid):
                try:
                    shutil.rmtree(lock)
                    sys.stderr.write(
                        f"[QG] 清理失效 scan single-flight 锁：owner pid={owner_pid} 已退出。\n"
                    )
                    sys.stderr.flush()
                except FileNotFoundError:
                    pass
                continue
            cached = _load_snapshot(root, key)
            if cached is not None:
                return lock, False, cached
            now = time.monotonic()
            if not waited or now - last_log >= 10:
                detail = f" pid={owner_pid}" if owner_pid is not None else ""
                sys.stderr.write(
                    "[QG] 已有同仓库审计正在执行 cold scan；正在排队等待复用结果"
                    f"{detail} elapsed={now - started:.1f}s。\n"
                )
                sys.stderr.flush()
                waited = True
                last_log = now
            time.sleep(0.5)
            continue
        owner = {
            "pid": os.getpid(),
            "started_at": time.time(),
            "repository": str(root.resolve()),
            "profile": key.profile,
            "base_revision": key.base_revision,
        }
        (lock / "owner.json").write_text(
            json.dumps(owner, ensure_ascii=False), encoding="utf-8"
        )
        return lock, True, None
    raise RuntimeError(
        "scan single-flight acquisition ended without an owner or cached result"
    )


def _cleanup_stale_worker_dirs(worker_dir: Path) -> None:
    """Remove abandoned worker IPC directories without racing a starting worker."""
    now = time.time()
    for candidate in worker_dir.glob("scan-*"):
        if not candidate.is_dir():
            continue
        try:
            age_seconds = max(0.0, now - candidate.stat().st_mtime)
        except FileNotFoundError:
            continue
        status_path = candidate / "status.json"
        if not status_path.is_file():
            if age_seconds < 60.0:
                continue
            pid = 0
        else:
            try:
                pid = int(_worker_status(status_path)["pid"])
            except RuntimeError:
                if age_seconds < 60.0:
                    continue
                pid = 0
        if pid > 0 and process_alive(pid):
            continue
        try:
            shutil.rmtree(candidate)
        except FileNotFoundError:
            pass


def _run_worker(args: Any, target: cli.AuditTarget) -> tuple[ScanReport, str]:
    """在短生命周期子进程执行原始 scan_target，并返回完整报告与实际 revision。"""
    worker_dir = _private_cache_dir("scan-worker")
    _cleanup_stale_worker_dirs(worker_dir)
    with tempfile.TemporaryDirectory(prefix="scan-", dir=worker_dir) as temporary:
        return _run_worker_in_dir(args, target, Path(temporary))


def _worker_status(path: Path) -> dict[str, Any]:
    """Read one atomic worker envelope; an absent initial status is STARTING."""
    if not path.is_file():
        return {
            "state": "STARTING",
            "phase": "starting",
            "message": "尚未写入状态",
            "pid": 0,
            "updated_at": time.time(),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"扫描 worker 状态文件损坏: {path}") from error
    required = {"state", "phase", "message", "pid", "updated_at"}
    if type(payload) is not dict or not required.issubset(payload):
        raise RuntimeError(f"扫描 worker 状态协议非法: {path}")
    if type(payload["pid"]) is not int or type(payload["updated_at"]) not in {
        int,
        float,
    }:
        raise RuntimeError(f"扫描 worker 状态字段类型非法: {path}")
    return payload


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop the worker process group and reap its root process."""
    if os.name == "nt":
        if process.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _worker_command(
    args: Any, target: cli.AuditTarget, output: Path, status_path: Path
) -> list[str]:
    """Carry the resolved focus, baseline and Profile into the isolated scanner.

    Args:
        args: Parsed audit options.
        target: Resolved target and focus scope.
        output: Private result path.
        status_path: Private worker status path.

    Returns:
        Complete command for the short-lived worker.
    """
    # Preserve the user's focus scope across the worker boundary.  Passing only
    # ``target.root`` widens positional single-file and Git-subdirectory audits
    # back to the whole repository/directory inside the worker.
    worker_path = target.focus_roots[0] if len(target.focus_roots) == 1 else target.root
    worker_files = args.files
    if not worker_files and target.focus_files:
        worker_files = ",".join(str(path) for path in sorted(target.focus_files))
    command = [
        sys.executable,
        "-m",
        "runtime.src.scan_worker",
        "--path",
        str(worker_path),
        "--output",
        str(output),
        "--status",
        str(status_path),
        "--parent-pid",
        str(os.getpid()),
    ]
    selection = cli.resolve_profile_reference(target.root, args.profile)
    command.extend(["--resolved-profile-source", selection.source])
    command.extend(["--resolved-profile-name", selection.name])
    if selection.reference:
        command.extend(["--resolved-profile-reference", selection.reference])
    if args.diff_base:
        command.extend(["--diff-base", args.diff_base])
    if args.staged:
        command.append("--staged")
    if worker_files:
        command.extend(["--files", worker_files])
    return command


def _wait_for_worker(
    process: subprocess.Popen[bytes],
    status_path: Path,
    started: float,
    timeout: float | None,
) -> None:
    """Monitor worker startup, diagnostic timeout, and heartbeat freshness.

    Args:
        process: Active isolated scan worker.
        status_path: Atomic worker status envelope path.
        started: Monotonic worker start time.
        timeout: Optional diagnostic timeout configured by the operator.

    Returns:
        None after the worker exits normally.
    """
    last_progress = started
    while process.poll() is None:
        now = time.monotonic()
        if not status_path.is_file() and now - started >= 20:
            raise RuntimeError(
                "扫描 worker 启动超过 20 秒仍未发布状态；正在终止子进程树。"
            )
        if timeout is not None and now - started >= timeout:
            status = _worker_status(status_path)
            raise RuntimeError(
                f"扫描超时 {timeout:g}s，phase={status['phase']}；"
                "审计未完成，不得当作通过。"
            )
        if now - last_progress >= 15:
            status = _worker_status(status_path)
            heartbeat_age = max(0.0, time.time() - float(status["updated_at"]))
            if heartbeat_age > 20:
                raise RuntimeError(
                    f"扫描 worker 心跳失联 {heartbeat_age:.1f}s，"
                    f"phase={status['phase']}；正在终止子进程树。"
                )
            sys.stderr.write(
                f"[QG] scan heartbeat pid={process.pid} "
                f"phase={status['phase']} elapsed={now - started:.1f}s\n"
            )
            sys.stderr.flush()
            last_progress = now
        time.sleep(0.2)


def _run_worker_in_dir(
    args: Any, target: cli.AuditTarget, temporary: Path
) -> tuple[ScanReport, str]:
    """Run and monitor one scan worker with a private, disposable IPC directory."""
    output = temporary / "result.pkl"
    status_path = temporary / "status.json"
    log_path = temporary / "worker.log"
    command = _worker_command(args, target, output, status_path)
    environment = os.environ.copy()
    skill_root = str(Path(__file__).resolve().parents[2])
    # worker 只需要 Guard 自身源码；覆盖继承的 PYTHONPATH，避免外部模块污染扫描运行时。
    environment["PYTHONPATH"] = skill_root
    timeout_key = "RQG_SCAN_TIMEOUT_SECONDS"
    timeout = None
    if timeout_key in os.environ:
        timeout = float(os.environ[timeout_key])
        if timeout <= 0:
            raise ValueError("RQG_SCAN_TIMEOUT_SECONDS 必须大于 0。")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    started = time.monotonic()
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            command,
            env=environment,
            stdout=log,
            stderr=log,
            start_new_session=os.name != "nt",
            creationflags=flags,
        )
        sys.stderr.write(f"[QG] scan worker started pid={process.pid}\n")
        sys.stderr.flush()
        previous_handler = None
        if threading.current_thread() is threading.main_thread():
            previous_handler = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, signal.default_int_handler)
        try:
            _wait_for_worker(process, status_path, started, timeout)
            status = _worker_status(status_path)
            if (
                process.returncode != 0
                or status["state"] != "SUCCEEDED"
                or not output.is_file()
            ):
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                raise RuntimeError(
                    f"完整扫描 worker 失败，退出码 {process.returncode}，"
                    f"phase={status['phase']}，"
                    f"error={status['message']}。\n{tail}"
                )
            with output.open("rb") as stream:
                report, revision = pickle.load(stream)
            sys.stderr.write(
                f"[QG] scan worker completed pid={process.pid} elapsed={time.monotonic() - started:.1f}s\n"
            )
            sys.stderr.flush()
        except KeyboardInterrupt as error:
            raise RuntimeError("审计被中断；扫描子进程树已清理。") from error
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGTERM, previous_handler)
            _terminate_tree(process)
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

    selection = cli.resolve_profile_reference(target.root, args.profile)
    profile = selection.reference or selection.source
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

    lock, owned, queued = _acquire_single_flight(target.root, snapshot_key)
    if queued is not None:
        report, revision = queued
        if report.interface_diff is None:
            raise RuntimeError("排队复用的扫描快照缺少 interface diff。")
        return (
            target,
            report,
            report.interface_diff,
            revision,
            f"WAIT-HIT:{fingerprint[:12]}",
        )
    try:
        # A scan may have completed between the initial cache miss and lock acquisition.
        cached = _load_snapshot(target.root, snapshot_key)
        if cached is not None:
            report, revision = cached
            return (
                target,
                report,
                report.interface_diff,
                revision,
                f"LOCK-HIT:{fingerprint[:12]}",
            )
        report, revision = _run_worker(args, target)
        _save_snapshot(target.root, report, revision, snapshot_key)
        return (
            target,
            report,
            report.interface_diff,
            revision,
            f"MISS:{fingerprint[:12]}",
        )
    finally:
        if owned:
            try:
                shutil.rmtree(lock)
            except FileNotFoundError:
                pass
