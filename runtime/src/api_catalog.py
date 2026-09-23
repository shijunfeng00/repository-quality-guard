"""从源码 docstring 与 type hint 生成可增量更新的 API 目录并提供 BM25 召回。"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .config import GuardConfig, is_test_path

_API_SCHEMA = "repository-quality-guard/api-catalog-v2"
_FIELD_WEIGHTS = {
    "symbol": 5.0,
    "signature": 3.0,
    "docstring": 2.0,
    "path": 1.2,
    "types": 2.5,
}
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_CJK_WHOLE_TOKEN_MAX = 4
_CJK_TRIGRAM_MIN = 3
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]+")


@dataclass(slots=True, frozen=True)
class _ApiRecord:
    """保存一个可复用 Python 接口的静态检索事实。"""

    symbol: str
    kind: str
    path: str
    line: int
    signature: str
    docstring: str
    types: str
    visibility: str


@dataclass(slots=True, frozen=True)
class _ApiSearchHit:
    """保存一次 API BM25 检索命中及其得分。"""

    record: _ApiRecord
    score: float


@dataclass(slots=True, frozen=True)
class _FileState:
    """保存单个生产源码文件的增量目录状态。"""

    digest: str
    size: int
    mtime_ns: int


def _render(node: ast.AST | None) -> str:
    """把 AST 节点稳定渲染为源码声明文本。"""
    if node is None:
        return ""
    return ast.unparse(node)


def _argument_text(arguments: ast.arguments) -> str:
    """渲染函数参数和类型注解，不执行目标模块。"""
    parts: list[str] = []
    positional = [*arguments.posonlyargs, *arguments.args]
    default_offset = len(positional) - len(arguments.defaults)
    for index, argument in enumerate(positional):
        item = argument.arg
        if argument.annotation is not None:
            item += f": {_render(argument.annotation)}"
        if index >= default_offset:
            item += f" = {_render(arguments.defaults[index - default_offset])}"
        parts.append(item)
    if arguments.vararg is not None:
        item = f"*{arguments.vararg.arg}"
        if arguments.vararg.annotation is not None:
            item += f": {_render(arguments.vararg.annotation)}"
        parts.append(item)
    elif arguments.kwonlyargs:
        parts.append("*")
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
        item = argument.arg
        if argument.annotation is not None:
            item += f": {_render(argument.annotation)}"
        if default is not None:
            item += f" = {_render(default)}"
        parts.append(item)
    if arguments.kwarg is not None:
        item = f"**{arguments.kwarg.arg}"
        if arguments.kwarg.annotation is not None:
            item += f": {_render(arguments.kwarg.annotation)}"
        parts.append(item)
    return ", ".join(parts)


def _type_text(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """汇总类基类、参数和返回类型，供接口能力检索加权。"""
    values: list[str] = []
    if isinstance(node, ast.ClassDef):
        values.extend(_render(base) for base in node.bases)
    else:
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)
        values.extend(_render(argument.annotation) for argument in arguments)
        values.append(_render(node.returns))
    return " ".join(value for value in values if value)


def _visibility(name: str) -> str:
    """按 Python 命名约定返回 public/private 可见性标签。"""
    private = name.startswith("_") and not (name.startswith("__") and name.endswith("__"))
    return "private" if private else "public"


def _module_name(relative: str) -> str:
    """把仓库相对 Python 路径转换为模块名。"""
    module = ".".join(Path(relative).with_suffix("").parts)
    return module.removesuffix(".__init__")


def _record(
    *,
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    module: str,
    qualname: str,
    kind: str,
    path: str,
) -> _ApiRecord:
    """从单个定义节点构造接口检索记录。"""
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(_render(base) for base in node.bases)
        signature = f"class {qualname}" + (f"({bases})" if bases else "")
    else:
        signature = f"{qualname}({_argument_text(node.args)})"
        return_type = _render(node.returns)
        if return_type:
            signature += f" -> {return_type}"
    return _ApiRecord(
        symbol=f"{module}.{qualname}" if module else qualname,
        kind=kind,
        path=path,
        line=node.lineno,
        signature=signature,
        docstring=ast.get_docstring(node, clean=True) or "",
        types=_type_text(node),
        visibility=_visibility(qualname.rsplit(".", 1)[-1]),
    )


def _discover_python_files(root: Path, config: GuardConfig) -> tuple[Path, ...]:
    """发现用于能力复用检索的生产 Python 文件。"""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )
    candidates = (
        [root / line for line in result.stdout.splitlines() if line.strip()]
        if result.returncode == 0
        else list(root.rglob("*.py"))
    )
    accepted: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if is_test_path(relative, config.project_name):
            continue
        if any(fnmatch(relative, pattern) for pattern in config.exclude):
            continue
        accepted.append(path)
    return tuple(sorted(set(accepted)))


def _file_state(path: Path, *, digest: str | None = None) -> _FileState:
    """计算源码文件内容摘要及廉价变更探针。"""
    stat = path.stat()
    content_digest = digest or hashlib.sha256(path.read_bytes()).hexdigest()
    return _FileState(digest=content_digest, size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _catalog_digest(states: dict[str, _FileState]) -> str:
    """从稳定的逐文件内容摘要计算整个接口目录摘要。"""
    digest = hashlib.sha256()
    for relative, state in sorted(states.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(state.digest.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _extract_path_records(root: Path, path: Path) -> list[_ApiRecord]:
    """只解析一个生产 Python 文件中的 class/function/method。"""
    relative = path.relative_to(root).as_posix()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative, type_comments=True)
    module = _module_name(relative)
    records: list[_ApiRecord] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            records.append(
                _record(node=node, module=module, qualname=node.name, kind="class", path=relative)
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    records.append(
                        _record(
                            node=child,
                            module=module,
                            qualname=f"{node.name}.{child.name}",
                            kind="method",
                            path=relative,
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            records.append(
                _record(
                    node=node, module=module, qualname=node.name, kind="function", path=relative
                )
            )
    return records


def _extract_api_records(root: Path, paths: tuple[Path, ...]) -> list[_ApiRecord]:
    """从指定生产源码集合提取 API 记录。"""
    records: list[_ApiRecord] = []
    for path in paths:
        try:
            records.extend(_extract_path_records(root, path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
    records.sort(key=lambda item: (item.path, item.line, item.symbol))
    return records


def _identifier_tokens(token: str) -> list[str]:
    """拆分 snake_case/CamelCase 标识符并补充连写词。"""
    parts: list[str] = []
    for piece in token.replace("-", "_").split("_"):
        if piece:
            parts.extend(part for part in _CAMEL_BOUNDARY.split(piece) if part)
    lowered = [part.lower() for part in parts]
    if len(lowered) > 1:
        lowered.append("".join(lowered))
    return lowered


def _tokenize(text: str) -> list[str]:
    """生成适合代码能力 BM25 的英文标识符与中文 n-gram 词项。"""
    tokens: list[str] = []
    for match in _WORD.finditer(text):
        token = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) <= _CJK_WHOLE_TOKEN_MAX:
                tokens.append(token)
            tokens.extend(token[index : index + 2] for index in range(max(0, len(token) - 1)))
            if len(token) >= _CJK_TRIGRAM_MIN:
                tokens.extend(token[index : index + 3] for index in range(len(token) - 2))
        else:
            tokens.extend(_identifier_tokens(token))
    return tokens


class _ApiBm25Index:
    """对 API 记录执行字段加权 BM25 检索，不依赖向量模型或 GPU。"""

    def __init__(self, records: list[_ApiRecord]) -> None:
        """构建内存倒排索引。

        Args:
            records: 从当前生产源码提取的接口检索记录。

        Returns:
            None。
        """
        self.records = records
        self.postings: dict[str, dict[str, dict[int, int]]] = {
            field: defaultdict(dict) for field in _FIELD_WEIGHTS
        }
        self.lengths: dict[str, list[int]] = {field: [] for field in _FIELD_WEIGHTS}
        self.averages: dict[str, float] = {}
        for record_id, record in enumerate(records):
            values = {
                "symbol": record.symbol,
                "signature": record.signature,
                "docstring": record.docstring,
                "path": record.path,
                "types": record.types,
            }
            for field, text in values.items():
                frequencies = Counter(_tokenize(text))
                self.lengths[field].append(sum(frequencies.values()))
                for term, count in frequencies.items():
                    self.postings[field][term][record_id] = count
        for field, lengths in self.lengths.items():
            self.averages[field] = sum(lengths) / len(lengths) if lengths else 1.0

    def search(self, query: str, *, limit: int = 10) -> list[_ApiSearchHit]:
        """按自然语言、符号名和类型词混合查询已有能力。

        Args:
            query: 描述待复用能力的自然语言、符号名或类型词查询。
            limit: 最多返回的候选接口数量。

        Returns:
            按 BM25 相关度降序排列的候选接口。
        """
        if not query.strip():
            raise ValueError("API 检索 query 不能为空")
        if limit <= 0:
            raise ValueError("检索结果数量必须大于 0")
        query_terms = Counter(_tokenize(query))
        scores: dict[int, float] = defaultdict(float)
        total = len(self.records)
        for field, weight in _FIELD_WEIGHTS.items():
            average = self.averages[field] or 1.0
            postings = self.postings[field]
            lengths = self.lengths[field]
            for term, query_count in query_terms.items():
                documents = postings.get(term)
                if not documents:
                    continue
                document_frequency = len(documents)
                inverse_frequency = math.log(
                    1 + (total - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                for record_id, frequency in documents.items():
                    denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * lengths[record_id] / average)
                    scores[record_id] += (
                        weight
                        * inverse_frequency
                        * (frequency * 2.2 / denominator)
                        * (1 + 0.15 * math.log1p(query_count))
                    )
        for record_id in tuple(scores):
            if self.records[record_id].visibility == "public":
                scores[record_id] *= 1.08
        ranked = sorted(scores.items(), key=lambda item: (-item[1], self.records[item[0]].symbol))[
            :limit
        ]
        return [
            _ApiSearchHit(record=self.records[record_id], score=score)
            for record_id, score in ranked
        ]


def _group_filename(group: str) -> str:
    """把顶层包名转换为稳定的 Markdown 文件名。"""
    return f"{group.replace('.', '_')}.md"


def _write_group(output: Path, group: str, records: list[_ApiRecord]) -> None:
    """重写一个顶层包对应的 Markdown shard。"""
    lines = [
        f"# API Catalog: {group}",
        "",
        "由源码自动生成。检索结果只负责候选召回，是否复用必须继续阅读真实源码。",
        "",
    ]
    current_path = ""
    for record in sorted(records, key=lambda item: (item.path, item.line, item.symbol)):
        if record.path != current_path:
            current_path = record.path
            lines.extend([f"## `{current_path}`", ""])
        escaped_signature = record.signature.replace("`", "\\`")
        lines.extend(
            [
                f"### `{record.symbol}`",
                "",
                f"- Kind: `{record.kind}`",
                f"- Visibility: `{record.visibility}`",
                f"- Source: `{record.path}:{record.line}`",
                f"- Signature: `{escaped_signature}`",
            ]
        )
        if record.docstring:
            lines.extend(["", record.docstring])
        lines.append("")
    (output / _group_filename(group)).write_text("\n".join(lines), encoding="utf-8")


def _write_api_catalog(
    output: Path,
    records: list[_ApiRecord],
    states: dict[str, _FileState],
    *,
    changed_groups: set[str] | None = None,
) -> None:
    """写入 catalog.json，并只重写受影响的 Markdown package shard。"""
    output.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[_ApiRecord]] = defaultdict(list)
    for record in records:
        groups[record.path.split("/", 1)[0]].append(record)
    existing_group_files = {path.name for path in output.glob("*.md") if path.name != "INDEX.md"}
    target_groups = set(groups) if changed_groups is None else set(changed_groups)
    for group in sorted(target_groups):
        path = output / _group_filename(group)
        if group in groups:
            _write_group(output, group, groups[group])
        elif path.exists():
            path.unlink()
    expected_group_files = {_group_filename(group) for group in groups}
    for filename in existing_group_files - expected_group_files:
        (output / filename).unlink()

    digest = _catalog_digest(states)
    index_lines = [
        "# Generated API Catalog",
        "",
        "由 Repository Quality Guard 从 Python docstring 与 type hint 自动生成。请勿手工编辑。",
        "",
        f"- Schema: `{_API_SCHEMA}`",
        f"- Source digest: `{digest}`",
        f"- Files: {len(states)}",
        f"- Symbols: {len(records)}",
        "",
        "## Packages",
        "",
    ]
    for group in sorted(groups):
        index_lines.append(f"- [{group}]({_group_filename(group)}) — {len(groups[group])} symbols")
    (output / "INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    payload = {
        "schema": _API_SCHEMA,
        "source_digest": digest,
        "files": {relative: asdict(state) for relative, state in sorted(states.items())},
        "records": [asdict(record) for record in records],
    }
    (output / "catalog.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _load_api_catalog(path: Path) -> tuple[list[_ApiRecord], dict[str, _FileState]]:
    """读取机器可读 API catalog，并验证 schema。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["schema"] != _API_SCHEMA:
        raise ValueError(f"API catalog schema 不匹配: {payload['schema']}")
    records = [_ApiRecord(**item) for item in payload["records"]]
    states = {relative: _FileState(**state) for relative, state in payload["files"].items()}
    return records, states


