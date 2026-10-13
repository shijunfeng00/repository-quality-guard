"""Regression: obsolete QG177 must not block contract-correct repairs."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from runtime.src.integrity import seal_release_tree


OLD = 'def helper(hits):\n    output = []\n    for rank, hit in enumerate(hits):\n        if "_id" in hit:\n            key = hit["_id"]\n        else:\n            key = f"temp:{rank}"\n        output.append(key)\n    return output\n\ndef fuse(hits):\n    keys = []\n    for hit in hits:\n        key = str(hit.get("_id") or "")\n        if not key:\n            key = "temporary"\n        keys.append(key)\n    return helper(hits), keys\n'
NEW = 'def helper(hits):\n    output = []\n    for rank, hit in enumerate(hits):\n        if "_id" in hit:\n            key = hit["_id"]\n        else:\n            key = f"temp:{rank}"\n        output.append(key)\n    return output\n\ndef fuse(hits):\n    keys = []\n    for hit in hits:\n        key = hit["_id"]\n        keys.append(key)\n    return helper(hits), keys\n'


def test_correct_direct_lookup_does_not_trigger_retired_qg177(tmp_path: Path) -> None:
    """Official audit/verify must not convert historical helper into new QG177."""
    origin = Path(__file__).resolve().parents[1]
    staging = tmp_path / "sealed-skill"
    staging.mkdir()
    for relative in ("runtime", "scripts", "references", "profiles"):
        shutil.copytree(origin / relative, staging / relative)
    shutil.copy2(origin / "SKILL.md", staging / "SKILL.md")
    seal_release_tree(staging, "skill")
    repo = tmp_path / "test-repository"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    file = repo / "fuse.py"
    file.write_text(OLD, encoding="utf-8")
    git("add", "fuse.py")
    git("commit", "-qm", "old baseline")
    file.write_text(NEW, encoding="utf-8")
    cmd = [sys.executable, str(staging / "scripts/quality_guard.py")]
    audit = subprocess.run(
        [*cmd, "audit", str(repo)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert "Skill 完整性门禁：PASS" in audit.stdout, audit.stdout + audit.stderr
    assert "audit GENERATED" in audit.stdout, audit.stdout + audit.stderr
    report = repo / "修改说明.md"
    content = report.read_text(encoding="utf-8")
    assert "| `QG177` |" not in content
    assert "QG177" not in audit.stdout + audit.stderr
    prior = hashlib.sha256(report.read_bytes()).digest()
    verify = subprocess.run(
        [*cmd, "verify", str(repo)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert "Skill 完整性门禁：PASS" in verify.stdout, verify.stdout + verify.stderr
    assert prior == hashlib.sha256(report.read_bytes()).digest()
    # Human decisions remain required; retiring a broken detector never grants ACCEPT.
    assert verify.returncode == 3


def test_retirement_does_not_disable_real_qg179_regression_gate(tmp_path: Path) -> None:
    """Retiring one defective detector must not weaken ordinary issue-delta gates."""
    from runtime.src.cli import _append_quality_count_regression_finding
    from runtime.src.gate_status import code_status
    from runtime.src.model import Finding, QualityBaseline, ScanReport

    bad = Finding(
        code="QG007",
        severity="warning",
        confidence="high",
        path="service.py",
        line=2,
        column=1,
        message="Unrelated, truly introduced warning",
    )
    prior = QualityBaseline(
        revision="original-git-base",
        files_scanned=1,
        definitions=1,
        documented_definitions=0,
        complete_docstrings=0,
        docstring_definitions=1,
        summary={"critical": 0, "error": 0, "warning": 0, "info": 0},
        by_rule={},
        by_file={},
        finding_severities={},
    )
    report = ScanReport(
        root=tmp_path,
        files_scanned=1,
        findings=[bad],
        definitions=1,
        baseline=prior,
    )
    _append_quality_count_regression_finding(report)
    assert any(f.code == "QG179" for f in report.findings)
    assert code_status(report) == "REJECT"
