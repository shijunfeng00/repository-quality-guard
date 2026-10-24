"""Top-level process ownership guards for Repository Quality Guard."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _log(message: str) -> None:
    """Write lifecycle diagnostics to the agent-visible error stream."""
    sys.stderr.write(f"[QG] {message}\n")
    sys.stderr.flush()


def process_alive(pid: int) -> bool:
    """Return whether a same-user process identifier still names a live process.

    Args:
        pid: Positive operating-system process identifier to inspect.

    Returns:
        True when the process is still live or inaccessible only because of
        permissions; False when the identifier is invalid or no process exists.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(
                kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                and exit_code.value == 259
            )
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate and reap all descendants belonging to a scanner-owned subprocess.

    Only call this with a dedicated private process group/session on POSIX;
    otherwise group termination could affect unrelated user processes.

    Args:
        process: Child started in an isolated group whose descendants are owned.

    Returns:
        None after best-effort termination and reaping; never owns host workers."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError as error:
            _log(f"清理时进程组已自然退出: {error}")
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired as caught:
            _log(f"温和终止等待超时，将升级为 SIGKILL: {caught}")
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError as error:
            _log(f"强制清理时进程组已自然退出: {error}")
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _process_cpu_ticks(pid: int) -> int | None:
    """Read observable child CPU progress without requiring a third-party dependency.

    Missing platform counters are not evidence of a hung child: callers may
    report its heartbeat but must not fail a healthy long-running job on that
    basis alone.
    """
    if sys.platform.startswith("linux"):
        # External tools can delegate CPU work to descendants while their root
        # waits. Include the isolated process group to avoid misclassifying a
        # healthy compiler/scanner subprocess as idle.
        total = 0
        observed = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "stat").read_text(encoding="ascii")
                fields = raw.rsplit(")", 1)[1].split()
                if int(fields[2]) != pid:
                    continue
                total += int(fields[11]) + int(fields[12])
                observed = True
            except (OSError, IndexError, ValueError):
                continue
        return total if observed else None
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            created = ctypes.c_ulonglong()
            exited = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            if kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return int(kernel.value + user.value)
        finally:
            kernel32.CloseHandle(handle)
    return None


def wait_for_active_process(
    process: subprocess.Popen[bytes],
    *,
    stall_timeout: float = 60.0,
    heartbeat_interval: float = 5.0,
    progress_file: Path | None = None,
) -> int:
    """Wait for a worker with no wall-clock cap while observable work continues.

    Progress consists of consumed CPU or explicit worker log updates. If
    neither is observable on this platform, elapsed time alone is insufficient
    evidence of a hung child. A failed child exit is detected at each poll.

    Args:
        process: Isolated subprocess to supervise.
        stall_timeout: Maximum seconds without observed activity, not wall time.
        heartbeat_interval: Seconds between completion and activity probes.
        progress_file: Optional worker log, which can indicate I/O progress.

    Returns:
        Child exit status, whether zero or nonzero.

    Raises:
        ValueError: Invalid timing configuration.
        RuntimeError: The worker stops progressing while still alive.
    """
    if stall_timeout <= 0 or heartbeat_interval <= 0:
        raise ValueError("stall timeout and heartbeat interval must be positive")
    started = last_progress = time.monotonic()
    previous_cpu = _process_cpu_ticks(process.pid)
    previous_log = None
    while process.poll() is None:
        try:
            process.wait(timeout=heartbeat_interval)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is not None:
            break
        now = time.monotonic()
        cpu = _process_cpu_ticks(process.pid)
        log = None
        if progress_file is not None:
            try:
                stat = progress_file.stat()
                log = (stat.st_size, stat.st_mtime_ns)
            except FileNotFoundError:
                pass
        active = cpu is not None and previous_cpu is not None and cpu > previous_cpu
        active = active or (
            log is not None and previous_log is not None and log != previous_log
        )
        if active:
            last_progress = now
        if cpu is not None:
            previous_cpu = cpu
        if log is not None:
            previous_log = log
        _log(
            f"子进程 pid={process.pid} 仍在执行，elapsed={now - started:.1f}s，"
            f"idle={now - last_progress:.1f}s，CPU活动={'有' if active else '未观测'}"
        )
        if cpu is not None and now - last_progress >= stall_timeout:
            raise RuntimeError(
                f"子进程 pid={process.pid} {now - last_progress:.1f}s 无可观测进展；"
                "已判定停滞，请检查该阶段日志及依赖"
            )
    result = process.returncode
    if result is None:
        raise RuntimeError("worker ended without an exit status")
    return result


