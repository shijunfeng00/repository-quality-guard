"""仓库源码/AST 分析快照：一次读取与解析，供多个审计阶段共享。"""

from __future__ import annotations

import ast
import io
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import GuardConfig
from .git_utils import run_readonly_git_bytes
from .source_inputs import source_language


@dataclass(slots=True, frozen=True)
class PythonUnit:
    """
    保存单个 Python 文件的源码、AST 与语法错误事实。

    该对象只承载同一次仓库分析生命周期内可复用的解析结果，不负责规则裁决。
    """

    path: str
    source: str
    tree: ast.Module | None
    syntax_error: tuple[int, int, str] | None = None


@dataclass(slots=True, frozen=True)
class LanguageUnit:
    """保存非 Python 源文件的统一源码事实。

    该对象只保存路径、语言与 UTF-8 源码。语言 adapter 必须消费这里的源码，
    不得再次自行读取 Git revision 或工作树，从而保证一个仓库快照只有一个事实 owner。
    """

    path: str
    language: str
    source: str


@dataclass(slots=True, frozen=True)
class RepositoryAnalysisSnapshot:
    """
    保存一个仓库快照中 Python AST 与受支持非 Python 源码的统一事实。

    scanner、接口、架构、语义与语言 adapter 共享该对象，避免重复读取 Git/工作树。
    """

    root: Path
    label: str
    units: dict[str, PythonUnit]
    language_units: dict[str, LanguageUnit] = field(default_factory=dict)

    @property
    def paths(self) -> tuple[str, ...]:
        """
        返回稳定排序的仓库相对 Python 路径。

        Returns:
            按字典序排列的仓库相对路径元组。
        """
        return tuple(sorted(self.units))

    @property
    def language_paths(self) -> tuple[str, ...]:
        """返回稳定排序的非 Python 源码路径。"""
        return tuple(sorted(self.language_units))

    def unit(self, relative: str) -> PythonUnit | None:
        """
        按仓库相对路径返回单文件解析单元。

        Args:
            relative: 仓库相对 Python 文件路径。

        Returns:
            路径存在时返回解析单元；不属于当前分析范围时返回 None。
        """
        return self.units[relative] if relative in self.units else None

    def sources(self) -> dict[str, str]:
        """
        返回路径到源码文本的稳定映射副本。

        Returns:
            按稳定路径集合构造的源码映射。
        """
        return {path: self.units[path].source for path in self.paths}

    def trees(self) -> dict[str, ast.Module]:
        """
        返回仅包含语法有效模块的路径到 AST 映射。

        Returns:
            语法解析成功文件的 AST 映射。
        """
        return {
            path: unit.tree
            for path in self.paths
            if (unit := self.units[path]).tree is not None
        }


def _parse_unit(path: str, source: str) -> PythonUnit:
    """解析单文件源码并保留与现有扫描器一致的语法错误位置。"""
    try:
        tree = ast.parse(source, filename=path, type_comments=True)
    except SyntaxError as error:
        return PythonUnit(
            path=path,
            source=source,
            tree=None,
            syntax_error=(error.lineno or 1, error.offset or 1, error.msg),
        )
    return PythonUnit(path=path, source=source, tree=tree)


def worktree_analysis_snapshot(
    root: Path, config: GuardConfig
) -> RepositoryAnalysisSnapshot:
    """
    一次读取当前工作树受支持源码，并解析 Python AST。

    Args:
        root: Git 工作树根目录。
        config: 当前扫描配置。

    Returns:
        WORKTREE 对应的统一源码与 AST 快照。
    """
    listed = run_readonly_git_bytes(
        root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
    )
    if listed.returncode != 0:
        detail = listed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "无法列出 Git 工作树源码文件")
    paths = sorted(
        {
            raw.decode("utf-8", errors="surrogateescape")
            for raw in listed.stdout.split(b"\0")
            if raw
        }
    )
    units: dict[str, PythonUnit] = {}
    language_units: dict[str, LanguageUnit] = {}
    for relative in paths:
        language = source_language(relative, config)
        if not language:
            continue
        candidate = root / relative
        if not candidate.is_file():
            continue
        try:
            source = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise RuntimeError(f"源码不是 UTF-8: {relative}") from error
        if language == "python":
            units[relative] = _parse_unit(relative, source)
        else:
            language_units[relative] = LanguageUnit(relative, language, source)
    return RepositoryAnalysisSnapshot(root.resolve(), "WORKTREE", units, language_units)


