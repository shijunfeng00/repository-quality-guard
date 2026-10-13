"""The R3 witness is useful only when emitted by the sealed Skill itself."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from runtime.src.integrity import seal_release_tree


def test_r3_effect_witness_survives_official_audit_report_and_verify(tmp_path):
    source = Path(__file__).resolve().parents[1]
    skill = tmp_path / "skill"
    shutil.copytree(
        source, skill, ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__")
    )
    seal_release_tree(skill, "skill")
    target = tmp_path / "sample"
    target.mkdir()
    (target / "state.py").write_text(
        "class Store:\n"
        "    def __init__(self):\n"
        "        self.seen: set[str] = set()\n"
        "        self.cache: dict[str, object] = {}\n"
        "    def save(self, key, value):\n"
        "        try:\n"
        "            self.seen.add(key)\n"
        "            self.cache[key] = value\n"
        "            return value\n"
        "        except Exception:\n"
        "            return None\n",
        encoding="utf-8",
    )
    command = [sys.executable, str(skill / "scripts/quality_guard.py")]
    audit = subprocess.run(
        [*command, "audit", str(target)],
        capture_output=True, text=True, timeout=70, check=False,
    )
    assert audit.returncode == 3, audit.stderr
    assert "Skill 完整性门禁：PASS" in audit.stdout
    report_path = target / "修改说明.md"
    report = report_path.read_text(encoding="utf-8")
    assert "| `QG213` |" in report
    assert "`state.py:8`" in report
    assert "RQG:AUTO:BEGIN source_bound_witnesses" in report
    assert '"first_field": "seen"' in report
    assert '"second_field": "cache"' in report
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    verify = subprocess.run(
        [*command, "verify", str(target)],
        capture_output=True, text=True, timeout=70, check=False,
    )
    assert verify.returncode == 3, verify.stderr
    assert "Skill 完整性门禁：PASS" in verify.stdout
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == digest
