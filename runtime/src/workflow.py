"""Repository Quality Guard Agent-facing 四命令工作流。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import cli as legacy_cli
from .api_catalog import build_api_catalog, search_api_catalog
from .compact_report import (
    REPORT_FILENAME,
    render_compact_report,
    semantic_items,
    validate_compact_report,
)
from .gate_status import code_status
from .scan_snapshot import scan_target_cached

REPORT_GATE_EXIT_CODE = 3
REVIEW_REQUIRED_EXIT_CODE = 5


def _write_cli_line(message: str, *, error: bool = False) -> None:
    """向 CLI 协议流写一行文本；stderr 只承载错误信息。"""
    stream = sys.stderr if error else sys.stdout
    stream.write(message + "\n")


def _common_target_options(parser: argparse.ArgumentParser) -> None:
    """向子命令加入统一目标、Git、profile 与可选文件范围参数。"""
    parser.add_argument("path", nargs="?", default=".", help="目标 Git 仓库或子目录。")
    parser.add_argument("--profile", metavar="PROFILE")
    parser.add_argument("--diff-base", default=None, metavar="COMMIT")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument(
        "--files", default=None, metavar="FILES", help="逗号分隔的文件范围。"
    )


def build_parser() -> argparse.ArgumentParser:
    """
    构造只有四个一级动作的稳定 Agent-facing CLI。

    Returns:
        配置完成的 ``ArgumentParser``。
    """
    parser = argparse.ArgumentParser(
        prog="quality-guard",
        description="Repository Quality Guard：文档生成、能力检索、审计与最终只读验收。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser(
        "doc-generate", help="生成或增量刷新接口文档/catalog。"
    )
    _common_target_options(generate)
    generate.add_argument(
        "--output", default=None, help="接口目录输出路径；默认 docs/api-reference。"
    )

    search = subparsers.add_parser("doc-search", help="BM25 检索已有接口能力。")
    _common_target_options(search)
    search.add_argument("query", help="自然语言、符号或类型能力描述。")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument(
        "--output", default=None, help="接口目录输出路径；默认 docs/api-reference。"
    )

    audit = subparsers.add_parser("audit", help="扫描当前修改并生成紧凑语义审计报告。")
    _common_target_options(audit)
    audit.add_argument(
        "--report-output", default=None, help="报告路径；默认仓库根 修改说明.md。"
    )

    verify = subparsers.add_parser("verify", help="最终只读重扫并验证报告。")
    _common_target_options(verify)
    verify.add_argument(
        "--report-output", default=None, help="报告路径；默认仓库根 修改说明.md。"
    )
    return parser


def _scoped_file_arguments(options: argparse.Namespace) -> str:
    """把相对 ``--files`` 转为以位置目标目录为基准的绝对文件列表。"""
    base = Path(options.path).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"目标目录不存在: {base}")
    resolved: list[str] = []
    for raw in options.files.split(","):
        value = raw.strip()
        if not value:
            continue
        candidate = Path(value).expanduser()
        path = (
            candidate.resolve()
            if candidate.is_absolute()
            else (base / candidate).resolve()
        )
        resolved.append(str(path))
    if not resolved:
        raise ValueError("--files 没有解析到任何文件")
    return ",".join(resolved)


def _legacy_args(
    options: argparse.Namespace, *, include_files: bool = True
) -> argparse.Namespace:
    """把四命令公共参数转换为现有扫描内核的 ``argparse.Namespace``。"""
    argv: list[str] = []
    if include_files and options.files:
        argv.extend(["--files", _scoped_file_arguments(options)])
    else:
        argv.append(options.path)
    if options.profile:
        argv.extend(["--profile", options.profile])
    if options.diff_base:
        argv.extend(["--diff-base", options.diff_base])
    if options.staged:
        argv.append("--staged")
    return legacy_cli.build_parser().parse_args(argv)


def _doc_target(options: argparse.Namespace) -> legacy_cli.AuditTarget:
    """解析文档命令仓库根；``--files`` 只决定刷新范围。"""
    return legacy_cli.resolve_target(_legacy_args(options, include_files=False))


def _resolve_output(root: Path, value: str | None) -> Path:
    """解析接口目录输出路径。"""
    if value is None:
        return root / "docs" / "api-reference"
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def _resolve_report(root: Path, value: str | None) -> Path:
    """解析审计报告输出路径。"""
    if value is None:
        return root / REPORT_FILENAME
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def _changed_paths(value: str | None) -> tuple[str, ...] | None:
    """解析 doc-generate/doc-search 的显式增量文件范围。"""
    if not value:
        return None
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _append_search_ledger(
    output: Path, query: str, hits: list[object], metrics: dict[str, object]
) -> None:
    """把实际 doc-search 查询与 TopK 结果追加到机器 ledger。"""
    output.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema": "repository-quality-guard/api-search-ledger-v1",
        "time_ns": time.time_ns(),
        "query": query,
        "metrics": metrics,
        "results": [
            {
                "rank": rank,
                "score": hit.score,
                "symbol": hit.record.symbol,
                "kind": hit.record.kind,
                "path": hit.record.path,
                "line": hit.record.line,
                "signature": hit.record.signature,
            }
            for rank, hit in enumerate(hits, 1)
        ],
    }
    ledger = output / "search-ledger.jsonl"
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        )


def _run_doc_generate(options: argparse.Namespace) -> int:
    """执行全量或范围增量接口文档刷新。"""
    target = _doc_target(options)
    config = legacy_cli.api_catalog_config(target, options.profile)
    output = _resolve_output(target.root, options.output)
    metrics = build_api_catalog(
        target.root,
        output,
        config,
        changed_paths=_changed_paths(options.files),
    )
    _write_cli_line(
        "doc-generate PASS "
        f"mode={metrics['mode']} symbols={metrics['symbols']} "
        f"changed_files={metrics['changed_files']} hashed_files={metrics['hashed_files']} "
        f"total={metrics['total_seconds']:.4f}s index={metrics['index_seconds']:.4f}s"
    )
    _write_cli_line(str(output / "INDEX.md"))
    return 0


def _run_doc_search(options: argparse.Namespace) -> int:
    """执行自动增量保鲜后的 BM25 TopK 能力检索。"""
    if options.limit <= 0:
        raise ValueError("--limit 必须大于 0")
    target = _doc_target(options)
    config, profile = legacy_cli.api_catalog_context(target, options.profile)
    output = _resolve_output(target.root, options.output)
    hits, metrics = search_api_catalog(
        target.root,
        output,
        config,
        options.query,
        limit=options.limit,
        changed_paths=_changed_paths(options.files),
    )
    hits = legacy_cli._apply_search_strategy(
        profile, options.query, hits, root=target.root, limit=options.limit
    )
    _append_search_ledger(output, options.query, hits, metrics)
    _write_cli_line(
        "doc-search PASS "
        f"refresh={metrics['refresh_mode']} changed_files={metrics['changed_files']} "
        f"prepare={metrics['catalog_prepare_seconds']:.4f}s "
        f"index={metrics['index_seconds']:.4f}s "
        f"search={metrics['search_seconds'] * 1000:.3f}ms total={metrics['total_seconds']:.4f}s"
    )
    for rank, hit in enumerate(hits, 1):
        summary = hit.record.docstring.splitlines()[0] if hit.record.docstring else ""
        _write_cli_line(
            f"{rank:>2}. {hit.record.symbol} score={hit.score:.3f} "
            f"{hit.record.path}:{hit.record.line} [{hit.record.visibility}] {summary}"
        )
    if not hits:
        _write_cli_line("未召回候选；不得据此认定能力不存在，必须继续直接阅读源码。")
    else:
        _write_cli_line(
            "候选仅用于高召回；必须阅读 TopK 真实源码与调用方后再作复用/所有权裁决。"
        )
    return 0


def _scan_for_workflow(
    options: argparse.Namespace,
) -> tuple[
    legacy_cli.AuditTarget,
    legacy_cli.ScanReport,
    legacy_cli.InterfaceDiffReport,
    str,
]:
    """使用强指纹扫描快照；失效时完整重扫且不减少任何规则。"""
    target, report, interface, revision, cache_status = scan_target_cached(
        _legacy_args(options)
    )
    _write_cli_line(f"scan-snapshot {cache_status}")
    return target, report, interface, revision


def _run_audit(options: argparse.Namespace) -> int:
    """生成机器事实与人工语义字段分离的紧凑审计报告。"""
    target, report, _interface_diff, revision = _scan_for_workflow(options)
    report_path = _resolve_report(target.root, options.report_output)
    existing = (
        report_path.read_text(encoding="utf-8") if report_path.is_file() else None
    )
    text = render_compact_report(report, revision=revision, existing=existing)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")
    validation = validate_compact_report(text, report, revision=revision)
    counts = dict.fromkeys(("critical", "error", "warning", "info"), 0)
    for finding in report.findings:
        if not finding.code.startswith("QG98"):
            counts[finding.severity] += 1
    sem = semantic_items(report)
    delta_sem = sum(item.delta for item in sem)
    touched_historical = sum(item.touched_historical for item in sem)
    _write_cli_line(
        "audit GENERATED "
        f"static={code_status(report)} "
        f"C/E/W/I={counts['critical']}/{counts['error']}/{counts['warning']}/{counts['info']} "
        f"semantic_delta={delta_sem} touched_historical={touched_historical} "
        f"report_issues={len(validation.issues)}"
    )
    _write_cli_line(str(report_path))
    if validation.issues:
        _write_cli_line(
            "audit UNVERIFIED：报告仍待语义/测试契约/Reduction/Commit 填写；不得把 audit 当最终通过。",
            error=True,
        )
        for issue in validation.issues[:20]:
            _write_cli_line(f"- {issue}", error=True)
        if len(validation.issues) > 20:
            _write_cli_line(
                f"- 其余 {len(validation.issues) - 20} 项见报告并由 verify 全量校验。",
                error=True,
            )
        return REPORT_GATE_EXIT_CODE
    _write_cli_line(
        "audit READY_FOR_VERIFY：报告结构已完整；仍必须执行 verify 才有最终状态。"
    )
    return 0


def _run_verify(options: argparse.Namespace) -> int:
    """只读重扫源码并验证最新报告、语义账本与 Reduction Pass。"""
    target, report, _interface_diff, revision = _scan_for_workflow(options)
    report_path = _resolve_report(target.root, options.report_output)
    if not report_path.is_file():
        _write_cli_line(f"verify REJECT：缺少报告 {report_path}", error=True)
        return REPORT_GATE_EXIT_CODE
    text = report_path.read_text(encoding="utf-8")
    validation = validate_compact_report(text, report, revision=revision)
    static_status = code_status(report)
    if validation.issues:
        _write_cli_line("verify REJECT：报告契约未通过", error=True)
        for issue in validation.issues:
            _write_cli_line(f"- {issue}", error=True)
        return REPORT_GATE_EXIT_CODE
    if static_status == "REJECT" or validation.semantic_status == "REJECT":
        _write_cli_line(
            f"verify REJECT static={static_status} semantic={validation.semantic_status}"
        )
        return 1
    if static_status == "REVIEW_REQUIRED":
        _write_cli_line("verify REVIEW_REQUIRED：静态接口/协议账本仍需人工确认。")
        return REVIEW_REQUIRED_EXIT_CODE
    _write_cli_line("verify PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    """
    执行四个稳定 Agent-facing 命令。

    Args:
        argv: 可选命令行参数；为 None 时由 argparse 使用进程参数。

    Returns:
        命令退出码；0 表示命令完成，其他值表示门禁或执行错误。
    """
    options = build_parser().parse_args(argv)
    try:
        integrity = legacy_cli.integrity_gate()
        if integrity is not None:
            return integrity
        if options.command == "doc-generate":
            return _run_doc_generate(options)
        if options.command == "doc-search":
            return _run_doc_search(options)
        if options.command == "audit":
            return _run_audit(options)
        if options.command == "verify":
            return _run_verify(options)
        raise ValueError(f"未知命令: {options.command}")
    except (OSError, RuntimeError, ValueError) as error:
        _write_cli_line(f"quality-guard 执行失败: {error}", error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
