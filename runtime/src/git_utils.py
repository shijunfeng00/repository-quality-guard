from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path


def run_readonly_git(
    root: Path, command: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    """
    执行不会修改仓库状态的 Git 查询命令。

    Args:
        root: Git 工作树根目录。
        command: 传给 Git 的只读子命令和参数序列。

    Returns:
        保留 stdout、stderr 和退出码的命令结果。
    """
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *command],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )


def run_readonly_git_bytes(
    root: Path, command: Sequence[str]
) -> subprocess.CompletedProcess[bytes]:
    """
    执行不会修改仓库状态且需要保留原始字节的 Git 查询。

    Args:
        root: Git 工作树根目录。
        command: 传给 Git 的只读子命令和参数序列。

    Returns:
        以 bytes 保留 stdout、stderr 和退出码的命令结果。
    """
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *command],
        cwd=root,
        capture_output=True,
        check=False,
    )
