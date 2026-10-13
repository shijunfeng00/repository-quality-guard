"""Only the sealed Skill's audit report certifies R3 suppression findings."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from runtime.src.integrity import seal_release_tree


def test_unobserved_broad_exception_is_emitted_by_official_skill(tmp_path):
    source = Path(__file__).resolve().parents[1]
    skill = tmp_path / "skill"
    shutil.copytree(
        source,
        skill,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__"),
    )
    seal_release_tree(skill, "skill")
    repo = tmp_path / "sample"
    repo.mkdir()
    code = (
        "def caller(operation):\n"
        "    try:\n"
        "        operation()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    (repo / "handler.py").write_text(code, encoding="utf8")
    launcher = [sys.executable, str(skill / "scripts" / "quality_guard.py")]
    audit = subprocess.run(
        [*launcher, "audit", str(repo)],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert audit.returncode == 3, (audit.stdout, audit.stderr)
    assert "Skill 完整性门禁：PASS" in audit.stdout
    report = repo / "修改说明.md"
    text = report.read_text(encoding="utf8")
    assert "| QG215 | `handler.py:4` |" in text
    assert "SOURCE_BOUND_BROAD_EXCEPTION_DIRECT_SUPPRESSION_OPEN_RUNTIME" in text
    assert hashlib.sha256(code.encode()).hexdigest() in text
    before = hashlib.sha256(report.read_bytes()).hexdigest()
    verify = subprocess.run(
        [*launcher, "verify", str(repo)],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert verify.returncode == 3, (verify.stdout, verify.stderr)
    assert "Skill 完整性门禁：PASS" in verify.stdout
    assert hashlib.sha256(report.read_bytes()).hexdigest() == before