def _changed_paths_from_state(
    root: Path,
    paths: tuple[Path, ...],
    cached: dict[str, _FileState],
    strong_check: bool = False,
) -> tuple[set[str], dict[str, _FileState], int]:
    """Detect changed source files; search can require content-strong freshness.

    ``strong_check`` hashes every current source file so a search can never reuse a
    stale catalog merely because a tool preserved size/mtime while editing code.
    Normal doc generation keeps the cheaper stat-first incremental path.
    """
    current_by_relative = {path.relative_to(root).as_posix(): path for path in paths}
    changed = set(cached) - set(current_by_relative)
    states = dict(cached)
    hashed = 0
    for relative, path in current_by_relative.items():
        stat = path.stat()
        if relative not in cached:
            states[relative] = _file_state(path)
            hashed += 1
            changed.add(relative)
            continue
        previous = cached[relative]
        if not strong_check and previous.size == stat.st_size and previous.mtime_ns == stat.st_mtime_ns:
            continue
        state = _file_state(path)
        hashed += 1
        if previous.digest != state.digest:
            changed.add(relative)
        states[relative] = state
    for relative in set(states) - set(current_by_relative):
        del states[relative]
    return changed, states, hashed


def _resolve_scoped_paths(root: Path, changed_paths: tuple[str, ...]) -> set[str]:
    """把显式范围更新参数规范为仓库相对 Python 路径。"""
    resolved: set[str] = set()
    for value in changed_paths:
        path = Path(value)
        absolute = path if path.is_absolute() else root / path
        try:
            relative = absolute.resolve().relative_to(root.resolve()).as_posix()
        except ValueError as error:
            raise ValueError(f"增量接口文档路径不属于目标仓库: {value}") from error
        if relative.endswith(".py"):
            resolved.add(relative)
    return resolved


