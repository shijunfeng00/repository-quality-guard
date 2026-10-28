"""查询权威 QG 修复规则和同一源码快照的静态仓库调用证据。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _ensure_source_imports() -> None:
    """在独立 CLI 模式使用真实 Skill 根目录而非目标仓库的模块。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach evidence query flags to the official parser.

    Args:
        parser: Public CLI parser that owns evidence query options.

    Returns:
        None; registers options on the supplied parser.
    """
    parser.add_argument("--rule", action="append", default=[], metavar="QG003")
    parser.add_argument(
        "--api", metavar="CAPABILITY", help="从已有 API Catalog BM25 检索能力"
    )
    parser.add_argument("--api-limit", type=int, default=5, help="API TopK，1..10")
    parser.add_argument(
        "--repo", type=Path, help="源码仓库根目录（仅查询规则时可省略）"
    )
    parser.add_argument("--symbol", help="符号完整 ID；短名称只有唯一匹配时可用")
    parser.add_argument(
        "--graph-stats", action="store_true", help="报告调用图连通性和关系类型统计"
    )
    parser.add_argument("--file", help="仓库相对文件路径，配合 --line 定位语义所有者")
    parser.add_argument("--line", type=int, help="源码 1-based 行号")
    parser.add_argument("--name", help="指定行内函数/类/变量名")
    parser.add_argument(
        "--kind", choices=("auto", "function", "class", "variable"), default="auto"
    )
    parser.add_argument("--variable", help="追踪函数形参/局部变量或 self.field 的来源")
    parser.add_argument(
        "--trace", action="store_true", help="输出带调用点上下文和变量传导的静态 Trace"
    )
    parser.add_argument(
        "--context", type=int, default=2, help="调用位置上下文行数 0..8"
    )
    parser.add_argument(
        "--cache-dir", type=Path, help="静态图 JSON 缓存目录（不写入被审仓库）"
    )
    parser.add_argument(
        "--direction", choices=("callers", "callees", "both"), default="callers"
    )
    parser.add_argument("--depth", type=int, default=2, help="调用边数，1..5")
    parser.add_argument("--max-edges", type=int, default=40, help="返回边预算，1..80")
    parser.add_argument(
        "--snippet-lines", type=int, default=8, help="源码预览行数，0..20"
    )


def _validate_arguments(
    parsed: argparse.Namespace, parser: argparse.ArgumentParser
) -> argparse.Namespace:
    """Reject inconsistent graph/rule query arguments before doing I/O.

    Args:
        parsed: Namespace returned by the shared evidence parser.
        parser: ArgumentParser providing bounded validation errors.

    Returns:
        Validated query parameters without inferred defaults.
    """
    if (
        not parsed.rule
        and parsed.symbol is None
        and parsed.api is None
        and parsed.file is None
        and not parsed.graph_stats
    ):
        parser.error("至少提供 --rule、--api 或 --symbol")
    if (
        parsed.symbol is not None
        or parsed.api is not None
        or parsed.file is not None
        or parsed.graph_stats
    ) != (parsed.repo is not None):
        parser.error("--api/--symbol/--file 必须与 --repo 同时提供")
    if (parsed.file is None) != (parsed.line is None):
        parser.error("--file 与 --line 必须同时提供")
    if parsed.symbol is not None and parsed.file is not None:
        parser.error("--symbol 与 --file 定位二选一")
    if parsed.kind == "variable" and not (parsed.variable or parsed.name):
        parser.error("变量追踪必须提供 --name 或 --variable")
    if parsed.variable and not (parsed.file or parsed.symbol):
        parser.error("变量追踪需要 --file/--line 或 --symbol")
    if parsed.line is not None and parsed.line < 1:
        parser.error("--line 必须 >= 1")
    if not 0 <= parsed.context <= 8:
        parser.error("--context 必须在 0..8 范围内")
    if not 1 <= parsed.api_limit <= 10:
        parser.error("--api-limit 必须在 1..10 范围内")
    if not 1 <= parsed.depth <= 5:
        parser.error("--depth 必须在 1..5 范围内")
    if not 1 <= parsed.max_edges <= 80:
        parser.error("--max-edges 必须在 1..80 范围内")
    if not 0 <= parsed.snippet_lines <= 20:
        parser.error("--snippet-lines 必须在 0..20 范围内")
    return parsed


def _source_snapshot(repo: Path):
    """读取权威工作树源码；不运行受审代码和测试。"""
    from runtime.src.analysis_snapshot import (
        directory_analysis_snapshot,
        worktree_analysis_snapshot,
    )
    from runtime.src.config import GuardConfig

    if not repo.is_dir():
        raise ValueError(f"不存在仓库目录: {repo}")
    if (repo / ".git").exists():
        return worktree_analysis_snapshot(repo, GuardConfig())
    return directory_analysis_snapshot(repo, GuardConfig())


def _render_api(repo: Path, query: str, limit: int) -> str:
    """复用官方 API Catalog/BM25 和强摘要保鲜，不额外建立检索实现。"""
    _ensure_source_imports()
    from runtime.src.api_catalog import search_api_catalog
    from runtime.src.config import GuardConfig

    if not repo.is_dir():
        raise ValueError(f"不存在仓库目录: {repo}")
    root = repo.resolve()
    hits, metrics = search_api_catalog(
        root,
        root / "docs" / "api-reference",
        GuardConfig.load(root),
        query,
        limit=limit,
    )
    output = [
        "# API Catalog 文档查询",
        "",
        f"能力描述：{query}；来源：现有 RQG API Catalog（BM25 Core）",
        f"source_digest=`{metrics['source_digest']}`；refresh=`{metrics['refresh_mode']}`",
        "此入口直接复用官方 doc-search 的索引和强内容摘要保鲜；生成缓存仅写入可再生 docs/api-reference。",
        "BM25 候选只用于定位符号，不代表唯一 owner、真实调用链或业务正确性；未召回不等于不存在。",
        "",
    ]
    for rank, hit in enumerate(hits, 1):
        record = hit.record
        output.extend(
            [
                f"{rank}. `{record.symbol}` · `{record.path}:{record.line}` · "
                f"score={hit.score:.3f} · {record.visibility}",
                f"   - 签名：`{record.signature}`",
                f"   - 文档：{record.docstring.splitlines()[0] if record.docstring else '(无 docstring)'}",
            ]
        )
    if not hits:
        output.append("没有候选；继续检查源码与外部调用入口，不得推断能力不存在。")
    return "\n".join(output).rstrip() + "\n"


def _render_symbol_evidence(parsed, snapshot, graph, file, cache_status, digest):
    """Render one validated symbol/variable trace from immutable source facts.

    Args:
        parsed: Parsed CLI query parameters.
        snapshot: Source snapshot matching the graph cache.
        graph: Source-bound normalized repository graph.
        file: Validated repository-relative query file, if supplied.
        cache_status: Cache hit/miss provenance.
        digest: Snapshot and provider content fingerprint.

    Returns:
        Complete Markdown source evidence, or an explicit query error.
    """
    import ast
    from runtime.src.evidence_graph import (
        select_symbol,
        render,
        TraceOptions,
        find_node,
    )

    anchor = select_symbol(
        graph, parsed.symbol, file, parsed.line, parsed.name, parsed.kind
    )
    variable = parsed.variable or (
        parsed.name
        if parsed.kind == "variable"
        or (parsed.kind == "auto" and parsed.name and parsed.name != anchor["name"])
        else None
    )
    if variable is not None:
        node = find_node(snapshot, anchor)
        member = variable.split(".")[-1]
        exists = node is not None and any(
            (isinstance(item, ast.Name) and item.id == member)
            or (isinstance(item, ast.Attribute) and item.attr == member)
            or (isinstance(item, ast.arg) and item.arg == member)
            for item in ast.walk(node)
        )
        if not exists:
            raise ValueError(f"变量不在选定函数/类 AST 中: {variable}")
    return render(
        snapshot,
        graph,
        anchor,
        TraceOptions(
            direction=parsed.direction,
            depth=parsed.depth,
            budget=parsed.max_edges,
            radius=parsed.context,
            variable=variable,
            line=parsed.line,
            cache_info=f"cache={cache_status} snapshot={digest[:16]}",
        ),
    )


def execute_query(parsed: argparse.Namespace) -> int:
    """Execute an evidence query using the official workflow parser.

    Args:
        parsed: Parsed, source-bound evidence query options.

    Returns:
        Process return code: zero on success, nonzero on query failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    _validate_arguments(parsed, parser)
    return _execute_query(parsed)


