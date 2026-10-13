"""Real Skill entrypoint and report contract for compiler-free R1/QG209."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.src.integrity import seal_release_tree


@pytest.mark.parametrize("profile_name", ["", "contract-fixture"])
def test_sealed_cli_audit_and_readonly_verify_emit_qg209(
    tmp_path: Path, profile_name: str
) -> None:
    """The rule must survive the actual bootstrap, audit and report renderer."""
    source = Path(__file__).resolve().parents[1]
    skill = tmp_path / "skill"
    shutil.copytree(
        source,
        skill,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__"),
    )
    if profile_name:
        private = skill / "profiles" / profile_name
        private.mkdir(parents=True)
        (private / "profile.json").write_text(
            json.dumps({"schema": "repository-quality-guard/profile-v1", "name": profile_name}),
            encoding="utf-8",
        )
    seal_release_tree(skill, "skill")
    target = tmp_path / (profile_name or "project")
    target.mkdir()
    (target / "producer.py").write_text(
        "def deliver(one, two):\n    return one, two\n", encoding="utf-8"
    )
    (target / "consumer.py").write_text(
        "from producer import deliver\nvalue = deliver(1)\n", encoding="utf-8"
    )
    command = [sys.executable, str(skill / "scripts/quality_guard.py")]
    audit = subprocess.run(
        [*command, "audit", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=65,
        check=False,
    )
    assert audit.returncode == 3, audit.stderr
    assert "Skill 完整性门禁：PASS" in audit.stdout
    report_path = target / "修改说明.md"
    report = report_path.read_text(encoding="utf-8")
    assert report.count("| `QG209` |") == 1
    assert "consumer.py:2" in report
    assert f"- Profile：`{profile_name or '通用'}`" in report
    assert (
        f"- Profile 来源：`{'auto-directory-name' if profile_name else 'generic'}`"
        in report
    )
    fingerprint = hashlib.sha256(report_path.read_bytes()).hexdigest()
    verify = subprocess.run(
        [*command, "verify", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=65,
        check=False,
    )
    assert verify.returncode == 3, verify.stderr
    assert "Skill 完整性门禁：PASS" in verify.stdout
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == fingerprint