def _refresh_catalog(
    root: Path,
    output: Path,
    config: GuardConfig,
    changed_paths: tuple[str, ...] | None = None,
    force_full: bool = False,
    strong_check: bool = False,
) -> tuple[list[_ApiRecord], dict[str, _FileState], dict[str, Any]]:
    """按全量、显式范围或自动变化检测刷新接口目录。"""
    started = time.perf_counter()
    paths = _discover_python_files(root, config)
    discovered = time.perf_counter()
    catalog_path = output / "catalog.json"
    cached_records: list[_ApiRecord] = []
    cached_states: dict[str, _FileState] = {}
    cache_available = catalog_path.is_file() and not force_full
    if cache_available:
        cached_records, cached_states = _load_api_catalog(catalog_path)
    loaded = time.perf_counter()

    current_paths = {path.relative_to(root).as_posix(): path for path in paths}
    if force_full or not cache_available:
        selected = set(current_paths)
        states = {relative: _file_state(path) for relative, path in current_paths.items()}
        hashed = len(states)
        records = _extract_api_records(root, paths)
        changed_groups = {relative.split("/", 1)[0] for relative in selected}
        mode = "full"
    else:
        if changed_paths is None:
            selected, states, hashed = _changed_paths_from_state(
                root, paths, cached_states, strong_check=strong_check
            )
            mode = "auto-incremental" if selected else "noop"
        else:
            selected = _resolve_scoped_paths(root, changed_paths)
            selected.update(set(cached_states) - set(current_paths))
            states = dict(cached_states)
            hashed = 0
            for relative in selected:
                if relative not in current_paths:
                    if relative not in states:
                        raise ValueError(
                            f"增量接口文档路径既不存在于当前源码，也不存在于旧 catalog: {relative}"
                        )
                    del states[relative]
                    continue
                state = _file_state(current_paths[relative])
                hashed += 1
                states[relative] = state
            mode = "scoped-incremental" if selected else "noop"
        records = [record for record in cached_records if record.path not in selected]
        selected_existing = tuple(
            current_paths[relative] for relative in sorted(selected) if relative in current_paths
        )
        records.extend(_extract_api_records(root, selected_existing))
        records.sort(key=lambda item: (item.path, item.line, item.symbol))
        changed_groups = {relative.split("/", 1)[0] for relative in selected}
    extracted = time.perf_counter()
    if force_full or not cache_available or selected:
        _write_api_catalog(
            output,
            records,
            states,
            changed_groups=None if force_full or not cache_available else changed_groups,
        )
    written = time.perf_counter()
    return (
        records,
        states,
        {
            "mode": mode,
            "changed_files": len(selected),
            "hashed_files": hashed,
            "discover_seconds": discovered - started,
            "load_seconds": loaded - discovered,
            "extract_seconds": extracted - loaded,
            "write_seconds": written - extracted,
            "total_seconds": written - started,
            "source_digest": _catalog_digest(states),
        },
    )