def directory_analysis_snapshot(
    root: Path, config: GuardConfig
) -> RepositoryAnalysisSnapshot:
    """
    一次读取普通目录中的受支持源码，并解析 Python AST。

    Args:
        root: 待读取的普通目录根路径。
        config: 当前扫描配置。

    Returns:
        DIRECTORY 对应的统一源码与 AST 快照。
    """
    units: dict[str, PythonUnit] = {}
    language_units: dict[str, LanguageUnit] = {}
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        language = source_language(relative, config)
        if not language:
            continue
        try:
            source = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise RuntimeError(f"源码不是 UTF-8: {relative}") from error
        if language == "python":
            units[relative] = _parse_unit(relative, source)
        else:
            language_units[relative] = LanguageUnit(relative, language, source)
    return RepositoryAnalysisSnapshot(
        root.resolve(), "DIRECTORY", units, language_units
    )


def revision_analysis_snapshot(
    root: Path,
    revision: str,
    config: GuardConfig,
) -> RepositoryAnalysisSnapshot:
    """
    通过单次 ``git archive`` 读取不可变 Git revision 的受支持源码，并解析 Python AST。

    Args:
        root: Git 工作树根目录。
        revision: 已解析或可解析的 Git revision。
        config: 当前扫描配置。

    Returns:
        指定 revision 对应的统一源码与 AST 快照。
    """
    archive = run_readonly_git_bytes(root, "archive", "--format=tar", revision)
    if archive.returncode != 0:
        detail = archive.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"无法生成 Git 快照: {revision}")
    units: dict[str, PythonUnit] = {}
    language_units: dict[str, LanguageUnit] = {}
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as stream:
        for member in stream.getmembers():
            language = source_language(member.name, config)
            if not member.isfile() or not language:
                continue
            extracted = stream.extractfile(member)
            if extracted is None:
                continue
            try:
                source = extracted.read().decode("utf-8")
            except UnicodeDecodeError as error:
                raise RuntimeError(f"源码不是 UTF-8: {member.name}") from error
            if language == "python":
                units[member.name] = _parse_unit(member.name, source)
            else:
                language_units[member.name] = LanguageUnit(
                    member.name, language, source
                )
    return RepositoryAnalysisSnapshot(root.resolve(), revision, units, language_units)


def index_analysis_snapshot(
    root: Path, config: GuardConfig
) -> RepositoryAnalysisSnapshot:
    """
    读取 Git index 作为 staged 目标快照；仅 staged 模式需要。

    Args:
        root: Git 工作树根目录。
        config: 当前扫描配置。

    Returns:
        INDEX 对应的统一源码与 AST 快照。
    """
    listed = run_readonly_git_bytes(root, "ls-files", "--cached", "-z")
    if listed.returncode != 0:
        detail = listed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "无法列出 Git index")
    units: dict[str, PythonUnit] = {}
    language_units: dict[str, LanguageUnit] = {}
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="surrogateescape")
        language = source_language(relative, config)
        if not language:
            continue
        blob = run_readonly_git_bytes(root, "show", f":{relative}")
        if blob.returncode != 0:
            continue
        try:
            source = blob.stdout.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RuntimeError(f"源码不是 UTF-8: {relative}") from error
        if language == "python":
            units[relative] = _parse_unit(relative, source)
        else:
            language_units[relative] = LanguageUnit(relative, language, source)
    return RepositoryAnalysisSnapshot(root.resolve(), "INDEX", units, language_units)
