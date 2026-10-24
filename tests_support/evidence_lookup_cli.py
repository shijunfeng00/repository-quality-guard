"""Test support for the real evidence-lookup CLI runner."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile

def run_evidence_lookup_case(payload: dict[str, object]) -> dict[str, object]:
    """Execute the actual evidence CLI using only the case's authored inputs."""
    from scripts import evidence_lookup
    ROOT = Path(__file__).resolve().parents[1]

    with tempfile.TemporaryDirectory(prefix="rqg-evidence-ut47-") as directory:
        project = Path(directory) / "project"
        project.mkdir()
        (project / "sample.py").write_text(str(payload["source"]), encoding="utf-8")
        for name, source in dict(payload.get("extra_files", {})).items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        if payload.get("git_snapshot"):
            subprocess.run(["git", "init", "-q"], cwd=project, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=RQG Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "frozen fixture"],
                cwd=project, check=True, capture_output=True,
            )
        args = [str(project) if item == "@repo" else str(item) for item in payload["args"]]
        if payload.get("exec") == "process":
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/evidence_lookup.py"), *args],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False, timeout=40,
            )
            code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        else:
            stdout_buffer, stderr_buffer = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                try:
                    code = evidence_lookup.main(args)
                except SystemExit as exc:
                    code = int(exc.code)
            stdout, stderr = stdout_buffer.getvalue(), stderr_buffer.getvalue()
        artifacts = sorted(
            path.relative_to(project).as_posix()
            for path in project.rglob("*")
            if path.is_file() and ".git" not in path.parts
        )
        return {"code": code, "stdout": stdout, "stderr": stderr, "artifacts": artifacts}
