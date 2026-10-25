#!/usr/bin/env python3
"""Run RQG's own source-repository pre-push audit; not the host Skill installer."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def main() -> int:
    """Verify the pushed RQG source HEAD against its remote merge base.

    Returns:
        Zero for ACCEPT/REVIEW_REQUIRED; a nonzero gate status blocks the push.
    """
    root = Path(__file__).resolve().parents[1]
    if not (root / ".git").exists() or not (root / "runtime/RELEASE.lock").is_file():
        print(
            "RQG source pre-push: source root or Git metadata missing", file=sys.stderr
        )
        return 2
    if (
        subprocess.run(
            ["git", "diff", "--quiet", "--"], cwd=root, check=False
        ).returncode
        or subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--"], cwd=root, check=False
        ).returncode
    ):
        print(
            "RQG source pre-push: tracked worktree/index must be clean", file=sys.stderr
        )
        return 2
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if head.returncode:
        return head.returncode
    zero = "0" * 40
    for line in sys.stdin:
        fields = line.split()
        if len(fields) != 4:
            print("RQG source pre-push: malformed ref input", file=sys.stderr)
            return 2
        local_ref, local_sha, remote_ref, remote_sha = fields
        if local_sha == zero:
            continue
        if local_sha != head.stdout.strip():
            print(
                f"RQG source pre-push: only HEAD is verified ({local_ref})",
                file=sys.stderr,
            )
            return 2
        if remote_sha != zero:
            base_cmd = ["git", "merge-base", local_sha, remote_sha]
        elif (
            subprocess.run(
                ["git", "rev-parse", "--verify", "refs/remotes/origin/main"],
                cwd=root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        ):
            base_cmd = ["git", "merge-base", local_sha, "refs/remotes/origin/main"]
        else:
            base_cmd = ["git", "rev-list", "--max-parents=0", local_sha]
        base = subprocess.run(
            base_cmd, cwd=root, text=True, capture_output=True, check=False
        )
        if base.returncode or not base.stdout.strip():
            print("RQG source pre-push: cannot resolve audit base", file=sys.stderr)
            return 2
        diff_base = base.stdout.strip().splitlines()[0]
        command = [
            sys.executable,
            str(root / "scripts/quality_guard.py"),
            "verify",
            str(root),
            "--diff-base",
            diff_base,
        ]
        code = subprocess.run(command, cwd=root, check=False).returncode
        if code not in (0, 5):
            print(
                f"RQG source pre-push: quality gate rejected push (exit={code})",
                file=sys.stderr,
            )
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
