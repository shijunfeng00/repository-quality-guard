from __future__ import annotations

import argparse
import copy
from fnmatch import fnmatch
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from .analysis_snapshot import (
    RepositoryAnalysisSnapshot,
    directory_analysis_snapshot,
    index_analysis_snapshot,
    revision_analysis_snapshot,
    worktree_analysis_snapshot,
)
from .api_catalog import build_api_catalog, search_api_catalog
from .architecture_diff import compare_git_architecture
from .call_chain_rules import single_use_chain_findings
from .config import GuardConfig, is_test_path, is_tool_generated_path
from .fallback_laundering import fallback_laundering_findings
from .git_utils import run_readonly_git
from .integrity import INTEGRITY_RULE_CODE, verify_release_integrity
from .interface_diff import GitWorktreeInterfaceComparator, PythonInterfaceExtractor
from .interface_visibility import filter_interface_symbols, resolve_import_aliases
from .multilang import multilang_findings
from .model import (
    Finding,
    InterfaceChange,
    InterfaceDiffReport,
    QualityBaseline,
    ScanReport,
)
from .project_contracts import validate_project_contracts
from .release_identity_rules import release_identity_findings
from .project_profiles import (
    ProfileSelection,
    ProjectProfile,
    RuleContext,
    apply_project_profile,
    get_project_profile,
    resolve_profile_reference,
)
from .report_contract import (
    code_status,
    render_strict_modification_report,
    report_final_status,
    validate_modification_report,
)
from .report_schema import REPORT_FILENAME
from .reporting import (
    SEVERITY_RANK,
    merge_findings,
    render_audit_markdown,
)
from .relation_graph import apply_relation_finding_context, build_relation_graph_summary
from .ruff_runner import RuffRunner
from .scanner import RepositoryScanner
from .semantic_diff import semantic_repair_findings
from .semantic_heuristic_candidates import semantic_heuristic_candidate_findings
from .semantic_review import annotate_semantic_review, semantic_review_key

MODIFICATION_REPORT_NAME = REPORT_FILENAME
GIT_COMMAND_NOT_FOUND_EXIT_CODE = 127
REPORT_GATE_EXIT_CODE = 3
INTEGRITY_GATE_EXIT_CODE = 4
REVIEW_REQUIRED_EXIT_CODE = 5
_AUDIT_ONLY_SUFFIXES = (".patch", ".diff", ".log", ".zip", ".tar", ".tgz", ".tar.gz")
_PORCELAIN_STATUS_PREFIX_LENGTH = 3
_AUDIT_ONLY_PREFIXES = (".agents/", ".pytest_cache/", ".ruff_cache/", "docs/api-reference/")
_INTERFACE_DEFINITION_KINDS = frozenset({"class", "function", "method"})
_INTERFACE_POLICY_CODES = frozenset({"QG161", "QG180", "QG181", "QG182"})


def _filter_test_quality_findings(
    findings: list[Finding],
    passthrough_patterns: tuple[str, ...],
) -> list[Finding]:
    """Apply the one canonical test-quality baseline projection."""
    return [
        item
        for item in findings
        if any(
            fnmatch(Path(item.path).as_posix().lstrip("./"), pattern)
            for pattern in passthrough_patterns
        )
        or item.code in {"QG000", "QG149"}
        or item.source in {"ruff", "integration"}
    ]


@dataclass(slots=True, frozen=True)
class AuditTarget:
    """
    保存一次审计的仓库根、聚焦范围和 Git 能力。

    该结构让 CLI 在 Git 根目录做全局调用关系分析，同时可以把报告输出
    聚焦到用户指定的子目录或文件列表。
    """

    root: Path
    focus_files: frozenset[Path]
    focus_roots: tuple[Path, ...]
    notes: tuple[str, ...]
    git_enabled: bool
    git_issue_message: str = ""
    git_issue_suggestion: str = ""


@dataclass(slots=True, frozen=True)
class GitRootLookup:
    """
    表示一次 Git 根目录探测结果。

    该结构区分真正非 Git 目录和 Git 仓库访问失败，避免 safe.directory
    或权限问题被误报成“不是 Git 仓库”。
    """

    root: Path | None
    issue_message: str = ""
    issue_suggestion: str = ""


@dataclass(slots=True, frozen=True)
class _GitComparisonPlan:
    """保存自动选择的 Git 比较基线与目标说明。"""

    base_revision: str
    target_label: str
    mode: str


def build_parser() -> argparse.ArgumentParser:
    """
    构建极简且不可降级的 quality-guard 命令行参数解析器。

    Returns:
        配置完成的 ArgumentParser。
    """
    parser = argparse.ArgumentParser(
        prog="quality-guard",
        description=(
            "强制执行 Ruff、结构质量规则和自动 Git 增量审计，并生成修改说明报告。"
            "默认不允许关闭任何检查。"
        ),
    )
    parser.add_argument("path", nargs="?", help="审计目录；省略时使用当前目录。")
    parser.add_argument(
        "--project",
        metavar="PATH",
        help="审计项目目录或 Git 仓库子目录；输出聚焦该目录，调用关系仍按 Git 根解析。",
    )
    parser.add_argument(
        "--files",
        metavar="FILES",
        help="逗号分隔的文件路径；输出聚焦这些文件。",
    )
    parser.add_argument(
        "--profile",
        metavar="PROFILE",
        help="Portable 模式显式选择 Profile 名称或目录；不指定时仅做目录名精确匹配。",
    )
    parser.add_argument(
        "--diff-base",
        metavar="COMMIT",
        default=None,
        help=(
            "显式指定旧接口快照；省略时优先使用最后一次可确认 push 对应的提交，"
            "累计审计本批全部已提交和未提交变化；无法可靠确定时才降级为 dirty/staged "
            "使用 HEAD、clean 使用 HEAD~1。"
        ),
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="接口 diff 使用 Git 暂存区作为目标；默认使用当前工作区。",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--final-check",
        action="store_true",
        help="执行最终只检门禁：不写文件，重新计算事实并验证现有修改说明。",
    )
    mode.add_argument(
        "--verify-integrity",
        action="store_true",
        help="只验证 Skill 发布内容完整性，不扫描目标仓库。",
    )
    mode.add_argument(
        "--build-api-docs",
        action="store_true",
        help="从全库 Python docstring/type hint 生成 docs/api-reference 接口目录与 BM25 catalog。",
    )
    mode.add_argument(
        "--search-api",
        metavar="QUERY",
        help="生成或刷新接口目录后用 BM25 检索已有能力；只负责候选召回，不替代源码审查。",
    )
    parser.add_argument(
        "--api-top-k",
        dest="api_limit",
        type=int,
        default=10,
        help="--search-api 返回候选数，默认 10。",
    )
    parser.set_defaults(
        resolved_profile_reference="",
        resolved_profile_name="",
        resolved_profile_source="",
    )
    return parser


def _cache_root() -> Path:
    """
    返回 Ruff/uv 运行时缓存根目录。

    Returns:
        可写缓存目录路径。
    """
    if "REPO_QUALITY_GUARD_CACHE_DIR" in os.environ:
        return Path(os.environ["REPO_QUALITY_GUARD_CACHE_DIR"])
    return Path(tempfile.gettempdir()) / "repository-quality-guard"


def git_failure_lookup(path: Path, stderr: str, returncode: int) -> GitRootLookup:
    """
    把 Git 探测失败转换为可行动的审计提示。

    Args:
        path: 用户传入的文件或目录路径。
        stderr: Git stderr 文本。
        returncode: Git 命令退出码。

    Returns:
        带失败原因和修复建议的 GitRootLookup。
    """
    message = stderr.strip()
    lowered = message.lower()
    if "dubious ownership" in lowered or "safe.directory" in lowered:
        return GitRootLookup(
            None,
            "目标路径位于 Git 仓库中，但 Git safe.directory 安全策略拒绝访问，接口 diff 已跳过。",
            f"确认路径无误后执行: git config --global --add safe.directory {path}",
        )
    if "not a git repository" in lowered:
        return GitRootLookup(
            None,
            "目标路径不是 Git 仓库或不在 Git 工作树内，接口 diff 已跳过。",
            "确认这是预期的非 Git 输入；如果不是，切换到正确仓库根或子目录重跑。",
        )
    if "permission denied" in lowered:
        return GitRootLookup(
            None,
            "目标路径疑似属于 Git 仓库，但当前进程没有足够权限访问 Git 元数据，接口 diff 已跳过。",
            "检查目录权限、容器挂载用户和 .git 目录访问权限后重跑。",
        )
    if "command not found" in lowered or returncode == GIT_COMMAND_NOT_FOUND_EXIT_CODE:
        return GitRootLookup(
            None,
            "当前环境无法执行 git 命令，接口 diff 已跳过。",
            "安装 Git 或修正 PATH 后重跑，避免缺失接口变动审查。",
        )
    detail = message or f"git rev-parse 退出码 {returncode}"
    return GitRootLookup(
        None,
        f"Git 仓库探测失败，接口 diff 已跳过: {detail}",
        "确认路径、仓库完整性、权限和 Git 配置后重跑。",
    )


