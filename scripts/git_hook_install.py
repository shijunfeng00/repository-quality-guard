# -*- coding: utf-8 -*-
"""显式安装 Repository Quality Guard 托管的 pre-push hook。"""

from __future__ import annotations

import argparse
import stat
import subprocess
import sys
from pathlib import Path

_HOOK_MARKER = "# managed Repository Quality Guard pre-push"
_HOOK_TEXT = f"""#!/bin/sh
{_HOOK_MARKER}

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 1
qg="$repo_root/.agents/skills/repository-quality-guard/scripts/quality_guard.py"
if [ ! -f "$qg" ]; then
  echo "pre-push: Repository Quality Guard 运行时缺失，拒绝 push" >&2
  exit 1
fi

head_sha=$(git rev-parse HEAD 2>/dev/null) || exit 1
if ! git diff --quiet -- || ! git diff --cached --quiet --; then
  echo "pre-push: 存在未提交 tracked 修改；无法证明 push commit 与修改说明.md 对应，拒绝 push" >&2
  exit 1
fi

zero=0000000000000000000000000000000000000000
while read -r local_ref local_sha remote_ref remote_sha; do
  [ -n "$local_sha" ] || continue
  [ "$local_sha" != "$zero" ] || continue
  if [ "$local_sha" != "$head_sha" ]; then
    echo "pre-push: 当前托管 hook 只验证 HEAD；$local_ref 不是 HEAD，拒绝 push" >&2
    exit 1
  fi

  if [ "$remote_sha" != "$zero" ]; then
    base=$(git merge-base "$local_sha" "$remote_sha" 2>/dev/null)
  elif git rev-parse --verify refs/remotes/origin/main >/dev/null 2>&1; then
    base=$(git merge-base "$local_sha" refs/remotes/origin/main 2>/dev/null)
  else
    base=$(git rev-list --max-parents=0 "$local_sha" | head -n 1)
  fi
  if [ -z "$base" ]; then
    echo "pre-push: 无法确定质量审计 diff-base，拒绝 push: $local_ref -> $remote_ref" >&2
    exit 1
  fi

  echo "pre-push: QG verify + 修改说明.md validator (diff-base=$base)" >&2
  python "$qg" verify "$repo_root" --diff-base "$base"
  qg_status=$?
  case "$qg_status" in
    0)
      ;;
    5)
      echo "pre-push: QG=REVIEW_REQUIRED；报告契约完整且未 REJECT，继续。" >&2
      ;;
    *)
      echo "pre-push: QG 未通过（exit=$qg_status），拒绝 push。" >&2
      exit "$qg_status"
      ;;
  esac
done
"""


def main() -> int:
    """安装当前版本的托管 pre-push hook。\n\n    Returns:\n        安装成功返回 0；目标无效或已有非托管 hook 时返回 1。\n"""
    default_repo = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="?", type=Path, default=default_repo)
    args = parser.parse_args()
    root = args.repo.resolve()
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-path", "hooks/pre-push"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
    )
    if result.returncode != 0:
        print("目标不是 Git 仓库，未安装 pre-push。", file=sys.stderr)
        return 1
    hook_path = Path(result.stdout.strip())
    if not hook_path.is_absolute():
        hook_path = root / hook_path
    hook_path = hook_path.resolve()
    if hook_path.exists():
        current = hook_path.read_text(encoding="utf-8")
        if _HOOK_MARKER not in current:
            print(
                f"已有非托管 pre-push，保持原文件不覆盖: {hook_path}", file=sys.stderr
            )
            return 1
        if current == _HOOK_TEXT:
            print("Repository Quality Guard pre-push hook: READY")
            return 0
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(_HOOK_TEXT, encoding="utf-8")
    hook_path.chmod(
        hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    print("Repository Quality Guard pre-push hook: READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