def run_bounded_command(
    command: Sequence[str],
    timeout: float | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    *,
    stall_timeout: float | None = None,
    heartbeat_interval: float = 5.0,
) -> subprocess.CompletedProcess[str]:
    """Execute an isolated command with fail-fast exits and observable liveness.

    Short network/bootstrap probes may use an explicit timeout. Long-running
    scans instead specify stall_timeout: active CPU/output can run arbitrarily
    long without a fixed wall-clock cap.

    Args:
        command: Executable and its arguments.
        timeout: Optional hard deadline for short bootstrap/network operations.
        cwd: Execution directory.
        env: Environment variables, if the child needs overrides.
        input_text: Optional text sent to stdin.
        stall_timeout: Maximum inactivity for CPU/output-observable children.
        heartbeat_interval: Progress reporting and exit-check interval.

    Returns:
        Actual child status, stdout, and stderr.

    Raises:
        ValueError: Invalid timeout arguments.
        OSError: Could not start the child.
        RuntimeError: The child stopped showing any observable activity.
        subprocess.TimeoutExpired: A short explicit deadline expired.
    """
    if timeout is not None and timeout <= 0:
        raise ValueError("External command timeout must be positive")
    if stall_timeout is not None and stall_timeout <= 0:
        raise ValueError("External command stall timeout must be positive")
    if heartbeat_interval <= 0:
        raise ValueError("Heartbeat interval must be positive")
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        start_new_session=os.name != "nt",
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
    )
    started = last_progress = time.monotonic()
    previous_cpu = _process_cpu_ticks(process.pid)
    previous_bytes = 0
    first_input = input_text
    try:
        while True:
            now = time.monotonic()
            remaining = (timeout - (now - started)) if timeout is not None else None
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            interval = (
                min(heartbeat_interval, remaining)
                if remaining is not None
                else heartbeat_interval
            )
            try:
                output, errors = process.communicate(
                    input=first_input, timeout=interval
                )
                break
            except subprocess.TimeoutExpired as pending:
                first_input = None
                now = time.monotonic()
                cpu = _process_cpu_ticks(process.pid)
                partial_bytes = len(pending.output or b"") + len(pending.stderr or b"")
                activity = partial_bytes > previous_bytes
                activity = activity or (
                    cpu is not None and previous_cpu is not None and cpu > previous_cpu
                )
                if activity:
                    last_progress = now
                previous_bytes = max(previous_bytes, partial_bytes)
                if cpu is not None:
                    previous_cpu = cpu
                _log(
                    f"外部命令 {Path(command[0]).name} pid={process.pid} 执行中 "
                    f"elapsed={now - started:.1f}s idle={now - last_progress:.1f}s "
                    f"CPU活动={'有' if activity else '未观测'}"
                )
                if (
                    stall_timeout is not None
                    and cpu is not None
                    and now - last_progress >= stall_timeout
                ):
                    raise RuntimeError(
                        f"外部命令 {Path(command[0]).name} 无可观测进展 "
                        f"{now - last_progress:.1f}s；已清理进程树"
                    )
    except BaseException:
        terminate_process_tree(process)
        raise
    if process.returncode != 0:
        terminate_process_tree(process)
    return subprocess.CompletedProcess(command, process.returncode, output, errors)


def terminate_isolated_process_tree() -> None:
    """Stop the current isolated worker group after its supervising parent disappears.

    This hard-stop path is intentionally not a graceful normal-exit mechanism.
    It must never be called in a shared host process group.

    Returns:
        None if the platform cannot terminate; usually ends the worker tree."""
    if os.name == "nt":
        try:
            subprocess.Popen(
                ["taskkill", "/PID", str(os.getpid()), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        except OSError as caught:
            _log(f"Windows 进程树终止请求失败: {caught}")
        os._exit(143)
    if os.getpgrp() != os.getpid():
        # Never signal an ambient shell/pytest process group by mistake.
        os._exit(143)
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    except OSError:
        os._exit(143)
    os._exit(143)


def _parent_death_signal_handler(_signum: int, _frame: object) -> None:
    """Route the Linux parent-death signal through isolated-tree cleanup."""
    terminate_isolated_process_tree()


def _arm_linux_parent_death_signal(parent_pid: int) -> None:
    """Ask Linux to deliver SIGTERM if the current parent disappears."""
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    PR_SET_PDEATHSIG = 1
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, "prctl(PR_SET_PDEATHSIG) failed")
    # Parent may have died between getppid() and prctl().
    if os.getppid() != parent_pid:
        os.kill(os.getpid(), signal.SIGTERM)


def _install_parent_death_guard(*, terminate_process_group: bool) -> int:
    """Terminate this top-level QG process when its invoking host disappears.

    Returns:
        The parent PID captured at installation time.
    """
    parent_pid = os.getppid()
    if terminate_process_group:
        if os.name != "nt" and os.getpgrp() != os.getpid():
            raise RuntimeError(
                "process-group parent-death guard requires an isolated process session"
            )
        signal.signal(signal.SIGTERM, _parent_death_signal_handler)
    try:
        _arm_linux_parent_death_signal(parent_pid)
    except OSError as error:
        _log(f"父进程死亡信号注册失败，将使用轮询守护: {error}")

    def watch() -> None:
        """Watch the captured parent and terminate this audit after parent death.

        Returns:
            None. The watcher exits only after the parent disappears and the
            current audit has been asked to terminate.
        """
        while True:
            time.sleep(1)
            parent_alive = (
                process_alive(parent_pid)
                if os.name == "nt"
                else os.getppid() == parent_pid
            )
            if parent_alive:
                continue
            _log(
                f"调用 RQG 的父进程 pid={parent_pid} 已退出；"
                "正在终止本次审计并清理子进程，避免后台残留。"
            )
            if terminate_process_group:
                terminate_isolated_process_tree()
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except OSError:
                os._exit(143)
            return

    threading.Thread(target=watch, name="rqg-parent-watch", daemon=True).start()
    return parent_pid


def install_parent_death_guard() -> int:
    """Bind the CLI parent-liveness guard without claiming its process group.

    Returns:
        Platform-specific installation status of the parent-death guard."""
    return _install_parent_death_guard(terminate_process_group=False)


def install_isolated_parent_death_guard() -> int:
    """Bind parent-death cleanup for a subprocess running in its own process group.

    Returns:
        Platform-specific installation status; on parent loss the private
        worker group is terminated so grandchildren cannot remain orphaned."""
    return _install_parent_death_guard(terminate_process_group=True)