def _git_root(path: Path) -> GitRootLookup:
    """
    查找路径所属 Git 仓库根目录并保留失败原因。

    Args:
        path: 文件或目录路径。

    Returns:
        找到时 root 为 Git 根目录；失败时包含原因与修复建议。
    """
    cwd = path if path.is_dir() else path.parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
    except FileNotFoundError:
        return GitRootLookup(
            None,
            "当前环境没有 git 可执行文件，接口 diff 已跳过。",
            "安装 Git 或修正 PATH 后重跑，避免缺失接口变动审查。",
        )
    if result.returncode != 0:
        return git_failure_lookup(path, result.stderr, result.returncode)
    root_text = result.stdout.strip()
    if not root_text:
        return GitRootLookup(
            None,
            "Git 返回了空仓库根目录，接口 diff 已跳过。",
            "确认仓库状态和 Git 输出后重跑。",
        )
    return GitRootLookup(Path(root_text).resolve())


def _git_stdout(root: Path, arguments: tuple[str, ...]) -> str:
    """执行一组只读 Git 参数并返回标准输出。

    Args:
        root: Git 仓库根目录。
        arguments: 不包含 ``git`` 本身的固定参数序列。

    Returns:
        命令成功时去除首尾空白的标准输出；失败时为空串。
    """
    result = run_readonly_git(root, *arguments)
    return result.stdout.strip() if result.returncode == 0 else ""


def _last_pushed_revision(root: Path) -> tuple[str, str] | None:
    """定位最后一次可证明的远端批次基线。

    优先采用远端跟踪 reflog 中明确标记为 push 的祖先提交；本机 reflog
    不可用时只读查询远端分支头，最后才采用 upstream 或其共同祖先。

    Args:
        root: Git 仓库根目录。

    Returns:
        ``(commit, source)``；无法可靠定位时返回 None。
    """
    push_ref = _git_stdout(
        root,
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{push}"),
    )
    upstream = _git_stdout(
        root,
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
    )
    refs = list(dict.fromkeys(ref for ref in (push_ref, upstream) if ref))
    if not refs:
        refs = [
            ref
            for ref in _git_stdout(
                root,
                ("for-each-ref", "--format=%(refname:short)", "refs/remotes"),
            ).splitlines()
            if ref and not ref.endswith("/HEAD")
        ]
    for ref in refs:
        reflog = _git_stdout(root, ("reflog", "show", "--format=%H%x09%gs", ref))
        for line in reflog.splitlines():
            commit, separator, subject = line.partition("\t")
            lowered = subject.lower()
            pushed = "update by push" in lowered or lowered.startswith("push:")
            if not separator or not pushed:
                continue
            verified = _git_stdout(
                root,
                ("rev-parse", "--verify", f"{commit}^{{commit}}"),
            )
            if (
                verified
                and run_readonly_git(
                    root,
                    "merge-base",
                    "--is-ancestor",
                    verified,
                    "HEAD",
                ).returncode
                == 0
            ):
                return verified, f"{ref} reflog"
    for ref in refs:
        normalized = ref.removeprefix("refs/remotes/")
        remote, separator, branch = normalized.partition("/")
        if not separator or not remote or not branch:
            continue
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--heads", remote, f"refs/heads/{branch}"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="surrogateescape",
                check=False,
                timeout=8,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        commit = result.stdout.partition("\t")[0].strip() if result.returncode == 0 else ""
        verified = (
            _git_stdout(root, ("rev-parse", "--verify", f"{commit}^{{commit}}")) if commit else ""
        )
        if (
            verified
            and run_readonly_git(
                root,
                "merge-base",
                "--is-ancestor",
                verified,
                "HEAD",
            ).returncode
            == 0
        ):
            return verified, f"remote {remote}/{branch}"
    upstream_commit = (
        _git_stdout(root, ("rev-parse", "--verify", f"{upstream}^{{commit}}")) if upstream else ""
    )
    if not upstream_commit:
        return None
    if (
        run_readonly_git(
            root,
            "merge-base",
            "--is-ancestor",
            upstream_commit,
            "HEAD",
        ).returncode
        == 0
    ):
        return upstream_commit, f"upstream {upstream}"
    common = _git_stdout(root, ("merge-base", "HEAD", upstream_commit))
    return (common, f"diverged upstream common ancestor with {upstream}") if common else None


def _comparison_plan(target: AuditTarget, args: argparse.Namespace) -> _GitComparisonPlan:
    """优先按最后一次 push 形成累计批次；无法证明时才使用单次提交回退。"""
    requested_target = "STAGED" if args.staged else "WORKTREE"
    if args.diff_base:
        return _GitComparisonPlan(
            args.diff_base, requested_target, f"显式基线：{args.diff_base}→{requested_target}"
        )
    if not target.git_enabled:
        return _GitComparisonPlan("HEAD", requested_target, "非 Git 输入：自动基线不可用")
    status = run_readonly_git(target.root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        detail = status.stderr.strip() or f"exit={status.returncode}"
        raise RuntimeError(f"无法判断 Git dirty/clean 状态：{detail}")
    relevant_changes: list[str] = []
    for raw_line in status.stdout.splitlines():
        path_text = (
            raw_line[_PORCELAIN_STATUS_PREFIX_LENGTH:].strip()
            if len(raw_line) > _PORCELAIN_STATUS_PREFIX_LENGTH
            else ""
        )
        if " -> " in path_text:
            path_text = path_text.rsplit(" -> ", 1)[-1]
        normalized = Path(path_text).as_posix()
        lowered = normalized.lower()
        audit_only = (
            normalized == MODIFICATION_REPORT_NAME
            or is_tool_generated_path(normalized)
            or normalized.startswith(_AUDIT_ONLY_PREFIXES)
            or "/__pycache__/" in f"/{normalized}/"
            or any(lowered.endswith(suffix) for suffix in _AUDIT_ONLY_SUFFIXES)
        )
        if normalized and not audit_only:
            relevant_changes.append(normalized)
    effective_target = requested_target if args.staged or relevant_changes else "HEAD"
    pushed = _last_pushed_revision(target.root)
    if pushed is not None:
        revision, source = pushed
        return _GitComparisonPlan(
            revision,
            effective_target,
            "自动累计批次基线：最后一次 Git push/远端批次 "
            f"`{revision[:12]}`（{source}）→{effective_target}",
        )
    if relevant_changes:
        return _GitComparisonPlan(
            "HEAD",
            requested_target,
            f"自动 dirty 回退基线：HEAD→{requested_target}（{len(relevant_changes)} 个有效变更路径）",
        )
    parent = _git_stdout(
        target.root,
        ("rev-parse", "--verify", "HEAD~1^{commit}"),
    )
    if not parent:
        raise RuntimeError(
            "无法定位最后一次 push/upstream，且 clean 工作树没有可用父提交；请显式传入 --diff-base。"
        )
    return _GitComparisonPlan(parent, "HEAD", f"自动 clean 回退基线：HEAD~1 `{parent[:12]}`→HEAD")


def resolve_target(args: argparse.Namespace) -> AuditTarget:
    """
    根据命令行参数解析审计根目录与输出聚焦范围。

    Args:
        args: 已解析命令行参数。

    Returns:
        完整审计目标描述。
    """
    if args.files and (args.path or args.project):
        raise ValueError("--files 不能和位置目录或 --project 同时使用")
    if args.files:
        files: list[Path] = []
        seen: set[Path] = set()
        for item in args.files.split(","):
            if not item.strip():
                continue
            path = Path(item.strip()).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f"指定文件不存在: {path}")
            if path not in seen:
                files.append(path)
                seen.add(path)
        if not files:
            raise ValueError("--files 没有解析到任何文件")
        lookups = [_git_root(path) for path in files]
        git_roots = {lookup.root for lookup in lookups if lookup.root is not None}
        if len(git_roots) == 1 and all(lookup.root is not None for lookup in lookups):
            root = next(iter(git_roots))
            return AuditTarget(
                root=root,
                focus_files=frozenset(files),
                focus_roots=(),
                notes=("按 Git 根目录全仓库解析调用关系；报告输出聚焦 --files 指定文件。",),
                git_enabled=True,
            )
        root = Path(os.path.commonpath(str(path.parent) for path in files)).resolve()
        issue_messages = tuple(lookup.issue_message for lookup in lookups if lookup.issue_message)
        issue_suggestions = tuple(
            lookup.issue_suggestion for lookup in lookups if lookup.issue_suggestion
        )
        git_issue_message = (
            issue_messages[0]
            if issue_messages
            else ("指定文件不属于同一个 Git 仓库；接口 diff 已跳过。")
        )
        git_issue_suggestion = (
            issue_suggestions[0]
            if issue_suggestions
            else ("确认这是预期的非 Git/跨仓库输入，而不是路径传错。")
        )
        return AuditTarget(
            root=root,
            focus_files=frozenset(files),
            focus_roots=(),
            notes=(git_issue_message, git_issue_suggestion),
            git_enabled=False,
            git_issue_message=git_issue_message,
            git_issue_suggestion=git_issue_suggestion,
        )
    raw_target = args.project or args.path or "."
    target = Path(raw_target).expanduser().resolve()
    if target.is_file():
        git_lookup = _git_root(target)
        if git_lookup.root is not None:
            return AuditTarget(
                root=git_lookup.root,
                focus_files=frozenset({target}),
                focus_roots=(),
                notes=(f"按 Git 根目录 `{git_lookup.root}` 解析仓库事实；报告输出聚焦文件 `{target}`。",),
                git_enabled=True,
            )
        return AuditTarget(
            root=target.parent,
            focus_files=frozenset({target}),
            focus_roots=(),
            notes=(git_lookup.issue_message, git_lookup.issue_suggestion),
            git_enabled=False,
            git_issue_message=git_lookup.issue_message,
            git_issue_suggestion=git_lookup.issue_suggestion,
        )
    if not target.is_dir():
        raise ValueError(f"审计路径不存在: {target}")
    git_lookup = _git_root(target)
    if git_lookup.root is None:
        return AuditTarget(
            root=target,
            focus_files=frozenset(),
            focus_roots=(),
            notes=(git_lookup.issue_message, git_lookup.issue_suggestion),
            git_enabled=False,
            git_issue_message=git_lookup.issue_message,
            git_issue_suggestion=git_lookup.issue_suggestion,
        )
    git_root = git_lookup.root
    if target == git_root:
        return AuditTarget(git_root, frozenset(), (), (), True)
    return AuditTarget(
        root=git_root,
        focus_files=frozenset(),
        focus_roots=(target,),
        notes=(f"按 Git 根目录 `{git_root}` 全仓库解析调用关系；报告输出聚焦子目录 `{target}`。",),
        git_enabled=True,
    )


