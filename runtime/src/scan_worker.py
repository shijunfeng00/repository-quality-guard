"""隔离执行一次完整仓库扫描，避免重 AST 生命周期污染报告验证阶段。"""

from __future__ import annotations

import argparse
import json
import os
import faulthandler
import pickle
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

from . import cli


def build_parser() -> argparse.ArgumentParser:
    """
    构造仅供 workflow 内部调用的扫描 worker 参数。

    Returns:
        固定内部参数契约的 ArgumentParser。
    """
    parser = argparse.ArgumentParser(prog="repo-quality-guard-scan-worker")
    parser.add_argument("--path", required=True)
    parser.add_argument("--profile", default="")
    parser.add_argument("--resolved-profile-reference", default="")
    parser.add_argument("--resolved-profile-name", default="")
    parser.add_argument("--resolved-profile-source", default="")
    parser.add_argument("--diff-base", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--status", default="")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--files", default="")
    return parser


@dataclass(frozen=True, slots=True)
class _Status:
    """One complete worker status envelope shared through atomic JSON."""

    state: str
    phase: str
    elapsed_seconds: float
    error_type: str = ""
    message: str = ""
    traceback_tail: str = ""


def _write_status(path: Path, status: _Status) -> None:
    """Atomically publish worker progress without leaving a partial JSON record."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    payload = {**asdict(status), "pid": os.getpid(), "updated_at": time.time()}
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _legacy_args(options: argparse.Namespace) -> argparse.Namespace:
    """把 worker 参数转换为保留扫描内核的参数契约。"""
    argv: list[str] = []
    if options.files:
        argv.extend(["--files", options.files])
    else:
        argv.append(options.path)
    if options.profile:
        argv.extend(["--profile", options.profile])
    if options.diff_base:
        argv.extend(["--diff-base", options.diff_base])
    if options.staged:
        argv.append("--staged")
    args = cli.build_parser().parse_args(argv)
    if options.resolved_profile_source:
        args.resolved_profile_reference = options.resolved_profile_reference
        args.resolved_profile_name = options.resolved_profile_name
        args.resolved_profile_source = options.resolved_profile_source
    return args


def main(argv: list[str] | None = None) -> int:
    """
    运行一次完整扫描并把 ScanReport 与 revision 写入父进程指定的临时文件。

    Args:
        argv: 可选内部 worker 参数；为空时读取当前进程命令行。

    Returns:
        0 表示完整扫描和结果写入成功；异常由进程直接失败并保留诊断。
    """
    options = build_parser().parse_args(argv)
    if not options.status or not options.parent_pid:
        raise RuntimeError("scan worker 缺少父进程状态通道，不允许独立启动。")
    faulthandler.dump_traceback_later(60, repeat=True, file=sys.stderr)
    started = time.perf_counter()
    status = Path(options.status)
    phase = ["scan_target"]
    stopped = threading.Event()

    def heartbeat() -> None:
        """Publish liveness and exit when the owning parent disappears.

        Returns:
            None after the terminal status stops the heartbeat loop.
        """
        while not stopped.wait(5):
            if os.getppid() != options.parent_pid:
                os._exit(3)
            _write_status(
                status,
                _Status("RUNNING", phase[0], round(time.perf_counter() - started, 1)),
            )

    _write_status(status, _Status("RUNNING", phase[0], 0))
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        _target, report, _interface = cli.scan_target(_legacy_args(options))
        revision = (
            report.baseline.revision
            if report.baseline is not None
            else options.diff_base
        )
        phase[0] = "serialize_result"
        output = Path(options.output)
        with output.open("wb") as stream:
            pickle.dump((report, revision), stream, protocol=pickle.HIGHEST_PROTOCOL)
            stream.flush()
            os.fsync(stream.fileno())
        elapsed = time.perf_counter() - started
        stopped.set()
        thread.join(timeout=1)
        _write_status(status, _Status("SUCCEEDED", phase[0], round(elapsed, 1)))
        sys.stderr.write(
            f"Quality Guard scan DONE wall={elapsed:.3f}s findings={len(report.findings)}\n"
        )
        sys.stderr.flush()
        return 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        AssertionError,
    ) as error:
        stopped.set()
        thread.join(timeout=1)
        _write_status(
            status,
            _Status(
                "FAILED",
                phase[0],
                round(time.perf_counter() - started, 1),
                type(error).__name__,
                str(error),
                traceback.format_exc()[-4000:],
            ),
        )
        traceback.print_exc()
        sys.stderr.flush()
        return 2
    finally:
        stopped.set()


if __name__ == "__main__":
    os._exit(main())
