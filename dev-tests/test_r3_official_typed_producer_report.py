"""A real sealed audit/verify report is the only acceptance oracle for QG216."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from runtime.src.integrity import seal_release_tree


def test_required_schema_violation_reaches_official_report(tmp_path: Path):
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
        "from typing import TypedDict\n"
        "class Item(TypedDict):\n"
        "    name: str\n"
        "    count: int\n"
        "def build(ok: bool) -> Item:\n"
        "    if ok:\n"
        "        return {'name': 'ok', 'count': 1}\n"
        "    return {'name': 'ok'}\n"
    )
    (repo / "contract.py").write_text(code, encoding="utf8")
    launcher = [sys.executable, str(skill / "scripts" / "quality_guard.py")]
    audit = subprocess.run(
        [*launcher, "audit", str(repo)], capture_output=True,
        text=True, timeout=90, check=False,
    )
    assert audit.returncode == 3, (audit.stdout, audit.stderr)
    assert "Skill 完整性门禁：PASS" in audit.stdout
    report = repo / "修改说明.md"
    content = report.read_text(encoding="utf8")
    assert "| `QG216` | `ERROR` | `contract.py:8` |" in content
    assert "| QG216 | `contract.py:8` |" in content
    assert "missing_required_fields" in content
    assert "count" in content
    assert hashlib.sha256(code.encode()).hexdigest() in content
    before = hashlib.sha256(report.read_bytes()).hexdigest()
    verify = subprocess.run(
        [*launcher, "verify", str(repo)], capture_output=True,
        text=True, timeout=90, check=False,
    )
    assert verify.returncode == 3, (verify.stdout, verify.stderr)
    assert "Skill 完整性门禁：PASS" in verify.stdout
    assert hashlib.sha256(report.read_bytes()).hexdigest() == before
