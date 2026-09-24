"""Top-level process ownership guards for Repository Quality Guard."""

from __future__ import annotations

import ctypes
import os
import signal
import sys
import threading
import time


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


def install_parent_death_guard() -> int:
    """Terminate this top-level QG process when its invoking host disappears.

    Returns:
        The parent PID captured at installation time.
    """
    parent_pid = os.getppid()
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
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except OSError:
                os._exit(143)
            return

    threading.Thread(target=watch, name="rqg-parent-watch", daemon=True).start()
    return parent_pid
