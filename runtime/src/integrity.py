from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

MANIFEST_NAME = "runtime/MANIFEST.sha256"
RELEASE_LOCK_NAME = "runtime/RELEASE.lock"
RELEASE_HOME_ENV = "REPO_QUALITY_GUARD_HOME"
RELEASE_SEAL_ENV = "REPO_QUALITY_GUARD_RELEASE_SEAL"
INTEGRITY_RULE_CODE = "QG990"
_PROTECTED_ROOT_FILES = frozenset(
    {
        "SKILL.md",
        "scripts/quality_guard.py",
        "scripts/git_hook_install.py",
        "references/RULES.md",
        "references/AUDIT.md",
        "references/CODING_GUIDE.md",
        "runtime/deploy.py",
        "runtime/profile_build.py",
        "runtime/requirements.txt",
        "runtime/dependencies.lock.json",
        "runtime/install_dependencies.py",
        "runtime/THIRD_PARTY_NOTICES.md",
        "runtime/RELEASE.lock",
    }
)
_PROTECTED_PREFIXES = (
    "runtime/src/",
    "offline/",
    "templates/",
    "profiles/",
    "installed/",
)
_IGNORED_SUFFIXES = (".pyc", ".pyo")
_MANIFEST_PARTS = 2
_SHA256_HEX_LENGTH = 64


@dataclass(slots=True, frozen=True)
class IntegrityResult:
    """
    保存发布内容完整性校验结果。

    Attributes:
        root: 经过解析的发布包根目录；启动入口非法时为 None。
        issues: 完整性校验发现的不可忽略问题。
        protected_files: 清单中受保护文件的数量。
    """

    root: Path | None
    issues: tuple[str, ...]
    protected_files: int = 0

    @property
    def passed(self) -> bool:
        """
        返回完整性门禁是否通过。

        Returns:
            没有任何完整性问题时返回 True。
        """
        return not self.issues