def build_api_catalog(
    root: Path,
    output: Path,
    config: GuardConfig,
    *,
    changed_paths: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """生成或增量刷新接口文档与 BM25 机器目录。

    Args:
        root: 待建立接口目录的仓库根目录。
        output: 自动生成 Markdown 与 catalog.json 的目标目录。
        config: 已应用项目 profile 的质量守卫配置。
        changed_paths: 可选仓库相对 Python 文件；提供时只刷新这些文件及受影响 shard。

    Returns:
        接口数量、刷新模式、变更文件数量、源码摘要和阶段耗时。
    """
    records, _states, metrics = _refresh_catalog(
        root,
        output,
        config,
        changed_paths=changed_paths,
    )
    index_started = time.perf_counter()
    _ApiBm25Index(records)
    indexed = time.perf_counter()
    index_seconds = indexed - index_started
    return {
        "symbols": len(records),
        **metrics,
        "index_seconds": index_seconds,
        "total_with_index_seconds": metrics["total_seconds"] + index_seconds,
        "output": str(output),
    }


def search_api_catalog(
    root: Path,
    output: Path,
    config: GuardConfig,
    query: str,
    *,
    limit: int = 10,
    changed_paths: tuple[str, ...] | None = None,
) -> tuple[list[_ApiSearchHit], dict[str, float | bool | int | str]]:
    """使用自动保持新鲜的接口目录执行 BM25 候选召回。

    Args:
        root: 待检索能力的仓库根目录。
        output: 自动接口目录所在目录。
        config: 已应用项目 profile 的质量守卫配置。
        query: 描述待复用能力的自然语言、符号名或类型词查询。
        limit: 最多返回的候选接口数量。
        changed_paths: 可选仓库相对 Python 文件；提供时只刷新这些文件后再检索。

    Returns:
        候选接口及目录刷新、建索引和检索阶段耗时。
    """
    started = time.perf_counter()
    records, _states, refresh = _refresh_catalog(
        root,
        output,
        config,
        changed_paths=changed_paths,
        strong_check=True,
    )
    prepared = time.perf_counter()
    index = _ApiBm25Index(records)
    indexed = time.perf_counter()
    hits = index.search(query, limit=limit)
    finished = time.perf_counter()
    return hits, {
        "cache_hit": refresh["mode"] == "noop",
        "refresh_mode": str(refresh["mode"]),
        "changed_files": int(refresh["changed_files"]),
        "hashed_files": int(refresh["hashed_files"]),
        "source_digest": str(refresh["source_digest"]),
        "catalog_prepare_seconds": prepared - started,
        "index_seconds": indexed - prepared,
        "search_seconds": finished - indexed,
        "total_seconds": finished - started,
    }
