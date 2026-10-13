"""R2/R3 findings count only when the actual sealed Skill emits them."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from runtime.src.integrity import seal_release_tree


def test_source_bound_field_order_and_silent_fallback_reach_native_report(tmp_path):
    source = Path(__file__).resolve().parents[1]
    skill = tmp_path / "skill"
    shutil.copytree(
        source,
        skill,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__"),
    )
    seal_release_tree(skill, "skill")
    target = tmp_path / "sample"
    target.mkdir()
    sample = (
        "class Store:\n"
        "    def __init__(self):\n"
        "        initial = self.value\n"
        "        self.value = initial\n"
        "\n"
        "def lookup(fetch, default=None):\n"
        "    try:\n"
        "        return fetch()\n"
        "    except Exception:\n"
        "        return default\n"
    )
    (target / "state.py").write_text(sample, encoding="utf-8")
    command = [sys.executable, str(skill / "scripts/quality_guard.py")]
    audit = subprocess.run(
        [*command, "audit", str(target)],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert audit.returncode == 3, (audit.stdout, audit.stderr)
    assert "Skill 完整性门禁：PASS" in audit.stdout
    report_path = target / "修改说明.md"
    report = report_path.read_text(encoding="utf-8")
    assert "| `QG210` |" in report
    assert "| `QG214` |" in report
    assert '"field": "value"' in report
    assert '"fallback": "parameter:default"' in report
    assert hashlib.sha256(sample.encode()).hexdigest() in report
    before = hashlib.sha256(report_path.read_bytes()).hexdigest()
    verify = subprocess.run(
        [*command, "verify", str(target)],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert verify.returncode == 3, (verify.stdout, verify.stderr)
    assert "Skill 完整性门禁：PASS" in verify.stdout
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == before
