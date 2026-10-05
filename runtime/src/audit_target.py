"""Repository scope resolution and explicit or historical Git comparison planning."""

from __future__ import annotations
import argparse
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from .config import is_tool_generated_path
from .git_utils import run_readonly_git

GIT_COMMAND_NOT_FOUND_EXIT_CODE = 127
MODIFICATION_REPORT_NAME = "修改说明.md"
_PORCELAIN_STATUS_PREFIX_LENGTH = 3
_AUDIT_ONLY_SUFFIXES = (".patch", ".diff", ".log", ".zip", ".tar", ".tgz", ".tar.gz")
_AUDIT_ONLY_PREFIXES = (
    ".agents/",
    ".pytest_cache/",
    ".ruff_cache/",
    "docs/api-reference/",
)


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
class GitComparisonPlan:
    """Bind a Git baseline to the requested index or working-tree target.

    The selection records why that baseline was chosen so callers can display
    the same comparison provenance as the scanner actually used.
    """

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


def git_stdout(root: Path, arguments: tuple[str, ...]) -> str:
    """执行一组只读 Git 参数并返回标准输出。

    Args:
        root: Git 仓库根目录。
        arguments: 不包含 ``git`` 本身的固定参数序列。

    Returns:
        命令成功时去除首尾空白的标准输出；失败时为空串。
    """
    result = run_readonly_git(root, arguments)
    return result.stdout.strip() if result.returncode == 0 else ""


def _reflog_push_revision(root: Path, refs: list[str]) -> tuple[str, str] | None:
    """Return the newest pushed ancestor proven by a tracking-ref reflog."""
    for ref in refs:
        reflog = git_stdout(root, ("reflog", "show", "--format=%H%x09%gs", ref))
        for line in reflog.splitlines():
            commit, separator, subject = line.partition("\t")
            lowered = subject.lower()
            pushed = "update by push" in lowered or lowered.startswith("push:")
            if not separator or not pushed:
                continue
            verified = git_stdout(
                root,
                ("rev-parse", "--verify", f"{commit}^{{commit}}"),
            )
            if (
                verified
                and run_readonly_git(
                    root, ("merge-base", "--is-ancestor", verified, "HEAD")
                ).returncode
                == 0
            ):
                return verified, f"{ref} reflog"
    return None


def _remote_head_revision(root: Path, refs: list[str]) -> tuple[str, str] | None:
    """Return a local ancestor matching one of the current remote branch heads."""
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
        commit = (
            result.stdout.partition("\t")[0].strip() if result.returncode == 0 else ""
        )
        verified = (
            git_stdout(root, ("rev-parse", "--verify", f"{commit}^{{commit}}"))
            if commit
            else ""
        )
        if (
            verified
            and run_readonly_git(
                root, ("merge-base", "--is-ancestor", verified, "HEAD")
            ).returncode
            == 0
        ):
            return verified, f"remote {remote}/{branch}"
    return None


def _last_pushed_revision(root: Path) -> tuple[str, str] | None:
    """定位最后一次可证明的远端批次基线。

    优先采用远端跟踪 reflog 中明确标记为 push 的祖先提交；本机 reflog
    不可用时只读查询远端分支头，最后才采用 upstream 或其共同祖先。

    Args:
        root: Git 仓库根目录。

    Returns:
        ``(commit, source)``；无法可靠定位时返回 None。
    """
    push_ref = git_stdout(
        root,
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{push}"),
    )
    upstream = git_stdout(
        root,
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
    )
    refs = list(dict.fromkeys(ref for ref in (push_ref, upstream) if ref))
    if not refs:
        refs = [
            ref
            for ref in git_stdout(
                root,
                ("for-each-ref", "--format=%(refname:short)", "refs/remotes"),
            ).splitlines()
            if ref and not ref.endswith("/HEAD")
        ]
    pushed = _reflog_push_revision(root, refs)
    if pushed is not None:
        return pushed
    remote_head = _remote_head_revision(root, refs)
    if remote_head is not None:
        return remote_head
    upstream_commit = (
        git_stdout(root, ("rev-parse", "--verify", f"{upstream}^{{commit}}"))
        if upstream
        else ""
    )
    if not upstream_commit:
        return None
    if (
        run_readonly_git(
            root, ("merge-base", "--is-ancestor", upstream_commit, "HEAD")
        ).returncode
        == 0
    ):
        return upstream_commit, f"upstream {upstream}"
    common = git_stdout(root, ("merge-base", "HEAD", upstream_commit))
    return (
        (common, f"diverged upstream common ancestor with {upstream}")
        if common
        else None
    )


