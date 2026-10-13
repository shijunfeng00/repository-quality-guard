"""Review-grade smoke: only the actual sealed Skill can write and verify a QG report."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.src.integrity import seal_release_tree


@pytest.mark.skipif(shutil.which("g++") is None, reason="native C++ compiler absent")
def test_skill_cli_emits_native_qg_in_modification_report_and_verify_is_readonly(
    tmp_path: Path,
) -> None:
    """Use the actual launcher, report writer, and read-only verifier, not scan_target."""
    origin = Path(__file__).resolve().parents[1]
    staging = tmp_path / "staged-development-skill"
    staging.mkdir()
    for relative in ("runtime", "scripts", "references", "profiles"):
        shutil.copytree(origin / relative, staging / relative)
    shutil.copy2(origin / "SKILL.md", staging / "SKILL.md")
    seal_release_tree(staging, "skill")

    repository = tmp_path / "cxx-project"
    repository.mkdir()
    source = repository / "bad.cpp"
    source.write_text("int run() { return missing_cpp_identifier; }\n", encoding="utf-8")
    (repository / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(repository),
                    "file": str(source),
                    "arguments": [
                        shutil.which("g++"),
                        "-std=c++14",
                        "-c",
                        str(source),
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    command = [sys.executable, str(staging / "scripts" / "quality_guard.py")]
    audit = subprocess.run(
        [*command, "audit", str(repository)],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert "Skill 完整性门禁：PASS" in audit.stdout, audit.stderr
    assert "audit GENERATED" in audit.stdout, audit.stderr
    assert "static=REVIEW_REQUIRED" in audit.stdout, audit.stderr
    assert audit.returncode == 3, audit.stdout + audit.stderr

    report_path = repository / "修改说明.md"
    report = report_path.read_text(encoding="utf-8")
    assert "| `QG212` | `ERROR` | `bad.cpp:1` |" in report
    assert "<!-- RQG:AUTO:BEGIN native_cpp_diagnostics -->" in report
    assert "missing_cpp_identifier" in report
    assert "-std=c++14" in report
    before = hashlib.sha256(report_path.read_bytes()).hexdigest()

    verify = subprocess.run(
        [*command, "verify", str(repository)],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert "Skill 完整性门禁：PASS" in verify.stdout, verify.stderr
    assert "verify REJECT：报告契约未通过" in verify.stderr
    assert verify.returncode == 3  # Incomplete human judgement is not ACCEPT.
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == before