def _filter_interface_diff(
    interface_diff: InterfaceDiffReport | None,
    target: AuditTarget,
) -> InterfaceDiffReport | None:
    """
    把接口差异明细限制到聚焦文件或目录。

    Args:
        interface_diff: 原始接口差异报告。
        target: 当前审计目标。

    Returns:
        过滤后的接口差异报告；为空时仍返回空报告。
    """
    if interface_diff is None:
        return None
    if not target.focus_files and not target.focus_roots:
        return interface_diff
    return InterfaceDiffReport(
        base=interface_diff.base,
        target=interface_diff.target,
        changes=tuple(
            change
            for change in interface_diff.changes
            if (
                (target.root / change.path).resolve() in target.focus_files
                if target.focus_files
                else any(
                    (target.root / change.path).resolve() == root
                    or (target.root / change.path).resolve().is_relative_to(root)
                    for root in target.focus_roots
                )
            )
        ),
        base_files=interface_diff.base_files,
        target_files=interface_diff.target_files,
        errors=interface_diff.errors,
        contract_findings=tuple(
            finding
            for finding in interface_diff.contract_findings
            if (
                (target.root / finding.path).resolve() in target.focus_files
                if target.focus_files
                else any(
                    (target.root / finding.path).resolve() == root
                    or (target.root / finding.path).resolve().is_relative_to(root)
                    for root in target.focus_roots
                )
            )
        ),
        api_scope=interface_diff.api_scope,
        private_min_lines=interface_diff.private_min_lines,
    )


def _apply_focus(report: ScanReport, target: AuditTarget) -> ScanReport:
    """
    应用输出聚焦范围和审计范围说明。

    Args:
        report: 待处理的扫描报告。
        target: 当前审计目标。

    Returns:
        原地更新后的报告。
    """
    if target.focus_files or target.focus_roots:
        report.findings = [
            item
            for item in report.findings
            if (
                (target.root / item.path).resolve() in target.focus_files
                if target.focus_files
                else any(
                    (target.root / item.path).resolve() == root
                    or (target.root / item.path).resolve().is_relative_to(root)
                    for root in target.focus_roots
                )
            )
        ]
        report.interface_diff = _filter_interface_diff(report.interface_diff, target)
        report.focus_paths = tuple(
            sorted(str(path) for path in (*target.focus_files, *target.focus_roots))
        )
    report.scope_notes = target.notes
    if not target.git_enabled:
        report.findings.append(
            Finding(
                code="QG998",
                severity="warning",
                confidence="high",
                path=".",
                line=1,
                column=1,
                message=target.git_issue_message
                or "当前输入缺失 Git 仓库上下文，接口 diff 已跳过。",
                suggestion=target.git_issue_suggestion
                or "确认传入的是预期非 Git 目录/文件；如果不是，切换到正确仓库根或子目录重跑。",
                source="quality-guard",
            )
        )
    report.findings.sort(
        key=lambda item: (-SEVERITY_RANK[item.severity], item.path, item.line, item.code)
    )
    return report


def _apply_rule_level(item: Finding, config: GuardConfig) -> Finding | None:
    """Apply one declarative Profile rule level without changing core rule ownership.

    ``SEMANTIC`` remains an Info finding but is forced into the semantic-review
    ledger. ``BLOCKER`` remains a Critical finding for display while carrying an
    independent absolute-blocker marker; this preserves the historical meaning
    that Critical is baseline-aware while Blocker is not.
    """
    if item.code in frozenset(config.disabled_rules):
        return None
    levels = dict(config.profile_rule_levels)
    level = levels.get(item.code)
    if not level:
        return item
    evidence = dict(item.evidence)
    evidence["profile_rule_level"] = level
    if level in {"info", "warning", "error", "critical"}:
        return replace(item, severity=level, evidence=evidence)
    if level == "semantic":
        evidence["semantic_review_required"] = True
        evidence.setdefault("semantic_review_question", "Q1,Q2,Q6,Q11")
        evidence.setdefault("semantic_review_kind", "profile-semantic")
        return replace(item, severity="info", evidence=evidence)
    if level == "blocker":
        evidence["absolute_blocker"] = True
        return replace(item, severity="critical", evidence=evidence)
    raise ValueError(f"unsupported profile rule level: {level!r}")


def _apply_profile_rule_policy(report: ScanReport, config: GuardConfig) -> None:
    """Apply declarative disable/level policy before baseline and static gating."""
    report.findings = [
        mapped
        for item in report.findings
        if (mapped := _apply_rule_level(item, config)) is not None
    ]
    if report.interface_diff is not None:
        contract_findings = tuple(
            mapped
            for item in report.interface_diff.contract_findings
            if (mapped := _apply_rule_level(item, config)) is not None
        )
        report.interface_diff = replace(
            report.interface_diff,
            contract_findings=contract_findings,
        )
    report.findings.sort(
        key=lambda item: (-SEVERITY_RANK[item.severity], item.path, item.line, item.code)
    )


def _apply_report_extensions(report: ScanReport, profile: ProjectProfile | None) -> None:
    """Collect stable Profile metadata without allowing extensions to control gate status."""
    if profile is None or not profile.report_extensions:
        return
    merged: dict[str, str] = {}
    for extension_cls in profile.report_extensions:
        extension = extension_cls()
        for key, value in extension.metadata().items():
            normalized = str(key).strip()
            if not normalized:
                raise ValueError("ReportExtension metadata keys must be non-empty")
            if normalized in merged:
                raise ValueError(f"duplicate ReportExtension metadata key: {normalized}")
            merged[normalized] = str(value)
    report.profile_metadata.update(merged)


def _profile_custom_findings(profile: ProjectProfile | None, ctx: RuleContext) -> list[Finding]:
    """Execute Profile custom rules against the Core-owned read-only analysis context."""
    if profile is None or not profile.custom_rules:
        return []
    findings: list[Finding] = []
    disabled = frozenset(ctx.config.disabled_rules)
    for rule_cls in profile.custom_rules:
        rule = rule_cls()
        code = profile.rule_codes.get(rule.key)
        if not code:
            raise ValueError(f"custom rule {rule.key!r} has no frozen QG code")
        if code in disabled:
            if not rule.suppressible:
                raise ValueError(f"custom rule {rule.key!r} ({code}) is not suppressible")
            continue
        for item in rule.evaluate(ctx):
            if not isinstance(item, Finding):
                raise TypeError(f"custom rule {rule.key!r} must yield Finding objects")
            findings.append(
                replace(
                    item,
                    code=code,
                    severity=rule.severity,
                    source=f"profile:{profile.name}",
                )
            )
    return findings


