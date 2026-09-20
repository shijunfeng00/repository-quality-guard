"""隔离执行一次完整仓库扫描，避免重 AST 生命周期污染报告验证阶段。"""

from __future__ import annotations

import argparse
import os
import faulthandler
import pickle
import sys
import time
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
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--files", default="")
    return parser


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
    faulthandler.dump_traceback_later(60, repeat=True, file=sys.stderr)
    started = time.perf_counter()
    _target, report, _interface = cli.scan_target(_legacy_args(options))
    revision = (
        report.baseline.revision if report.baseline is not None else options.diff_base
    )
    output = Path(options.output)
    with output.open("wb") as stream:
        pickle.dump((report, revision), stream, protocol=pickle.HIGHEST_PROTOCOL)
    elapsed = time.perf_counter() - started
    sys.stderr.write(
        f"Quality Guard scan DONE wall={elapsed:.3f}s findings={len(report.findings)}\n"
    )
    sys.stderr.flush()
    return 0


if __name__ == "__main__":
    os._exit(main())
