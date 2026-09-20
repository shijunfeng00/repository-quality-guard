"""Detect repository coupling to release/version identities.

QG203 is intentionally narrow: it only reports release-like identity encoded in
repository path names. A path name is a long-lived structural contract, so the
generic policy treats it as Critical and lets the normal Git baseline decide
whether it is historical debt or a newly introduced regression.

QG205 is intentionally broad and semantic-only by default: versions, Git tags,
commits, SHA/digests and release labels inside file contents can be legitimate
API/protocol/dependency/schema/migration/integrity contracts. Profiles may
promote either rule, including to an absolute BLOCKER.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from .config import GuardConfig
from .model import Finding

_SOURCE = "quality-guard-release-identity"
_TEXT_LIMIT_BYTES = 2 * 1024 * 1024
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)
_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".a",
        ".bin",
        ".bz2",
        ".class",
        ".dll",
        ".dylib",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".o",
        ".obj",
        ".pdf",
        ".png",
        ".pyc",
        ".pyo",
        ".so",
        ".svg",
        ".tar",
        ".ttf",
        ".whl",
        ".woff",
        ".woff2",
        ".xz",
        ".zip",
        ".zst",
    }
)

# Strong project-release-looking labels. Plain semantic versions are *not*
# QG203 because they can be valid protocol/dependency values in content.
_STRONG_RELEASE_RE = re.compile(
    r"(?ix)(?:"
    r"(?<![A-Za-z0-9])v\d+\.\d+(?:\.\d+)?(?:[-_.]?(?:rc|alpha|beta|pre|post)\d+)?(?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9])rc\d+(?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9])v\d{2,}[-_.]?rc\d+(?![A-Za-z0-9])"
    r")"
)
_SEMVER_RE = re.compile(
    r"(?i)(?<![.A-Za-z0-9])\d+\.\d+\.\d+(?:[-_.]?(?:alpha|beta|pre|post)\d+)?(?![.A-Za-z0-9])"
)
_TWO_PART_VERSION_RE = re.compile(r"(?i)(?<![.A-Za-z0-9])\d+\.\d+(?![.A-Za-z0-9])")
_VERSION_CONTEXT_RE = re.compile(
    r"(?i)version|requires[-_ ]?python|python|dependency|dependencies|package|dsl|api|schema|protocol|compat|migration|upstream"
)
_DEPENDENCY_FILENAMES = frozenset(
    {
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "poetry.lock",
        "pipfile",
        "pipfile.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "cargo.toml",
        "cargo.lock",
        "go.mod",
        "go.sum",
    }
)
_DEPENDENCY_COMPARATOR_RE = re.compile(r"(?:>=|<=|==|~=|!=|>|<)\s*$")
_FULL_DIGEST_RE = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{40}|[0-9a-f]{64})(?![0-9a-f])"
)
_SHORT_COMMIT_RE = re.compile(
    r"(?i)(?:commit|revision|rev|baseline|parent|tree|sha(?:1|256)?)\s*[:=]\s*[\"']?([0-9a-f]{7,39})(?![0-9a-f])"
)


@dataclass(frozen=True, slots=True)
class _IdentityMatch:
    """Represent one release/version identity token found in file content."""

    kind: str
    token: str
    line: int
    column: int


def _git_tags(root: Path) -> tuple[str, ...]:
    """Return release-like Git tags; non-Git directories simply have none."""
    result = subprocess.run(
        ["git", "tag", "--list"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ()
    return tuple(
        sorted(
            {
                item.strip()
                for item in result.stdout.splitlines()
                if item.strip() and _STRONG_RELEASE_RE.search(item.strip())
            }
        )
    )


def _repository_paths(root: Path) -> tuple[Path, ...]:
    """Return repository-owned files, with a filesystem fallback for non-Git input."""
    listed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if listed.returncode == 0:
        paths: list[Path] = []
        for raw in listed.stdout.split(b"\0"):
            if not raw:
                continue
            candidate = root / os.fsdecode(raw)
            if candidate.is_file():
                paths.append(candidate)
        return tuple(
            sorted(set(paths), key=lambda item: item.relative_to(root).as_posix())
        )

    paths: list[Path] = []
    for current_root, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name not in _SKIP_DIRS]
        current = Path(current_root)
        for filename in filenames:
            candidate = current / filename
            if candidate.is_file():
                paths.append(candidate)
    return tuple(sorted(paths, key=lambda item: item.relative_to(root).as_posix()))


def _read_text(path: Path) -> str | None:
    """Read bounded UTF-8 text and ignore binary/media artifacts."""
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return None
    try:
        if path.stat().st_size > _TEXT_LIMIT_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _has_two_part_version_context(
    line: str, match: re.Match[str], relative: str
) -> bool:
    """Return whether a two-part decimal is locally version-like.

    Minified HTML/JS can place unrelated CSS/SVG decimals on the same physical
    line as a distant ``version`` field or comparison expression.  Requiring a
    bounded local window prevents those visual/numeric values from becoming
    release-identity findings.  Dependency manifests additionally accept a
    package comparator immediately preceding the token.
    """
    # Version labels normally sit immediately beside the value.  Keep the
    # window deliberately asymmetric/tight so a distant ``versionLabel`` in
    # minified JS cannot bless later numeric comparisons or SVG/CSS decimals.
    begin = max(0, match.start() - 24)
    end = min(len(line), match.end() + 16)
    nearby = line[begin:end]
    if _VERSION_CONTEXT_RE.search(nearby):
        return True

    name = Path(relative).name.lower()
    dependency_file = (
        name in _DEPENDENCY_FILENAMES
        or name.startswith("requirements")
        and name.endswith((".txt", ".in"))
    )
    if not dependency_file:
        return False
    prefix = line[max(0, match.start() - 8) : match.start()]
    return _DEPENDENCY_COMPARATOR_RE.search(prefix) is not None


def _line_identity_matches(
    line: str, line_number: int, git_tags: tuple[str, ...], relative: str
) -> list[_IdentityMatch]:
    """Collect release/version identity tokens from one text line.

    Keeping the per-line parser separate makes each token family explicit while
    avoiding one orchestration function with excessive branching.
    """
    matches = [
        _IdentityMatch("release", match.group(0), line_number, match.start() + 1)
        for match in _STRONG_RELEASE_RE.finditer(line)
    ]
    matches.extend(
        _IdentityMatch("version", match.group(0), line_number, match.start() + 1)
        for match in _SEMVER_RE.finditer(line)
        if not (
            match.start() > 0 and line[match.start() - 1 : match.start()].lower() == "v"
        )
    )
    matches.extend(
        _IdentityMatch("version", match.group(0), line_number, match.start() + 1)
        for match in _TWO_PART_VERSION_RE.finditer(line)
        if not (
            match.start() > 0 and line[match.start() - 1 : match.start()].lower() == "v"
        )
        and _has_two_part_version_context(line, match, relative)
    )
    matches.extend(
        _IdentityMatch("digest", match.group(0), line_number, match.start() + 1)
        for match in _FULL_DIGEST_RE.finditer(line)
    )
    matches.extend(
        _IdentityMatch("commit", match.group(1), line_number, match.start(1) + 1)
        for match in _SHORT_COMMIT_RE.finditer(line)
    )
    matches.extend(
        _IdentityMatch("git-tag", tag, line_number, start + 1)
        for tag in git_tags
        if (start := line.find(tag)) >= 0
    )
    return matches


def release_identity_findings(root: Path, config: GuardConfig) -> tuple[Finding, ...]:
    """Scan structural path identity and semantic content identity.

    The detector is project-agnostic.  QG203 is deliberately limited to
    path/file-name identity; QG205 records content identity for later semantic
    adjudication.  Profile severity/semantic/blocker policy is applied by the
    common CLI layer.

    Args:
        root: Repository root or non-Git directory selected for audit.
        config: Active scanner configuration, including explicit path excludes.

    Returns:
        Stable, sorted release-identity findings for the selected repository.
    """
    root = root.resolve()
    git_tags = _git_tags(root)
    findings: list[Finding] = []

    for path in _repository_paths(root):
        relative = path.relative_to(root).as_posix()
        if Path(relative).name.lower() == "readme.md":
            continue
        if any(fnmatch(relative, pattern) for pattern in config.exclude):
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue

        normalized = relative.replace("\\", "/")
        path_match = _STRONG_RELEASE_RE.search(normalized)
        if path_match is None:
            path_match = re.search(
                r"(?i)(?:^|[/_.-])(v\d{2,}[-_.]?rc\d+)(?:$|[/_.-])", normalized
            )
        if path_match is not None:
            path_token = path_match.group(0).strip("/_.-")
            findings.append(
                Finding(
                    code="QG203",
                    severity="critical",
                    confidence="high",
                    path=relative,
                    line=1,
                    column=1,
                    message=f"仓库路径/文件名绑定 release-like identity `{path_token}`。",
                    suggestion=(
                        "将当前代码、测试、fixture、artifact 或文档路径改成稳定职责/能力/领域名称；"
                        "发布身份应由 Git/release evidence 承载。"
                    ),
                    evidence={
                        "mechanism": "path-release-identity",
                        "token": path_token,
                    },
                    source=_SOURCE,
                )
            )

        text = _read_text(path)
        if text is None:
            continue
        raw_matches = [
            item
            for line_number, line in enumerate(text.splitlines(), 1)
            for item in _line_identity_matches(line, line_number, git_tags, relative)
        ]
        if not raw_matches:
            continue
        matches = sorted(
            {
                (item.line, item.column, item.kind, item.token): item
                for item in raw_matches
            }.values(),
            key=lambda item: (item.line, item.column, item.kind, item.token),
        )
        first = matches[0]
        findings.append(
            Finding(
                code="QG205",
                severity="info",
                confidence="medium",
                path=relative,
                line=first.line,
                column=first.column,
                message=(
                    "文件内容包含版本、release label、Git tag、commit/SHA 或固定 digest，"
                    "需要语义判断其是否属于稳定契约。"
                ),
                suggestion=(
                    "合法 API/协议/依赖/schema/迁移/完整性契约可以保留；若它只是项目自身"
                    "发布 lineage 并驱动当前实现、测试或文档契约，则改用稳定能力/领域标识。"
                ),
                evidence={
                    "mechanism": "release-identity-semantic-review",
                    "matches": [
                        {"kind": item.kind, "token": item.token, "line": item.line}
                        for item in matches[:16]
                    ],
                    "git_tags_checked": list(git_tags),
                },
                source=_SOURCE,
            )
        )

    return tuple(
        sorted(
            findings, key=lambda item: (item.path, item.line, item.code, item.message)
        )
    )
