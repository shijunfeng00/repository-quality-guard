from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .model import Finding


class RuffRunner:
    """
    负责调用 Ruff lint 与 formatter 并转换诊断结果。

    执行失败也会转换为显式发现，保证 Ruff 运行状态可审计。
    """

    def __init__(
        self,
        root: Path,
        *,
        cache_root: Path,
        environment: dict[str, str],
    ) -> None:
        """
        初始化 Ruff 执行器。

        Args:
            root: 目标仓库根目录。
            cache_root: uvx/ruff 可写缓存根目录。
            environment: 子进程基础环境变量。

        Returns:
            None。
        """
        self.root = root
        self.subprocess_env = dict(environment)
        self.subprocess_env["UV_CACHE_DIR"] = str(cache_root / "uv-cache")
        self.subprocess_env["UV_TOOL_DIR"] = str(cache_root / "uv-tools")
        self.subprocess_env["UV_PYTHON_INSTALL_DIR"] = str(cache_root / "uv-python")
        self.subprocess_env["XDG_CACHE_HOME"] = str(cache_root / "xdg-cache")
        self.subprocess_env["XDG_CONFIG_HOME"] = str(cache_root / "xdg-config")
        self.subprocess_env["XDG_DATA_HOME"] = str(cache_root / "xdg-data")

    def run(self, check_format: bool = False) -> list[Finding]:
        """
        运行 Ruff lint，并按需检查代码格式。

        Args:
            check_format: 是否同时执行 Ruff formatter 检查。

        Returns:
            Ruff 产生的统一发现列表。
        """
        command = self._resolve_ruff_command()
        if isinstance(command, Finding):
            return [command]
        findings = self._run_linter(command)
        if check_format:
            findings.extend(self._run_formatter(command))
        return findings

    def _resolve_ruff_command(self) -> list[str] | Finding:
        """
        解析可用 Ruff 命令，必要时从本地 wheelhouse 或 pip 安装。

        Returns:
            可执行 Ruff 命令前缀；失败时返回 QG900 发现。
        """
        executable = shutil.which("ruff")
        if executable is not None:
            return [executable]
        if importlib.util.find_spec("ruff") is not None:
            return [sys.executable, "-m", "ruff"]
        wheelhouse = self._local_wheelhouse()
        if wheelhouse is not None and self._install_ruff(
            ["--no-index", "--find-links", str(wheelhouse)]
        ):
            return [sys.executable, "-m", "ruff"]
        uvx = shutil.which("uvx")
        if uvx is not None:
            return [uvx, "ruff"]
        if self._install_ruff([]):
            return [sys.executable, "-m", "ruff"]
        return Finding(
            code="QG900",
            severity="error",
            confidence="high",
            path=".",
            line=1,
            column=1,
            message="未找到或无法安装 ruff，Ruff 没有真正运行。",
            suggestion=(
                "安装 Ruff，或让 `.skill.zip` 的 offline/wheelhouse 提供匹配平台的 Ruff wheel 后重跑。"
            ),
            source="integration",
        )

    def _local_wheelhouse(self) -> Path | None:
        """
        查找随 skill 包携带的 Ruff wheelhouse。

        Returns:
            存在 Ruff wheel 时返回目录，否则返回 None。
        """
        candidates = [Path(__file__).resolve().parents[2] / "offline" / "wheels"]
        for directory in candidates:
            if directory.is_dir() and any(directory.glob("ruff*.whl")):
                return directory
        return None

    def _install_ruff(self, extra_args: list[str]) -> bool:
        """
        尝试把 Ruff 安装到当前 Python 环境。

        Args:
            extra_args: 传递给 pip install 的额外参数。

        Returns:
            安装后当前 Python 能 import ruff 时返回 True。
        """
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *extra_args, "ruff==0.15.20"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            env=self.subprocess_env,
        )
        if result.returncode != 0:
            return False
        probe = subprocess.run(
            [sys.executable, "-c", "import ruff"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            env=self.subprocess_env,
        )
        return probe.returncode == 0

    def _run_linter(self, command: list[str]) -> list[Finding]:
        """
        执行 Ruff lint 并转换 JSON 诊断。

        Args:
            command: Ruff 命令前缀。

        Returns:
            Ruff lint 诊断转换后的发现列表。
        """
        result = subprocess.run(
            [
                *command,
                "check",
                ".",
                "--extend-exclude",
                ".agents",
                "--extend-exclude",
                ".git",
                "--output-format",
                "json",
                "--no-cache",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            env=self.subprocess_env,
        )
        if result.returncode not in {0, 1}:
            return [self._execution_failure("QG901", "Ruff lint 执行失败", result)]
        if result.returncode == 1 and not result.stdout.strip():
            return [self._execution_failure("QG901", "Ruff lint 执行失败", result)]
        try:
            diagnostics = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return [self._execution_failure("QG901", "Ruff JSON 输出无法解析", result)]
        findings: list[Finding] = []
        for diagnostic in diagnostics:
            location = diagnostic["location"]
            filename = Path(diagnostic["filename"])
            try:
                path = str(filename.resolve().relative_to(self.root.resolve()))
            except ValueError:
                path = str(filename)
            code = diagnostic["code"] or "RUFF"
            findings.append(
                Finding(
                    code=f"RUF:{code}",
                    severity="error" if code.startswith(("E", "F")) else "warning",
                    confidence="high",
                    path=path,
                    line=location["row"],
                    column=location["column"],
                    message=diagnostic["message"],
                    suggestion="运行 `ruff check . --fix` 处理可自动修复项，再人工审查剩余问题。",
                    source="ruff",
                    evidence={"url": diagnostic["url"]} if "url" in diagnostic else {},
                )
            )
        return findings

    def _run_formatter(self, command: list[str]) -> list[Finding]:
        """
        执行 Ruff formatter 检查并生成格式问题。

        Args:
            command: Ruff 命令前缀。

        Returns:
            格式检查问题列表。
        """
        result = subprocess.run(
            [
                *command,
                "format",
                "--check",
                ".",
                "--exclude",
                ".agents",
                "--exclude",
                ".git",
                "--no-cache",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            env=self.subprocess_env,
        )
        if result.returncode == 0:
            return []
        if result.returncode not in {1}:
            return [self._execution_failure("QG902", "Ruff format 执行失败", result)]
        candidates: list[str] = []
        output = result.stdout + "\n" + result.stderr
        for line in output.splitlines():
            marker = "Would reformat:"
            if marker in line:
                candidates.append(line.split(marker, 1)[1].strip())
        if not candidates:
            if "would be reformatted" not in output.lower():
                return [
                    self._execution_failure("QG902", "Ruff format 执行失败", result)
                ]
            candidates = ["."]
        return [
            Finding(
                code="RUF:FORMAT",
                severity="warning",
                confidence="high",
                path=path,
                line=1,
                column=1,
                message="文件不符合 Ruff formatter 输出。",
                suggestion="运行 `ruff format .`。",
                source="ruff",
            )
            for path in candidates
        ]

    def _execution_failure(
        self,
        code: str,
        message: str,
        result: subprocess.CompletedProcess[str],
    ) -> Finding:
        """
        把外部命令执行失败转换为统一发现。

        Args:
            code: 规则代码。
            message: 面向使用者的问题说明。
            result: 外部命令执行结果。

        Returns:
            表示执行失败的统一发现。
        """
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit={result.returncode}"
        )
        return Finding(
            code=code,
            severity="error",
            confidence="high",
            path=".",
            line=1,
            column=1,
            message=f"{message}: {detail}",
            source="integration",
        )