def _execute_query(parsed: argparse.Namespace) -> int:
    """Render rule, API, call graph and trace evidence for validated arguments.

    Args:
        parsed: Explicit, validated query parameters.

    Returns:
        Zero on rendered evidence; two for execution errors.
    """
    try:
        parts = []
        if parsed.rule:
            _ensure_source_imports()
            from runtime.src.rule_lookup import (
                RULES_PATH,
                requested_codes,
                render_rule_cards,
            )

            requested = requested_codes(parsed.rule)
            parts.append(
                render_rule_cards(requested, RULES_PATH.read_text(encoding="utf-8"))
            )
        if parsed.api is not None:
            parts.append(_render_api(parsed.repo, parsed.api, parsed.api_limit))
        if (
            parsed.file is not None
            or parsed.symbol is not None
            or parsed.trace
            or parsed.variable is not None
            or parsed.graph_stats
        ):
            _ensure_source_imports()
            from runtime.src.evidence_graph import (
                load_graph,
            )

            repo = parsed.repo.resolve()
            file = None
            if parsed.file is not None:
                candidate = (repo / parsed.file).resolve()
                if not candidate.is_relative_to(repo):
                    raise ValueError("--file 必须位于指定仓库内")
                file = candidate.relative_to(repo).as_posix()
            snapshot = _source_snapshot(repo)
            if file is not None and file not in snapshot.units:
                raise ValueError(
                    f"文件不在权威源码快照中或 Python Provider 未覆盖: {file}"
                )
            from runtime.src.evidence_statistics import graph_statistics

            graph, cache_status, digest = load_graph(snapshot, ROOT, parsed.cache_dir)
            if parsed.graph_stats:
                parts.append(graph_statistics(graph))
            if parsed.symbol is not None or parsed.file is not None:
                parts.append(
                    _render_symbol_evidence(
                        parsed, snapshot, graph, file, cache_status, digest
                    )
                )
    except (OSError, ValueError, RuntimeError) as error:
        sys.stderr.write(f"[QG evidence] {error}\n")
        return 2
    sys.stdout.write("\n".join(parts))
    return 0
