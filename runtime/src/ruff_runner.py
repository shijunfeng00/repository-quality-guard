from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
import urllib.error
import urllib.request

from .model import Finding
from .process_lifecycle import run_bounded_command


class RuffRunner:
    """
    负责调用 Ruff lint 与 formatter 并转换诊断结果。

    执行失败也会转换为显式发现，保证 Ruff 运行状态可审计。
    """

    def __init__(
        self,
        root: Path,
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
        self._install_error = ""
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
            raise RuntimeError(command.message)
        findings = self._run_linter(command)
        failed = [finding for finding in findings if finding.code == "QG901"]
        if failed:
            raise RuntimeError(failed[0].message)
        if check_format:
            formatter_findings = self._run_formatter(command)
            failed = [
                finding for finding in formatter_findings if finding.code == "QG902"
            ]
            if failed:
                raise RuntimeError(failed[0].message)
            findings.extend(formatter_findings)
        return findings

    def _resolve_ruff_command(self) -> list[str] | Finding:
        """
        解析可用 Ruff 命令，必要时只从随包离线 wheelhouse 安装。

        Returns:
            可执行 Ruff 命令前缀；失败时返回 QG900 发现。
        """
        executable = shutil.which("ruff")
        if executable is not None:
            return [executable]
        if importlib.util.find_spec("ruff") is not None:
            return [sys.executable, "-m", "ruff"]
        directory = Path(__file__).resolve().parents[2] / "offline" / "wheelhouse"
        wheelhouse = (
            directory
            if directory.is_dir() and any(directory.glob("ruff*.whl"))
            else None
        )
        if wheelhouse is not None and self._install_ruff(
            ["--no-index", "--find-links", str(wheelhouse)]
        ):
            return [sys.executable, "-m", "ruff"]
        # The local installation is authoritative first. A missing/incompatible
        # local Wheel may be resolved online, but never with pip's default
        # multi-minute retry/connection waits.
        if self._online_registry_reachable() and self._install_ruff(
            ["--retries", "0", "--timeout", "3"]
        ):
            return [sys.executable, "-m", "ruff"]
        return Finding(
            code="QG900",
            severity="error",
            confidence="high",
            path=".",
            line=1,
            column=1,
            message=("Ruff 依赖安装失败，扫描未执行：" + self._install_error),
            suggestion="优先安装本地 Ruff Wheel；联网可用时允许从索引安装后重试。",
            source="integration",
        )

    def _online_registry_reachable(self) -> bool:
        """Check index reachability once before a potentially long pip invocation.

        Returns:
            True when an HTTP registry responds, including an authorization error.
        """
        index = self.subprocess_env.get("PIP_INDEX_URL", "https://pypi.org/simple/")
        if not index.startswith(("http://", "https://")):
            self._install_error = "unsupported HTTP package index; no online fallback"
            return False
        try:
            with urllib.request.urlopen(index, timeout=3) as response:
                response.read(1)
            return True
        except urllib.error.HTTPError:
            # HTTP 403/404 still demonstrates a working network; pip reports
            # authorization/package failures with its own exact diagnostics.
            return True
        except (OSError, urllib.error.URLError) as error:
            self._install_error = f"network probe failed: {type(error).__name__}"
            return False

    def _install_ruff(self, extra_args: list[str]) -> bool:
        """
        尝试把 Ruff 安装到当前 Python 环境。

        Args:
            extra_args: 传递给 pip install 的额外参数。

        Returns:
            安装后当前 Python 能 import ruff 时返回 True。
        """
        offline = "--no-index" in extra_args
        self._install_error = ""
        try:
            result = run_bounded_command(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    *extra_args,
                    "ruff==0.15.20",
                ],
                env=self.subprocess_env,
                timeout=12 if offline else 15,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self._install_error = f"pip {'offline' if offline else 'online'}: {error}"
            return False
        if result.returncode != 0:
            self._install_error = (
                f"pip {'offline' if offline else 'online'} exit={result.returncode}: "
                f"{(result.stderr or result.stdout)[-1000:]}"
            )
            return False
        try:
            probe = run_bounded_command(
                [sys.executable, "-c", "import ruff"],
                env=self.subprocess_env,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self._install_error = f"Ruff import probe: {error}"
            return False
        if probe.returncode != 0:
            self._install_error = (
                f"Ruff import probe exit={probe.returncode}: {probe.stderr[-1000:]}"
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
        try:
            result = run_bounded_command(
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
                env=self.subprocess_env,
                stall_timeout=60,
                heartbeat_interval=10,
            )
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            return [self._command_failure("QG901", "Ruff lint 子进程失败", error)]
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
        try:
            result = run_bounded_command(
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
                env=self.subprocess_env,
                stall_timeout=60,
                heartbeat_interval=10,
            )
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            return [self._command_failure("QG902", "Ruff format 子进程失败", error)]
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

    @staticmethod
    def _command_failure(code: str, message: str, error: BaseException) -> Finding:
        """Report execution errors immediately, preserving their real exception."""
        return Finding(
            code=code,
            severity="error",
            confidence="high",
            path=".",
            line=1,
            column=1,
            message=f"{message}: {type(error).__name__}: {error}",
            source="integration",
        )

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
