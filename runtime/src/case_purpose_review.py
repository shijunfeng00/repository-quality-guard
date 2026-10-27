"""Review declarations for changed concrete behavior Cases, never infer intent."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .config import is_test_path
from .git_utils import run_readonly_git

_CASE_BASES = frozenset({"TestCase", "StatefulTestCase"})
_CASE_MODULES = frozenset({"rqgtest"})


@dataclass(frozen=True, slots=True)
class CasePurpose:
    """A concrete changed Case bound to its current AST identity.

    The immutable digest is tied to a source-certified framework root.
    """

    item_id: str
    path: str
    case_name: str
    source_sha256: str
    kind: str


def _qualified_name(value: ast.expr) -> str:
    """Return a statically named attribute chain without dynamic resolution."""
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return _qualified_name(value.value) + "." + value.attr
    return ""


def _forget_shadowed(bindings: dict[str, str], name: str) -> None:
    """Invalidate imported symbols when another declaration owns their name."""
    for reference in tuple(bindings):
        if reference == name or reference.startswith(name + "."):
            del bindings[reference]


def _register_case_import(
    node: ast.Import | ast.ImportFrom, bindings: dict[str, str]
) -> None:
    """Bind only import-qualified framework roots from the official contract."""
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        for alias in node.names:
            name = alias.asname or alias.name
            _forget_shadowed(bindings, name)
            if module in _CASE_MODULES and alias.name in _CASE_BASES:
                bindings[name] = module + "." + alias.name
            elif module + "." + alias.name in _CASE_MODULES:
                bindings[name] = module + "." + alias.name
    else:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            _forget_shadowed(bindings, name)
            if alias.name in _CASE_MODULES:
                binding = name if alias.asname else alias.name
                bindings[binding] = alias.name


def _case_nodes(source: str) -> dict[str, tuple[ast.ClassDef, str]]:
    """Accept direct standard Case roots with identity-bound imported aliases."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    bound: dict[str, str] = {}
    cases: dict[str, tuple[ast.ClassDef, str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            _register_case_import(node, bound)
            continue
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                qualified = _qualified_name(base)
                direct = bound.get(qualified)
                if direct is not None and direct.rsplit(".", 1)[-1] in _CASE_BASES:
                    cases[node.name] = (node, direct)
                    break
                reference, dot, suffix = qualified.rpartition(".")
                if (
                    dot
                    and suffix in _CASE_BASES
                    and bound.get(reference) in _CASE_MODULES
                ):
                    cases[node.name] = (node, bound[reference] + "." + suffix)
                    break
            _forget_shadowed(bound, node.name)
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _forget_shadowed(bound, node.name)
            continue
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        shadowed = {
            child.id
            for target in targets
            for child in ast.walk(target)
            if isinstance(child, ast.Name)
        }
        for name in shadowed:
            _forget_shadowed(bound, name)
    return cases


def _git_text(root: Path, spec: str) -> str | None:
    """Read a committed or indexed Git blob without writing repository state."""
    result = run_readonly_git(root, ("show", spec))
    return result.stdout if result.returncode == 0 else None


def _is_case_source(path: str) -> bool:
    """Follow the existing recognized test-file contract across Git renames."""
    return (
        is_test_path(path) and path.endswith(".py") and Path(path).name != "__init__.py"
    )


def _changed_python_origins(
    root: Path, revision: str, staged: bool
) -> tuple[tuple[str, str | None, str], ...]:
    """Map changed test paths to their original Git paths across renames.

    Git's rename detection compares content rather than Python class names.  A
    pure file move must not create a new business Case review obligation.
    """
    command = ["diff", "--name-status", "-z", "-M", "--diff-filter=ACMRD"]
    if staged:
        command.append("--cached")
    command.extend([revision, "--"])
    changed = run_readonly_git(root, command)
    if changed.returncode != 0:
        raise RuntimeError(f"TestCase Git diff 查询失败：{changed.stderr.strip()}")
    tokens = changed.stdout.split("\0")
    if tokens[-1] != "":
        raise RuntimeError("TestCase Git diff 输出缺少 NUL 终止符。")
    changes: dict[str, tuple[str | None, str]] = {}
    index = 0
    while index < len(tokens) - 1:
        status = tokens[index]
        index += 1
        if not status or status[0] not in "ACMRD":
            raise RuntimeError(f"TestCase Git diff 状态无效：{status!r}")
        if status[0] == "R":
            original, path = tokens[index : index + 2]
            index += 2
        else:
            path = tokens[index]
            original = None if status[0] == "A" else path
            index += 1
        if status[0] == "R" and _is_case_source(original) and not _is_case_source(path):
            # Moving a test to a production/non-test path retires its test duty.
            changes[original] = (original, "REMOVED")
        elif _is_case_source(path):
            previous = (
                original if original is not None and _is_case_source(original) else None
            )
            changes[path] = (previous, "REMOVED" if status[0] == "D" else "CURRENT")
    if not staged:
        untracked = run_readonly_git(
            root, ("ls-files", "--others", "--exclude-standard", "-z")
        )
        if untracked.returncode != 0:
            raise RuntimeError("TestCase 未跟踪文件查询失败。")
        added = tuple(
            path
            for path in filter(None, untracked.stdout.split("\0"))
            if is_test_path(path)
            and path.endswith(".py")
            and Path(path).name != "__init__.py"
        )
        if added:
            deleted = run_readonly_git(
                root, ("diff", "--name-only", "-z", "--diff-filter=D", revision, "--")
            )
            if deleted.returncode != 0:
                raise RuntimeError("TestCase 已删除文件查询失败。")
            previous: dict[str, list[str]] = {}
            for source_path in filter(None, deleted.stdout.split("\0")):
                if not _is_case_source(source_path):
                    continue
                contents = _git_text(root, f"{revision}:{source_path}")
                if contents is not None:
                    previous.setdefault(contents, []).append(source_path)
            additions: dict[str, list[str]] = {}
            for path in added:
                try:
                    contents = (root / path).read_text(encoding="utf-8")
                except (OSError, UnicodeError) as error:
                    raise RuntimeError(f"TestCase 当前文件不可读取：{path}") from error
                additions.setdefault(contents, []).append(path)
            for contents, destinations in additions.items():
                sources = previous[contents] if contents in previous else []
                # Both ends must be unique.  A copy-to-two-files is not a move.
                original = (
                    sources[0] if len(sources) == len(destinations) == 1 else None
                )
                for path in destinations:
                    changes[path] = (original, "CURRENT")
                if original is not None:
                    del changes[original]
    return tuple(
        (path, original, state) for path, (original, state) in sorted(changes.items())
    )


def changed_case_purposes(
    root: Path, revision: str, staged: bool = False
) -> tuple[CasePurpose, ...]:
    """Compare bound Case AST identities without executing target tests.

    Args:
        root: Root of the Git repository containing Case source files.
        revision: Frozen comparison revision or commit identifier.
        staged: Whether to read the INDEX instead of the current worktree.

    Returns:
        Changed standard Case declarations and their source digests.
    """
    result: list[CasePurpose] = []
    for path, original_path, state in _changed_python_origins(root, revision, staged):
        if state == "REMOVED":
            current = ""
        else:
            current = _git_text(root, f":{path}") if staged else None
            if not staged:
                try:
                    current = (root / path).read_text(encoding="utf-8")
                except (OSError, UnicodeError) as error:
                    raise RuntimeError(f"TestCase 当前文件不可读取：{path}") from error
            if current is None:
                raise RuntimeError(f"TestCase INDEX 文件读取失败：{path}")
        before = {}
        if original_path is not None:
            historical_source = _git_text(root, f"{revision}:{original_path}")
            if historical_source is None:
                raise RuntimeError(
                    f"TestCase baseline 文件读取失败：{revision}:{original_path}"
                )
            before = _case_nodes(historical_source)
        after = _case_nodes(current)
        for name in sorted(before.keys() | after.keys()):
            prior = before[name] if name in before else None
            latest = after[name] if name in after else None
            if prior is not None and latest is not None:
                previous = (
                    prior[1] + "\0" + ast.dump(prior[0], include_attributes=False)
                )
                value = latest[1] + "\0" + ast.dump(latest[0], include_attributes=False)
                if value == previous:
                    continue
                kind = "MODIFIED"
            elif latest is not None:
                value = latest[1] + "\0" + ast.dump(latest[0], include_attributes=False)
                kind = "ADDED"
            else:
                assert prior is not None
                value = prior[1] + "\0" + ast.dump(prior[0], include_attributes=False)
                kind = "REMOVED"
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            identity = hashlib.sha256(
                f"{path}\0{name}\0{kind}\0{digest}".encode()
            ).hexdigest()[:12]
            result.append(
                CasePurpose(f"CASE-PURPOSE-{identity}", path, name, digest, kind)
            )
    return tuple(result)