def _sha256(path: Path) -> str:
    """计算文件原始字节的 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protected_sha256(path: Path, relative: str) -> str:
    """计算受保护文件摘要，并消除发布 seal 的自引用字段。"""
    content = path.read_bytes()
    if relative == "scripts/quality_guard.py":
        content = re.sub(
            rb'RELEASE_SEAL = "[0-9a-f]{64}"',
            b'RELEASE_SEAL = "' + (b"0" * _SHA256_HEX_LENGTH) + b'"',
            content,
        )
    elif relative == RELEASE_LOCK_NAME:
        content = re.sub(
            rb"manifest_sha256=[0-9a-f]{64}",
            b"manifest_sha256=" + (b"0" * _SHA256_HEX_LENGTH),
            content,
        )
    return hashlib.sha256(content).hexdigest()


def _parse_manifest(path: Path) -> tuple[dict[str, str], list[str]]:
    """解析固定格式的 SHA-256 清单。"""
    entries: dict[str, str] = {}
    issues: list[str] = []
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("  ", 1)
        if len(parts) != _MANIFEST_PARTS or len(parts[0]) != _SHA256_HEX_LENGTH:
            issues.append(f"{MANIFEST_NAME}:{number} 格式非法。")
            continue
        digest, relative = parts
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            issues.append(f"{MANIFEST_NAME}:{number} 包含越界路径 `{relative}`。")
            continue
        normalized = candidate.as_posix()
        if normalized in entries:
            issues.append(f"{MANIFEST_NAME}:{number} 重复声明 `{normalized}`。")
            continue
        entries[normalized] = digest
    return entries, issues


def _protected_candidates(root: Path) -> set[str]:
    """枚举必须出现在清单中的运行时代码、规则、脚本与源码回归测试。"""
    result: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts or relative.endswith(_IGNORED_SUFFIXES):
            continue
        if relative in _PROTECTED_ROOT_FILES or relative.startswith(
            _PROTECTED_PREFIXES
        ):
            result.add(relative)
    return result


def _lock_values(path: Path) -> dict[str, str]:
    """读取发布锁中的键值。"""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _lock_schema_issues(lock: dict[str, str]) -> list[str]:
    """返回发布锁缺失或非法字段问题。"""
    required = {
        "schema",
        "version",
        "distribution",
        "manifest_sha256",
        "protected_file_count",
    }
    missing = sorted(required - lock.keys())
    issues = ["RELEASE.lock 缺少必需字段：" + ", ".join(missing)] if missing else []
    if missing:
        return issues
    if lock["distribution"] not in {"skill", "agents"}:
        issues.append("RELEASE.lock 的 distribution 只能是 skill 或 agents。")
    return issues


def _legacy_payload_issues(root: Path) -> list[str]:
    """拒绝废弃布局；仅 `skill` 允许受保护离线依赖介质，`agents` 必须最小化。"""
    issues: list[str] = []
    forbidden = (
        "tests",
        "node_modules",
        "runtime/vendor",
        "runtime/wheels",
        "runtime/node",
        "runtime/releases",
        "runtime/CURRENT",
        "runtime/src/repo_quality_guard",
    )
    for relative in forbidden:
        if (root / relative).exists():
            issues.append(f"正式发布不得包含废弃/非运行时路径 `{relative}`。")
    lock_path = root / RELEASE_LOCK_NAME
    if lock_path.is_file():
        lock = _lock_values(lock_path)
        if lock["distribution"] == "agents" and (root / "offline").exists():
            issues.append("安装态 `.agents` 不得包含 `offline/` 离线依赖介质。")
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".pyo"} or path.name in {
            ".ruff_cache",
            ".pytest_cache",
        }:
            issues.append(
                f"正式发布包含构建缓存 `{path.relative_to(root).as_posix()}`。"
            )
            break
    return issues


def _instruction_issues(root: Path, lock: dict[str, str]) -> list[str]:
    """验证核心 Skill 指令存在。"""
    issues: list[str] = []
    skill_path = root / "SKILL.md"
    if not skill_path.is_file():
        issues.append("Skill 发布缺少 SKILL.md。")
    return issues


def seal_release_tree(root: Path, distribution: str) -> str:
    """
    重新封存完整 release tree，并返回新的 manifest seal。

    Args:
        root: 待封存的 release 根目录。
        distribution: ``skill`` 或 ``agents``。

    Returns:
        ``runtime/MANIFEST.sha256`` 的 SHA-256。

    Raises:
        ValueError: distribution 非法或现有 RELEASE.lock 缺少版本。
        RuntimeError: 启动器缺少唯一 RELEASE_SEAL 字段。
    """
    if distribution != "skill" and distribution != "agents":
        raise ValueError("distribution 只能是 skill 或 agents。")
    root = root.resolve()
    lock_path = root / RELEASE_LOCK_NAME
    if not lock_path.is_file():
        raise ValueError("release 缺少 runtime/RELEASE.lock。")
    current_lock = _lock_values(lock_path)
    version = current_lock["version"]
    if not version:
        raise ValueError("runtime/RELEASE.lock 的 version 不能为空。")

    launcher = root / "scripts" / "quality_guard.py"

    candidates = sorted(_protected_candidates(root))
    lock_path.write_text(
        "\n".join(
            [
                "schema=repository-quality-guard/release-v1",
                f"version={version}",
                f"distribution={distribution}",
                f"manifest_sha256={'0' * _SHA256_HEX_LENGTH}",
                f"protected_file_count={len(candidates)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    manifest = root / MANIFEST_NAME
    entries = [
        f"{_protected_sha256(root / relative, relative)}  {relative}"
        for relative in candidates
    ]
    manifest.write_text("\n".join(entries) + "\n", encoding="utf-8")
    seal = _sha256(manifest)

    launcher_text = launcher.read_text(encoding="utf-8")
    marker = 'RELEASE_SEAL = "'
    start = launcher_text.index(marker) + len(marker)
    end = start + _SHA256_HEX_LENGTH
    if launcher_text[end : end + 1] != '"':
        raise RuntimeError("scripts/quality_guard.py 的 RELEASE_SEAL 结构非法。")
    launcher.write_text(
        launcher_text[:start] + seal + launcher_text[end:],
        encoding="utf-8",
    )
    lock_path.write_text(
        lock_path.read_text(encoding="utf-8").replace(
            f"manifest_sha256={'0' * _SHA256_HEX_LENGTH}",
            f"manifest_sha256={seal}",
            1,
        ),
        encoding="utf-8",
    )
    return seal


def verify_release_integrity(
    environment: dict[str, str] | None = None,
) -> IntegrityResult:
    """
    验证 Skill 发布文件没有被删除、增加或改写。

    Args:
        environment: 包含发布根目录和内置摘要的环境变量映射；为空时读取当前进程环境。

    Returns:
        包含发布根目录、受保护文件数量和全部完整性问题的校验结果。
    """
    source = os.environ if environment is None else environment
    home = source.get(RELEASE_HOME_ENV, "").strip()
    expected_seal = source.get(RELEASE_SEAL_ENV, "").strip()
    if not home or not expected_seal:
        return IntegrityResult(
            None,
            (
                "必须通过 scripts/quality_guard.py 启动，"
                "当前进程缺少不可降级的发布根与 release seal。",
            ),
        )
    root = Path(home).resolve()
    manifest = root / MANIFEST_NAME
    release_lock = root / RELEASE_LOCK_NAME
    missing_files = [
        name
        for name, path in (
            (MANIFEST_NAME, manifest),
            (RELEASE_LOCK_NAME, release_lock),
        )
        if not path.is_file()
    ]
    if missing_files:
        return IntegrityResult(
            root,
            tuple(f"缺少 `{name}`。" for name in missing_files),
        )
    issues: list[str] = []

    actual_seal = _sha256(manifest)
    if actual_seal != expected_seal:
        issues.append(
            "发布清单摘要与启动器内置 release seal 不一致，Skill 可能被改写。"
        )
    lock = _lock_values(release_lock)
    lock_issues = _lock_schema_issues(lock)
    issues.extend(lock_issues)
    if lock_issues:
        return IntegrityResult(root, tuple(issues))
    if lock["manifest_sha256"] != expected_seal:
        issues.append("RELEASE.lock 与启动器内置 release seal 不一致。")
    issues.extend(_instruction_issues(root, lock))
    issues.extend(_legacy_payload_issues(root))

    entries, manifest_issues = _parse_manifest(manifest)
    issues.extend(manifest_issues)
    candidates = _protected_candidates(root)
    declared = set(entries)
    missing_declarations = sorted(candidates - declared)
    stale_declarations = sorted(declared - candidates)
    if missing_declarations:
        issues.append("受保护目录出现未登记文件：" + ", ".join(missing_declarations))
    if stale_declarations:
        issues.append("清单声明的受保护文件不存在：" + ", ".join(stale_declarations))

    for relative, expected in sorted(entries.items()):
        path = root / relative
        if not path.is_file():
            continue
        if _protected_sha256(path, relative) != expected:
            issues.append(f"受保护文件摘要不一致：`{relative}`。")
    expected_count = lock["protected_file_count"]
    if expected_count.isdigit() and int(expected_count) != len(entries):
        issues.append("RELEASE.lock 的受保护文件数量与清单不一致。")
    return IntegrityResult(root, tuple(issues), len(entries))