def _relevant_worktree_changes(status_output: str) -> list[str]:
    """Extract non-audit-only paths from ``git status --porcelain=v1`` output."""
    relevant_changes: list[str] = []
    for raw_line in status_output.splitlines():
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
    return relevant_changes


def git_comparison_plan(
    target: AuditTarget, args: argparse.Namespace
) -> GitComparisonPlan:
    """Resolve the explicit or last-push baseline for this audit selection.

    Args:
        target: Repository and focus selection with resolved Git capability.
        args: CLI request containing baseline and staged-target preferences.

    Returns:
        Baseline, comparison target and human-readable provenance.
    """
    requested_target = "STAGED" if args.staged else "WORKTREE"
    if args.diff_base:
        return GitComparisonPlan(
            args.diff_base,
            requested_target,
            f"显式基线：{args.diff_base}→{requested_target}",
        )
    if not target.git_enabled:
        return GitComparisonPlan(
            "HEAD", requested_target, "非 Git 输入：自动基线不可用"
        )
    status = run_readonly_git(
        target.root, ("status", "--porcelain=v1", "--untracked-files=all")
    )
    if status.returncode != 0:
        detail = status.stderr.strip() or f"exit={status.returncode}"
        raise RuntimeError(f"无法判断 Git dirty/clean 状态：{detail}")
    relevant_changes = _relevant_worktree_changes(status.stdout)
    effective_target = requested_target if args.staged or relevant_changes else "HEAD"
    pushed = _last_pushed_revision(target.root)
    if pushed is not None:
        revision, source = pushed
        return GitComparisonPlan(
            revision,
            effective_target,
            "自动累计批次基线：最后一次 Git push/远端批次 "
            f"`{revision[:12]}`（{source}）→{effective_target}",
        )
    if relevant_changes:
        return GitComparisonPlan(
            "HEAD",
            requested_target,
            f"自动 dirty 回退基线：HEAD→{requested_target}（{len(relevant_changes)} 个有效变更路径）",
        )
    parent = git_stdout(
        target.root,
        ("rev-parse", "--verify", "HEAD~1^{commit}"),
    )
    if not parent:
        raise RuntimeError(
            "无法定位最后一次 push/upstream，且 clean 工作树没有可用父提交；请显式传入 --diff-base。"
        )
    return GitComparisonPlan(
        parent, "HEAD", f"自动 clean 回退基线：HEAD~1 `{parent[:12]}`→HEAD"
    )


def _resolve_files_target(raw_files: str) -> AuditTarget:
    """Resolve the ``--files`` selection and its shared Git ownership, if any."""
    files: list[Path] = []
    seen: set[Path] = set()
    for item in raw_files.split(","):
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
    issue_messages = tuple(
        lookup.issue_message for lookup in lookups if lookup.issue_message
    )
    issue_suggestions = tuple(
        lookup.issue_suggestion for lookup in lookups if lookup.issue_suggestion
    )
    git_issue_message = (
        issue_messages[0]
        if issue_messages
        else "指定文件不属于同一个 Git 仓库；接口 diff 已跳过。"
    )
    git_issue_suggestion = (
        issue_suggestions[0]
        if issue_suggestions
        else "确认这是预期的非 Git/跨仓库输入，而不是路径传错。"
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
        return _resolve_files_target(args.files)
    raw_target = args.project or args.path or "."
    target = Path(raw_target).expanduser().resolve()
    if target.is_file():
        git_lookup = _git_root(target)
        if git_lookup.root is not None:
            return AuditTarget(
                root=git_lookup.root,
                focus_files=frozenset({target}),
                focus_roots=(),
                notes=(
                    f"按 Git 根目录 `{git_lookup.root}` 解析仓库事实；报告输出聚焦文件 `{target}`。",
                ),
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
        notes=(
            f"按 Git 根目录 `{git_root}` 全仓库解析调用关系；报告输出聚焦子目录 `{target}`。",
        ),
        git_enabled=True,
    )
