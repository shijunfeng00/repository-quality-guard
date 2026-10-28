#!/usr/bin/env python3
"""Install the source repository's own pre-push quality gate.

This source-only installer is intentionally separate from scripts/git_hook_install.py,
which installs the Agent Skill hook into a consuming project.
"""

from __future__ import annotations

import argparse
import stat
import subprocess
import sys
from pathlib import Path

_MARKER = "# managed RQG source pre-push"
_HOOK = f"""#!/bin/sh
{_MARKER}
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 1
if [ ! -f "$repo_root/SKILL.md" ] || [ ! -f "$repo_root/maintainer/verify_release.py" ]; then
  echo "RQG source pre-push: 当前仓库不是 RQG 源码仓库，拒绝运行" >&2
  exit 1
fi
exec python "$repo_root/maintainer/source_pre_push.py"
"""


def main(argv: list[str] | None = None) -> int:
    """Install only the RQG source repository's managed pre-push hook.

    Args:
        argv: Optional command-line arguments; None reads the process arguments.

    Returns:
        Zero when the managed hook is ready, otherwise a nonzero refusal status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repo", nargs="?", type=Path, default=Path(__file__).resolve().parents[1]
    )
    root = parser.parse_args(argv).repo.resolve()
    if (
        not (root / "SKILL.md").is_file()
        or not (root / "maintainer/verify_release.py").is_file()
    ):
        print("目标不是 RQG 源码仓库；不会安装开发 hook。", file=sys.stderr)
        return 2
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-path", "hooks/pre-push"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        print("目标不是 Git 仓库；不会安装开发 hook。", file=sys.stderr)
        return 2
    hook = Path(result.stdout.strip())
    if not hook.is_absolute():
        hook = root / hook
    if hook.exists():
        current = hook.read_text(encoding="utf-8")
        if _MARKER not in current:
            print(f"已有非 RQG-source hook，保持原文件不覆盖: {hook}", file=sys.stderr)
            return 3
        if current == _HOOK:
            hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            print("RQG source pre-push hook: READY")
            return 0
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(_HOOK, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print("RQG source pre-push hook: READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