def _quality_baseline(report: ScanReport, revision: str) -> QualityBaseline:
    """把一次基线扫描压缩为可比较的质量摘要。

    Args:
        report: 已完成聚焦处理的基线扫描报告。
        revision: Git 基线 revision。

    Returns:
        严重级别、规则和文件维度的基线摘要。
    """
    summary = {"critical": 0, "error": 0, "warning": 0, "info": 0}
    by_rule: dict[str, dict[str, int]] = {}
    by_file: dict[str, dict[str, int]] = {}
    for finding in report.findings:
        summary[finding.severity] += 1
        if finding.code not in by_rule:
            by_rule[finding.code] = {
                "critical": 0,
                "error": 0,
                "warning": 0,
                "info": 0,
            }
        by_rule[finding.code][finding.severity] += 1
        if finding.path not in by_file:
            by_file[finding.path] = {
                "critical": 0,
                "error": 0,
                "warning": 0,
                "info": 0,
            }
        by_file[finding.path][finding.severity] += 1
    return QualityBaseline(
        revision=revision,
        files_scanned=report.files_scanned,
        definitions=report.definitions,
        documented_definitions=report.documented_definitions,
        complete_docstrings=report.complete_docstrings,
        docstring_definitions=report.docstring_definitions,
        summary=summary,
        by_rule=by_rule,
        by_file=by_file,
        finding_severities={
            finding.fingerprint: finding.severity
            for finding in report.findings
            if finding.severity in {"critical", "error", "warning"}
            and not finding.code.startswith("QG98")
        },
        semantic_review_fingerprints=dict(
            Counter(
                semantic_review_key(finding)
                for finding in annotate_semantic_review(list(report.findings))
                if finding.evidence.get("semantic_review_required") is True
            )
        ),
    )


def _baseline_target(target: AuditTarget, baseline_root: Path) -> AuditTarget:
    """把当前聚焦范围映射到临时 Git 基线目录。

    Args:
        target: 当前工作区审计目标。
        baseline_root: 解压后的 Git 基线根目录。

    Returns:
        保持相同相对聚焦范围的基线审计目标。
    """
    focus_files = frozenset(
        baseline_root / path.relative_to(target.root) for path in target.focus_files
    )
    focus_roots = tuple(
        baseline_root / path.relative_to(target.root) for path in target.focus_roots
    )
    return AuditTarget(
        root=baseline_root,
        focus_files=focus_files,
        focus_roots=focus_roots,
        notes=(f"静态质量基线来自 Git `{target.root}`。",),
        git_enabled=True,
    )


_BASELINE_CACHE_SCHEMA = "repository-quality-guard/baseline-cache-v3"


def _baseline_cache_path(target: AuditTarget, config: GuardConfig, revision: str) -> Path | None:
    """返回不可变 Git revision 质量基线的安全缓存路径。"""
    resolved_revision = _git_stdout(
        target.root, ("rev-parse", "--verify", f"{revision}^{{commit}}")
    )
    if not resolved_revision:
        return None
    focus_files = sorted(path.relative_to(target.root).as_posix() for path in target.focus_files)
    focus_roots = sorted(path.relative_to(target.root).as_posix() for path in target.focus_roots)
    payload = json.dumps(
        {
            "schema": _BASELINE_CACHE_SCHEMA,
            "root": str(target.root.resolve()),
            "revision": resolved_revision,
            "release_seal": os.environ["REPO_QUALITY_GUARD_RELEASE_SEAL"],
            "config": repr(config),
            "focus_files": focus_files,
            "focus_roots": focus_roots,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    key = hashlib.sha256(payload).hexdigest()
    return _cache_root() / "baseline-v3" / f"{key}.json"


def _baseline_payload(item: QualityBaseline) -> dict[str, object]:
    """把质量基线转换为稳定缓存结构。"""
    return {
        "revision": item.revision,
        "files_scanned": item.files_scanned,
        "definitions": item.definitions,
        "documented_definitions": item.documented_definitions,
        "complete_docstrings": item.complete_docstrings,
        "docstring_definitions": item.docstring_definitions,
        "summary": item.summary,
        "by_rule": item.by_rule,
        "by_file": item.by_file,
        "finding_severities": item.finding_severities,
        "semantic_review_fingerprints": item.semantic_review_fingerprints,
    }


def _baseline_from_payload(payload: dict[str, object]) -> QualityBaseline:
    """从已验证 schema 的缓存结构恢复质量基线。"""
    return QualityBaseline(
        revision=str(payload["revision"]),
        files_scanned=int(payload["files_scanned"]),
        definitions=int(payload["definitions"]),
        documented_definitions=int(payload["documented_definitions"]),
        complete_docstrings=int(payload["complete_docstrings"]),
        docstring_definitions=int(payload["docstring_definitions"]),
        summary=dict(payload["summary"]),
        by_rule=dict(payload["by_rule"]),
        by_file=dict(payload["by_file"]),
        finding_severities=dict(payload["finding_severities"]),
        semantic_review_fingerprints={
            str(key): int(value)
            for key, value in dict(payload["semantic_review_fingerprints"]).items()
        },
    )


def _load_baseline_cache(path: Path | None) -> tuple[QualityBaseline, QualityBaseline] | None:
    """读取不可变 Git 基线缓存；缺失或版本过期时重新计算。"""
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["schema"] != _BASELINE_CACHE_SCHEMA:
        return None
    return (
        _baseline_from_payload(payload["production"]),
        _baseline_from_payload(payload["test"]),
    )


def _write_baseline_cache(
    path: Path | None, production: QualityBaseline, test: QualityBaseline
) -> None:
    """原子写入不可变 Git 基线缓存，不改变任何审计结果。"""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": _BASELINE_CACHE_SCHEMA,
        "production": _baseline_payload(production),
        "test": _baseline_payload(test),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _compute_git_baseline(
    target: AuditTarget,
    config: GuardConfig,
    revision: str,
    profile: ProjectProfile | None = None,
) -> tuple[QualityBaseline, QualityBaseline]:
    """重新计算指定 Git revision 的生产代码与测试代码质量基线。

    Args:
        target: 当前工作区审计目标。
        config: 已启用项目 profile 的固定规则配置。
        revision: 待扫描的 Git revision。

    Returns:
        生产代码基线与测试代码独立基线。

    Raises:
        RuntimeError: Git 快照生成失败。
    """
    archive = subprocess.run(
        ["git", "archive", "--format=tar", revision],
        cwd=target.root,
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        detail = archive.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"无法生成 Git 基线 `{revision}`: {detail}")
    with tempfile.TemporaryDirectory(prefix="quality-guard-baseline-") as temp_dir:
        baseline_root = Path(temp_dir).resolve()
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
            for member in tar.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise RuntimeError(f"Git 基线快照包含不安全路径: {member.name}")
            tar.extractall(baseline_root, filter="data")
        baseline_target = _baseline_target(target, baseline_root)
        production_config = config.with_overrides(include_tests=False)
        test_config = config.with_overrides(include_tests=True)
        baseline_analysis = directory_analysis_snapshot(baseline_root, config)
        production_scanner = RepositoryScanner(
            baseline_root,
            production_config,
            baseline_analysis,
        )
        production_report = production_scanner.scan()
        baseline_architecture_findings = single_use_chain_findings(
            baseline_root,
            production_config,
            analysis_snapshot=baseline_analysis,
        )
        merge_findings(
            production_report,
            (
                item
                for item in baseline_architecture_findings
                if not is_test_path(item.path, config.project_name)
            ),
        )
        if production_config.project_name:
            sources: dict[str, str] = {}
            for path in production_scanner.discover_python_files():
                relative = path.relative_to(baseline_root).as_posix()
                unit = baseline_analysis.unit(relative)
                if unit is not None:
                    sources[relative] = unit.source
            symbols = {}
            import_aliases = []
            for path, source in sorted(sources.items()):
                unit = baseline_analysis.unit(path)
                extractor = PythonInterfaceExtractor(
                    path,
                    source,
                    tree=unit.tree if unit is not None else None,
                )
                file_symbols, _errors = extractor.extract()
                symbols.update(file_symbols)
                import_aliases.extend(extractor.import_aliases)
            symbols = resolve_import_aliases(symbols, import_aliases, tuple(sources))
            symbols = filter_interface_symbols(
                symbols,
                sources,
                import_aliases,
                forced_symbols=set(production_config.forced_interface_symbols),
                include_private=True,
                private_min_lines=production_config.api_private_min_lines,
            )
            merge_findings(
                production_report,
                validate_project_contracts(
                    profile,
                    sources,
                    sources,
                    symbols,
                    base_trees=baseline_analysis.trees(),
                    target_trees=baseline_analysis.trees(),
                ),
            )
        test_scanner = RepositoryScanner(
            baseline_root,
            test_config,
            baseline_analysis,
        )
        test_files = {
            path
            for path in test_scanner.discover_python_files()
            if is_test_path(path.relative_to(baseline_root), config.project_name)
        }
        test_report = test_scanner.scan(selected_files=test_files)
        test_report.findings = _filter_test_quality_findings(
            test_report.findings,
            config.profile_test_baseline_passthrough_paths,
        )
        merge_findings(
            test_report,
            (
                item
                for item in baseline_architecture_findings
                if is_test_path(item.path, config.project_name)
            ),
        )
        ruff_findings = RuffRunner(
            baseline_root,
            cache_root=_cache_root(),
            environment=os.environ,
        ).run(check_format=True)
        merge_findings(
            production_report,
            (item for item in ruff_findings if not is_test_path(item.path, config.project_name)),
        )
        merge_findings(
            test_report,
            (item for item in ruff_findings if is_test_path(item.path, config.project_name)),
        )
        language_findings, language_summary = multilang_findings(
            baseline_analysis, production_config
        )
        merge_findings(production_report, language_findings)
        production_report.multilang_summary = language_summary
        identity_findings = release_identity_findings(baseline_root, production_config)
        # Release/version identity is always a semantic-review concern. Keep test
        # candidates in the main report so QG203/QG205 cannot become a hidden
        # test-only static rejection path.
        merge_findings(production_report, identity_findings)
        custom_findings = _profile_custom_findings(
            profile,
            RuleContext(
                root=baseline_root,
                config=production_config,
                analysis=baseline_analysis,
            ),
        )
        merge_findings(
            production_report,
            (item for item in custom_findings if not is_test_path(item.path, config.project_name)),
        )
        merge_findings(
            test_report,
            (item for item in custom_findings if is_test_path(item.path, config.project_name)),
        )
        _apply_profile_rule_policy(production_report, production_config)
        _apply_profile_rule_policy(test_report, test_config)
        _apply_report_extensions(production_report, profile)
        _apply_report_extensions(test_report, profile)
        _apply_focus(production_report, baseline_target)
        _apply_focus(test_report, baseline_target)
        return (
            _quality_baseline(production_report, revision),
            _quality_baseline(test_report, revision),
        )


def _scan_git_baseline(
    target: AuditTarget,
    config: GuardConfig,
    revision: str,
    profile: ProjectProfile | None = None,
) -> tuple[QualityBaseline, QualityBaseline]:
    """读取不可变 Git 基线缓存，未命中时完整计算并原子保存。"""
    cache_path = _baseline_cache_path(target, config, revision)
    cached = _load_baseline_cache(cache_path)
    if cached is not None:
        return cached
    production, test = _compute_git_baseline(target, config, revision, profile)
    _write_baseline_cache(cache_path, production, test)
    return production, test


def _annotate_interface_changes(
    interface_diff: InterfaceDiffReport,
) -> InterfaceDiffReport:
    """标注 property 新增/形态变化和 private→public 权限提升。"""
    removed_lookup = {
        (change.path, change.kind, change.symbol): change
        for change in interface_diff.changes
        if change.change == "removed"
    }
    annotated: list[InterfaceChange] = []
    property_decorators = {"property", "cached_property", "setter", "deleter"}
    for original in interface_diff.changes:
        details = dict(original.details)
        policy = dict(details.get("interface_policy", {}))
        symbol = original.after or original.before
        decorators = () if symbol is None else symbol.decorators
        leaves = {item.rsplit(".", 1)[-1] for item in decorators}
        if original.change == "added" and original.kind in _INTERFACE_DEFINITION_KINDS:
            if leaves & property_decorators:
                policy["property_added"] = True
            owner, separator, leaf = original.symbol.rpartition(".")
            private_symbol = f"{owner}._{leaf}" if separator else f"_{leaf}"
            promoted = removed_lookup.get((original.path, original.kind, private_symbol))
            if promoted is not None and not leaf.startswith("_"):
                policy["visibility_promotion_from"] = promoted.symbol
        decorator_change = details.get("decorators")
        if (
            original.change == "modified"
            and original.kind in {"function", "method"}
            and isinstance(decorator_change, dict)
        ):
            before = {item.rsplit(".", 1)[-1] for item in decorator_change.get("before", [])}
            after = {item.rsplit(".", 1)[-1] for item in decorator_change.get("after", [])}
            if (before | after) & property_decorators:
                policy["property_decorator_changed"] = True
        if policy:
            details["interface_policy"] = policy
            annotated.append(replace(original, details=details))
        else:
            annotated.append(original)
    return replace(interface_diff, changes=tuple(annotated))


def _interface_policy_finding(change: InterfaceChange) -> Finding:
    """把一条新增或存量接口声明变化转换为 QG180。"""
    symbol = change.after or change.before
    is_addition = change.change == "added"
    policy = change.details.get("interface_policy", {})
    promoted_from = policy.get("visibility_promotion_from", "")
    property_added = bool(policy.get("property_added"))
    property_changed = bool(policy.get("property_decorator_changed"))
    if promoted_from:
        message = (
            f"生产接口 `{promoted_from}` 被提升为 public `{change.symbol}`，"
            "属于函数权限与接口契约变更。"
        )
    elif property_added:
        message = f"新增生产 property 接口 `{change.symbol}`，必须完成接口必要性审判。"
    elif is_addition:
        message = f"新增生产 `{change.kind}` 接口 `{change.symbol}`，必须完成减法审判。"
    elif property_changed:
        message = f"存量接口 `{change.symbol}` 的 property/装饰器形态发生变化。"
    elif "parameters" in change.details:
        message = f"存量 `{change.kind}` 接口 `{change.symbol}` 的参数列表发生变化。"
    else:
        changed_fields = "、".join(sorted(change.details)) or "声明"
        message = f"存量 `{change.kind}` 接口 `{change.symbol}` 的声明发生变化：{changed_fields}。"
    return Finding(
        code="QG180",
        severity="warning",
        confidence="high",
        path=change.path,
        line=symbol.line if symbol is not None else 1,
        column=1,
        symbol=change.symbol,
        message=message,
        suggestion=(
            "先尝试删除、内联、合并或复用；保留时必须逐项证明绝对必要性、"
            "不可替代性、唯一职责、调用链和新鲜验证。private→public 与 @property "
            "不能用‘方便调用’解释。"
            if is_addition
            else "逐项证明为何绝对不能保持原接口，核对全部调用方、兼容性、"
            "README/Prompt/配置与测试，并给出不可替代方案和新鲜验证。"
        ),
        evidence={
            "interface_change": change.change,
            "interface_kind": change.kind,
            "details": change.details,
            "visibility_promotion_from": promoted_from,
            "property_added": property_added,
            "property_decorator_changed": property_changed,
            "qg179_exempt": True,
            "governed_by": "QG181" if is_addition else "report-interface-review",
        },
    )


def _run_interface_diff(
    target: AuditTarget,
    config: GuardConfig,
    args: argparse.Namespace,
    base_analysis: RepositoryAnalysisSnapshot | None = None,
    target_analysis: RepositoryAnalysisSnapshot | None = None,
    profile: ProjectProfile | None = None,
) -> InterfaceDiffReport:
    """执行不可关闭的 Git 接口差异审查。

    Args:
        target: 当前审计目标。
        config: 已合并项目档案的质量配置。
        args: 已解析命令行参数。
        base_analysis: 可选的 Git before 统一源码/AST 快照。
        target_analysis: 可选的 after 统一源码/AST 快照。
        profile: 已由 CLI 解析完成的项目 Profile；内部不得按名称二次解析。

    Returns:
        已附加 QG180/QG181 与项目协议发现的接口差异报告。
    """
    if not target.git_enabled:
        return InterfaceDiffReport(
            base=args.diff_base,
            target="unavailable:no-git",
            changes=(),
            base_files=0,
            target_files=0,
            api_scope="all",
            private_min_lines=config.api_private_min_lines,
        )
    interface_diff = GitWorktreeInterfaceComparator(
        target.root,
        config,
        base_revision=args.diff_base,
        include_private=True,
        target="staged" if args.staged else "worktree",
        base_analysis=base_analysis,
        target_analysis=target_analysis,
        profile=profile,
    ).compare()
    interface_diff = _annotate_interface_changes(interface_diff)
    production_changes = [
        change
        for change in interface_diff.changes
        if not is_test_path(change.path, config.project_name)
    ]
    added = [
        change
        for change in production_changes
        if change.change == "added" and change.kind in _INTERFACE_DEFINITION_KINDS
    ]
    removed = [
        change
        for change in production_changes
        if change.change == "removed" and change.kind in _INTERFACE_DEFINITION_KINDS
    ]
    interface_review_changes = [
        change
        for change in production_changes
        if change.change == "modified" and change.kind in _INTERFACE_DEFINITION_KINDS
    ]
    policy_findings = [
        _interface_policy_finding(change) for change in (*added, *interface_review_changes)
    ]
    interface_net = len(added) - len(removed)
    if interface_net > 0:
        first = added[0]
        first_symbol = first.after or first.before
        policy_findings.append(
            Finding(
                code="QG181",
                severity="warning",
                confidence="high",
                path=first.path,
                line=first_symbol.line if first_symbol is not None else 1,
                column=1,
                symbol="repository.interface_definition_balance",
                message=(
                    "生产函数/方法/类净增加，触发接口膨胀人工复核："
                    f"新增 {len(added)}，删除 {len(removed)}，净额 +{interface_net}。"
                ),
                suggestion=(
                    "优先继续删除、内联、合并或复用；确需保留时必须逐项完成必要性审判，"
                    "并由用户人工确认后再决定是否提交。删除项无需解释删除理由。"
                ),
                evidence={
                    "added": [f"{item.kind}:{item.path}:{item.symbol}" for item in added],
                    "removed": [f"{item.kind}:{item.path}:{item.symbol}" for item in removed],
                    "added_count": len(added),
                    "removed_count": len(removed),
                    "net": interface_net,
                    "qg179_exempt": True,
                },
            )
        )
    return replace(
        interface_diff,
        contract_findings=(*interface_diff.contract_findings, *policy_findings),
    )


def _readme_sync_finding(
    target: AuditTarget,
    args: argparse.Namespace,
    interface_diff: InterfaceDiffReport,
    project_name: str,
) -> Finding | None:
    """检查公开接口变化是否同步修改 README。

    Args:
        target: 当前审计目标。
        args: 已解析命令行参数。
        interface_diff: 已生成的接口差异报告。
        project_name: 当前启用的项目 profile。

    Returns:
        公开接口变化但 README 未同步时返回 QG161，否则返回 None。
    """
    public_changes: list[InterfaceChange] = []
    for change in interface_diff.changes:
        if (
            change.change == "removed"
            or change.kind == "file"
            or is_test_path(change.path, project_name)
        ):
            continue
        symbol = change.after or change.before
        if symbol is None:
            continue
        leaf = symbol.qualname.rsplit(".", 1)[-1]
        if not leaf.startswith("_") or (leaf.startswith("__") and leaf.endswith("__")):
            public_changes.append(change)
    if not public_changes:
        return None
    changed_paths: set[str] = set()
    if target.git_enabled:
        command = [
            "diff",
            *(["--cached"] if args.staged else []),
            "--name-only",
            args.diff_base,
            "--",
        ]
        changed = run_readonly_git(target.root, *command)
        if changed.returncode == 0:
            changed_paths.update(
                line.strip() for line in changed.stdout.splitlines() if line.strip()
            )
        if not args.staged:
            untracked = run_readonly_git(target.root, "ls-files", "--others", "--exclude-standard")
            if untracked.returncode == 0:
                changed_paths.update(
                    line.strip() for line in untracked.stdout.splitlines() if line.strip()
                )
    readme_changed = any(Path(item).name.lower().startswith("readme") for item in changed_paths)
    if readme_changed:
        return None
    first = public_changes[0]
    symbol = first.after or first.before
    return Finding(
        code="QG161",
        severity="error",
        confidence="high",
        path=first.path,
        line=symbol.line if symbol is not None else 1,
        column=1,
        message=(f"检测到 {len(public_changes)} 项公开接口变化，但当前变更未同步任何 README。"),
        suggestion=(
            "更新仓库 README 中的公开能力、参数、返回结构、配置或使用示例，并同步测试与调用方；"
            "如果接口不应公开，先修正接口边界而不是仅补文档。"
        ),
        evidence={
            "public_changes": [
                f"{change.change}:{change.kind}:{change.path}:{change.symbol}"
                for change in public_changes
            ],
            "changed_paths": sorted(changed_paths),
            "qg179_exempt": True,
            "governed_by": "interface-review",
        },
    )


def _profile_selection(root: Path, explicit: str | None):
    """解析 Profile 并把自动/显式/通用来源明确打印给用户。"""
    selection = resolve_profile_reference(root, explicit)
    if selection.source == "explicit-cli":
        print(f"[QG] Profile selected: '{selection.name}' (source=explicit-cli).")
    elif selection.source == "auto-directory-name":
        print(
            f"[QG] Auto-selected profile '{selection.name}' because repository directory "
            "name matches an available profile. (source=auto-directory-name)"
        )
    elif selection.source == "sealed-installed":
        print(
            f"[QG] Using sealed installed profile '{selection.name}'. "
            "(source=sealed-installed)"
        )
    elif selection.source == "sealed-installed-generic":
        print("[QG] Using sealed installed generic policy. (source=sealed-installed-generic)")
    else:
        print("[QG] No profile selected; using generic policy. (source=generic)")
    return selection


def _scan_profile_selection(
    root: Path, args: argparse.Namespace
) -> ProfileSelection:
    """Use an inherited worker selection or resolve the normal user-facing Profile.

    Args:
        root: Resolved audit repository root.
        args: Parsed scan arguments with internal resolved-Profile defaults.

    Returns:
        The exact Profile selection to apply to this scan.
    """
    if args.resolved_profile_source:
        return ProfileSelection(
            reference=args.resolved_profile_reference or None,
            name=args.resolved_profile_name,
            source=args.resolved_profile_source,
            reason="profile selection inherited from the isolated scan parent",
        )
    return _profile_selection(root, args.profile)


def api_catalog_context(
    target: AuditTarget, profile_name: str | None
) -> tuple[GuardConfig, ProjectProfile | None]:
    """Resolve one visible Profile selection for catalog config and optional search hooks."""
    selection = _profile_selection(target.root, profile_name)
    profile = get_project_profile(selection.reference)
    config = apply_project_profile(
        GuardConfig.load(target.root),
        selection.reference,
        selection_source=selection.source,
    ).with_overrides(include_tests=False)
    return config, profile


def api_catalog_config(target: AuditTarget, profile_name: str | None) -> GuardConfig:
    """兼容入口：只返回接口目录使用的生产代码配置。"""
    config, _profile = api_catalog_context(target, profile_name)
    return config


def _apply_search_strategy(
    profile: ProjectProfile | None, query: str, hits: list, *, root: Path, limit: int
) -> list:
    """Run an optional Profile reranker after the Core-owned default BM25 search."""
    if profile is None or profile.search_strategy is None:
        return hits
    strategy = profile.search_strategy()
    reranked = list(strategy.rerank(query, tuple(hits), root=root))
    # Profiles may reorder/drop default candidates but may not synthesize unrelated object types.
    if any(item not in hits for item in reranked):
        raise ValueError("Profile SearchStrategy may only rerank/filter Core search hits")
    return reranked[:limit]


def _run_api_catalog_mode(args: argparse.Namespace) -> int | None:
    """执行开发前接口目录生成或 BM25 候选检索模式。"""
    if not args.build_api_docs and args.search_api is None:
        return None
    target = resolve_target(args)
    config, profile = api_catalog_context(target, args.profile)
    output = target.root / "docs" / "api-reference"
    if args.build_api_docs:
        metrics = build_api_catalog(target.root, output, config)
        print(
            "API 目录生成完成："
            f"symbols={metrics['symbols']} "
            f"extract={metrics['extract_seconds']:.3f}s "
            f"index={metrics['index_seconds']:.3f}s "
            f"write={metrics['write_seconds']:.3f}s "
            f"total={metrics['total_seconds']:.3f}s"
        )
        print(f"API 目录：{output / 'INDEX.md'}")
        return 0
    if args.api_limit <= 0:
        raise ValueError("--api-top-k 必须大于 0")
    hits, metrics = search_api_catalog(
        target.root,
        output,
        config,
        args.search_api,
        limit=args.api_limit,
    )
    hits = _apply_search_strategy(
        profile, args.search_api, hits, root=target.root, limit=args.api_limit
    )
    print(f"API BM25 查询：{args.search_api}")
    print(
        "性能："
        f"cache_hit={metrics['cache_hit']} "
        f"source_check={metrics['source_check_seconds']:.3f}s "
        f"index={metrics['index_seconds']:.3f}s "
        f"search={metrics['search_seconds'] * 1000:.3f}ms "
        f"total={metrics['total_seconds']:.3f}s"
    )
    if not hits:
        print("未召回候选；必须继续直接检索源码，不能据此认定能力不存在。")
        return 0
    for rank, hit in enumerate(hits, start=1):
        summary = hit.record.docstring.splitlines()[0] if hit.record.docstring else ""
        print(
            f"{rank:>2}. {hit.record.symbol} "
            f"score={hit.score:.3f} {hit.record.path}:{hit.record.line} "
            f"[{hit.record.visibility}] {summary}"
        )
    print("提示：候选排名不是语义裁决；新增接口前必须阅读 Top10 及相关调用方源码。")
    return 0


def integrity_gate() -> int | None:
    """
    执行不可降级的 Skill 发布内容完整性门禁。

    Returns:
        完整性通过时返回 None；失败时返回固定完整性门禁退出码。
    """
    result = verify_release_integrity()
    if result.passed:
        print(f"Skill 完整性门禁：PASS（{result.protected_files} 个受保护文件）")
        return None
    print(f"Skill 完整性门禁：REJECT（{INTEGRITY_RULE_CODE}）", file=sys.stderr)
    for issue in result.issues:
        print(f"- {issue}", file=sys.stderr)
    return INTEGRITY_GATE_EXIT_CODE


def _append_docstring_regression_finding(report: ScanReport) -> None:
    """在当前树新增未记录 docstring 的定义时追加聚合 Critical。

    覆盖门禁比较缺失 docstring 的稳定问题指纹，而不是覆盖率百分比。删除
    已记录 helper、缩短仍然准确的 docstring 或改变统计分母都不会构成回归；
    docstring 结构完整性仍由 QG028/QG030 等逐定义规则独立审计。

    Args:
        report: 已完成当前代码与 Git 基线扫描的生产报告。

    Returns:
        None。
    """
    baseline = report.baseline
    if baseline is None:
        return
    baseline_missing = {
        fingerprint for fingerprint in baseline.finding_severities if ":QG027:" in fingerprint
    }
    current_missing = {
        finding.fingerprint: finding for finding in report.findings if finding.code == "QG027"
    }
    introduced = sorted(set(current_missing) - baseline_missing)
    if not introduced:
        return
    introduced_findings = [current_missing[fingerprint] for fingerprint in introduced]
    symbols = [
        finding.symbol or f"{finding.path}:{finding.line}" for finding in introduced_findings
    ]
    report.findings.append(
        Finding(
            code="QG169",
            severity="critical",
            confidence="high",
            path=".",
            line=1,
            column=1,
            symbol="repository.missing_docstrings",
            message=(
                f"相对 Git 基线 `{baseline.revision}` 新增 "
                f"{len(introduced_findings)} 个缺失 docstring 的生产定义。"
            ),
            suggestion=(
                "为新增缺失定义补充准确 docstring，或删除不必要定义。覆盖率百分比仅展示，"
                "不得通过扩写无关 docstring 抵消缺失对象。"
            ),
            evidence={
                "baseline_revision": baseline.revision,
                "introduced_missing_count": len(introduced_findings),
                "introduced_missing_symbols": symbols,
                "introduced_missing_fingerprints": introduced,
                "baseline_coverage_percent": baseline.docstring_coverage,
                "current_coverage_percent": report.docstring_coverage,
                "coverage_percent_is_informational": True,
            },
        )
    )
    report.findings.sort(
        key=lambda item: (-SEVERITY_RANK[item.severity], item.path, item.line, item.code)
    )


def _append_quality_count_regression_finding(report: ScanReport) -> None:
    """逐项拒绝相对 Git 基线新增或升级的三档问题。

    历史问题的删除或降级只作为债务削减事实记录，绝不能抵消本轮新增问题。
    QG180/QG181 和项目核心协议 QG182 属于接口专用账本，由接口净额或
    修改说明语义门禁独立裁决，避免与 QG179 对同一接口变化重复计罪。
    """
    baseline = report.baseline
    if baseline is None:
        return
    severities = ("critical", "error", "warning")
    current_counts = dict.fromkeys(severities, 0)
    current_findings: dict[str, Finding] = {}
    for finding in report.findings:
        if (
            finding.code.startswith("QG98")
            or finding.code == "QG179"
            or finding.code in _INTERFACE_POLICY_CODES
            or finding.evidence.get("qg179_exempt") is True
            or finding.severity not in current_counts
        ):
            continue
        current_counts[finding.severity] += 1
        current_findings[finding.fingerprint] = finding
    report.quality_count_deltas = {
        severity: current_counts[severity] - baseline.summary[severity] for severity in severities
    }

    introduced: list[Finding] = []
    worsened: list[tuple[Finding, str]] = []
    rank = {"warning": 1, "error": 2, "critical": 3}
    for fingerprint, finding in current_findings.items():
        previous = baseline.finding_severities.get(fingerprint)
        if previous is None:
            introduced.append(finding)
        elif rank[finding.severity] > rank[previous]:
            worsened.append((finding, previous))
    if not introduced and not worsened:
        return

    introduced_counts = dict.fromkeys(severities, 0)
    worsened_counts = dict.fromkeys(severities, 0)
    for finding in introduced:
        introduced_counts[finding.severity] += 1
    for finding, _previous in worsened:
        worsened_counts[finding.severity] += 1
    detail_parts = []
    for severity in severities:
        count = introduced_counts[severity]
        upgraded = worsened_counts[severity]
        if count or upgraded:
            detail_parts.append(f"{severity.upper()} 新增 {count}、升级至该档 {upgraded}")
    report.findings.append(
        Finding(
            code="QG179",
            severity="critical",
            confidence="high",
            path=".",
            line=1,
            column=1,
            symbol="repository.quality_fingerprint_regression",
            message=(
                f"相对 Git 基线 `{baseline.revision}` 检测到不可抵消的新增或升级问题："
                + "；".join(detail_parts)
                + "。历史债务减少不抵消本轮回归。"
            ),
            suggestion=(
                "本门禁不可由修改说明或历史债务削减豁免。必须删除、修复或回退每一项"
                " INTRODUCED/WORSENED 问题后重新扫描。"
            ),
            evidence={
                "comparison_mode": report.comparison_mode,
                "comparison_target": report.comparison_target,
                "baseline_counts": {
                    severity: baseline.summary[severity] for severity in severities
                },
                "current_counts": current_counts,
                "raw_count_delta": dict(report.quality_count_deltas),
                "introduced": [
                    {
                        "fingerprint": finding.fingerprint,
                        "code": finding.code,
                        "severity": finding.severity,
                        "path": finding.path,
                        "line": finding.line,
                        "symbol": finding.symbol,
                    }
                    for finding in introduced
                ],
                "worsened": [
                    {
                        "fingerprint": finding.fingerprint,
                        "code": finding.code,
                        "baseline_severity": previous,
                        "current_severity": finding.severity,
                        "path": finding.path,
                        "line": finding.line,
                        "symbol": finding.symbol,
                    }
                    for finding, previous in worsened
                ],
            },
        )
    )
    report.findings.sort(
        key=lambda item: (-SEVERITY_RANK[item.severity], item.path, item.line, item.code)
    )


def _apply_git_quality_baseline(
    target: AuditTarget,
    config: GuardConfig,
    revision: str,
    report: ScanReport,
    profile: ProjectProfile | None = None,
) -> None:
    """扫描并应用生产/测试 Git 质量基线及不可豁免退化门禁。"""
    try:
        production_baseline, test_baseline = _scan_git_baseline(target, config, revision, profile)
    except RuntimeError as error:
        report.baseline_error = str(error)
        return
    report.baseline = production_baseline
    report.test_baseline = test_baseline
    _append_docstring_regression_finding(report)
    _append_quality_count_regression_finding(report)


def scan_target(args: argparse.Namespace) -> tuple[AuditTarget, ScanReport, InterfaceDiffReport]:
    """
    执行目标解析、生产/测试分账扫描、Git 差分、Ruff 与基线计算。

    Args:
        args: 现有扫描内核兼容的命令行参数命名空间。

    Returns:
        解析后的审计目标、生产扫描报告与接口差分报告。
    """
    target = resolve_target(args)
    comparison = _comparison_plan(target, args)
    scan_args = copy.copy(args)
    scan_args.diff_base = comparison.base_revision
    selection = _scan_profile_selection(target.root, scan_args)
    scan_args.profile = selection.reference
    profile = get_project_profile(scan_args.profile)
    base_config = apply_project_profile(
        GuardConfig.load(target.root),
        scan_args.profile,
        selection_source=selection.source,
    )
    production_config, test_config = (
        base_config.with_overrides(include_tests=False),
        base_config.with_overrides(include_tests=True),
    )
    current_analysis = (
        worktree_analysis_snapshot(target.root, base_config)
        if target.git_enabled
        else directory_analysis_snapshot(target.root, base_config)
    )
    base_analysis = (
        revision_analysis_snapshot(target.root, scan_args.diff_base, base_config)
        if target.git_enabled
        else None
    )
    comparison_analysis = (
        index_analysis_snapshot(target.root, base_config)
        if target.git_enabled and scan_args.staged
        else current_analysis
    )
    interface_diff = _run_interface_diff(
        target,
        base_config,
        scan_args,
        base_analysis=base_analysis,
        target_analysis=comparison_analysis,
        profile=profile,
    )

    production_scanner = RepositoryScanner(
        target.root,
        production_config,
        current_analysis,
    )
    selected_files = (
        set(target.focus_files) if target.focus_files and not target.git_enabled else None
    )
    report = production_scanner.scan(selected_files=selected_files)
    report.interface_diff = interface_diff
    report.comparison_mode = comparison.mode
    report.comparison_target = comparison.target_label

    base_relation_graph, relation_graph, relation_summary = build_relation_graph_summary(
        base_analysis if target.git_enabled else None,
        comparison_analysis,
        interface_diff,
    )
    report.relation_graph = relation_summary

    merge_findings(
        report,
        (
            finding
            for finding in interface_diff.contract_findings
            if not is_test_path(finding.path, base_config.project_name)
        ),
    )

    test_scanner = RepositoryScanner(target.root, test_config, current_analysis)
    test_files = {
        path
        for path in test_scanner.discover_python_files()
        if is_test_path(path.relative_to(target.root), base_config.project_name)
    }
    if selected_files is not None:
        test_files &= selected_files
    test_report = test_scanner.scan(selected_files=test_files)
    test_report.findings = _filter_test_quality_findings(
        test_report.findings,
        base_config.profile_test_baseline_passthrough_paths,
    )

    if target.git_enabled:
        architecture_findings = []
        if not scan_args.staged:
            architecture_findings.extend(
                compare_git_architecture(
                    target.root,
                    base_config,
                    scan_args.diff_base,
                    base_analysis=base_analysis,
                    target_analysis=current_analysis,
                )
            )
            architecture_findings.extend(
                single_use_chain_findings(
                    target.root,
                    production_config,
                    analysis_snapshot=current_analysis,
                )
            )
        architecture_findings.extend(
            fallback_laundering_findings(
                target.root,
                production_config,
                scan_args.diff_base,
                staged=scan_args.staged,
                base_analysis=base_analysis,
                target_analysis=comparison_analysis,
            )
        )
        architecture_findings.extend(
            semantic_repair_findings(
                target.root,
                production_config,
                scan_args.diff_base,
                staged=scan_args.staged,
                base_analysis=base_analysis,
                target_analysis=comparison_analysis,
            )
        )
        if "semantic-heuristic-candidates" in production_config.profile_capabilities:
            architecture_findings.extend(
                semantic_heuristic_candidate_findings(
                    target.root,
                    production_config,
                    scan_args.diff_base,
                    staged=scan_args.staged,
                    static_exemptions=(
                        profile.semantic_heuristic_exemptions if profile is not None else ()
                    ),
                    authorization_path=(
                        profile.semantic_authorization_path if profile is not None else ""
                    ),
                    target_analysis=comparison_analysis,
                )
            )
        merge_findings(
            report,
            (
                item
                for item in architecture_findings
                if not is_test_path(item.path, base_config.project_name)
            ),
        )
        merge_findings(
            test_report,
            (
                item
                for item in architecture_findings
                if is_test_path(item.path, base_config.project_name)
            ),
        )
    report.findings, report.relation_graph = apply_relation_finding_context(
        list(report.findings),
        relation_graph,
        base_relation_graph,
        report.relation_graph,
        interface_diff,
    )
    readme_finding = _readme_sync_finding(
        target, scan_args, interface_diff, base_config.project_name
    )
    if readme_finding is not None and not is_test_path(
        readme_finding.path, base_config.project_name
    ):
        report.findings.append(readme_finding)

    ruff_findings = RuffRunner(
        target.root,
        cache_root=_cache_root(),
        environment=os.environ,
    ).run(check_format=True)
    merge_findings(
        report,
        (item for item in ruff_findings if not is_test_path(item.path, base_config.project_name)),
    )
    merge_findings(
        test_report,
        (item for item in ruff_findings if is_test_path(item.path, base_config.project_name)),
    )
    language_findings, language_summary = multilang_findings(
        comparison_analysis,
        production_config,
        base_analysis if target.git_enabled else None,
    )
    merge_findings(report, language_findings)
    report.multilang_summary = language_summary
    identity_findings = release_identity_findings(target.root, production_config)
    # QG203/QG205 are semantic candidates regardless of whether the evidence
    # lives in production code, tests, fixtures or configuration.
    merge_findings(report, identity_findings)
    custom_findings = _profile_custom_findings(
        profile,
        RuleContext(
            root=target.root,
            config=production_config,
            analysis=comparison_analysis,
            base_analysis=base_analysis,
            interface_diff=interface_diff,
            diff_base=scan_args.diff_base,
            staged=scan_args.staged,
        ),
    )
    merge_findings(
        report,
        (item for item in custom_findings if not is_test_path(item.path, base_config.project_name)),
    )
    merge_findings(
        test_report,
        (item for item in custom_findings if is_test_path(item.path, base_config.project_name)),
    )
    _apply_profile_rule_policy(report, production_config)
    _apply_profile_rule_policy(test_report, test_config)
    _apply_report_extensions(report, profile)
    _apply_report_extensions(test_report, profile)
    interface_diff = report.interface_diff or interface_diff
    report.findings = annotate_semantic_review(list(report.findings))
    _apply_focus(report, target)
    _apply_focus(test_report, target)
    report.test_findings, report.test_files_scanned, report.test_definitions = (
        list(test_report.findings),
        test_report.files_scanned,
        test_report.definitions,
    )

    if target.git_enabled:
        _apply_git_quality_baseline(target, base_config, scan_args.diff_base, report, profile)
    return target, report, interface_diff


def _review_required_notice(
    report: ScanReport,
    interface_diff: InterfaceDiffReport,
) -> str:
    """渲染必须直接展示给用户的接口人工审核警告。"""
    lines = [
        "",
        "人工审核警告：当前版本存在生产接口或核心协议变动，不能自动批准提交。",
        "必须由用户逐项确认；接口变动较多时应视为改动范围过大，谨慎考虑拆分或重新设计。",
    ]
    for change in interface_diff.changes:
        if (
            is_test_path(change.path, report.project_name)
            or change.change == "removed"
            or change.kind == "file"
        ):
            continue
        details = "、".join(change.details) if change.details else "声明变化"
        lines.append(
            f"- [{change.change.upper()}][{change.kind}] {change.path}:{change.symbol}（{details}）"
        )
    for finding in interface_diff.contract_findings:
        if finding.code != "QG182" or is_test_path(finding.path, report.project_name):
            continue
        lines.append(
            f"- [PROTOCOL][{finding.code}] {finding.path}:{finding.symbol}（{finding.message}）"
        )
    return "\n".join(lines) + "\n"


def _final_gate_status(
    *,
    final_check: bool,
    report_contract_findings: list[Finding],
    tool_status: str,
    semantic_status: str,
) -> str:
    """合并报告、静态事实与模型结论，返回最终三态门禁。"""
    if (
        report_contract_findings
        or not final_check
        or "REJECT"
        in {
            tool_status,
            semantic_status,
        }
    ):
        return "REJECT"
    if "REVIEW_REQUIRED" in {tool_status, semantic_status}:
        return "REVIEW_REQUIRED"
    return "ACCEPT"


def _final_exit_code(
    final_status: str,
    *,
    report_contract_findings: list[Finding],
    code_exit: int,
) -> int:
    """把最终三态和报告契约结果映射为稳定退出码。"""
    if report_contract_findings:
        return REPORT_GATE_EXIT_CODE
    if final_status == "REJECT":
        return 1
    if final_status == "REVIEW_REQUIRED":
        return REVIEW_REQUIRED_EXIT_CODE
    return code_exit


def main(argv: list[str] | None = None) -> int:
    """
    执行完整性、代码和修改说明三道不可降级门禁。

    Args:
        argv: 可选命令行参数；为 None 时读取当前进程参数。

    Returns:
        稳定的质量门禁退出码。
    """
    args = build_parser().parse_args(argv)
    try:
        integrity_exit = integrity_gate()
        if integrity_exit is not None:
            return integrity_exit
        if args.verify_integrity:
            return 0
        api_exit = _run_api_catalog_mode(args)
        if api_exit is not None:
            return api_exit

        target, report, interface_diff = scan_target(args)
        revision = (
            report.baseline.revision if report.baseline is not None else (args.diff_base or "HEAD")
        )
        effective_interface_diff = report.interface_diff or interface_diff
        tool_status = code_status(report)
        code_exit = (
            2
            if effective_interface_diff.errors
            else 1
            if tool_status == "REJECT"
            else REVIEW_REQUIRED_EXIT_CODE
            if tool_status == "REVIEW_REQUIRED"
            else 0
        )
        modification_report = target.root / MODIFICATION_REPORT_NAME

        if args.final_check:
            if not modification_report.is_file():
                print(f"修改说明门禁：REJECT（缺少 {modification_report}）", file=sys.stderr)
                print(f"静态事实门禁：{tool_status}")
                return REPORT_GATE_EXIT_CODE
            strict_report = modification_report.read_text(encoding="utf-8")
        else:
            existing_report = (
                modification_report.read_text(encoding="utf-8")
                if modification_report.is_file()
                else None
            )
            strict_report = render_strict_modification_report(
                report,
                existing=existing_report,
                revision=revision,
            )
            modification_report.write_text(strict_report, encoding="utf-8")

        report_contract_findings = validate_modification_report(
            strict_report,
            report,
            revision=revision,
        )
        sys.stdout.write(render_audit_markdown(report))
        if report_contract_findings:
            sys.stdout.write("\n## 修改说明门禁问题（不计入代码三档统计）\n\n")
            for finding in report_contract_findings:
                sys.stdout.write(f"- [{finding.code}] {finding.message}\n")
        report_status = "PASS" if not report_contract_findings else "REJECT"
        semantic_status = (
            report_final_status(strict_report) if not report_contract_findings else "INVALID"
        )
        mode = "最终检查" if args.final_check else "生成后检查"
        sys.stdout.write(
            f"\n修改说明：{modification_report}\n"
            f"修改说明门禁（{mode}）：{report_status}\n"
            f"静态事实门禁：{tool_status}\n"
            f"模型语义结论：{semantic_status}\n"
        )
        if tool_status == "REVIEW_REQUIRED":
            sys.stdout.write(_review_required_notice(report, effective_interface_diff))
        final_status = _final_gate_status(
            final_check=args.final_check,
            report_contract_findings=report_contract_findings,
            tool_status=tool_status,
            semantic_status=semantic_status,
        )
        sys.stdout.write(f"最终门禁：{final_status}\n")
        return _final_exit_code(
            final_status,
            report_contract_findings=report_contract_findings,
            code_exit=code_exit,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"quality-guard 执行失败: {error}", file=sys.stderr)
        return 2
