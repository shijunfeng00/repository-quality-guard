"""Required mapping fallback: independently specified positive and negative cases."""

from pathlib import Path

import pytest

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


@pytest.mark.parametrize(
    ("source", "count"),
    [
        ("def submit(payload: dict):\n    return dict(payload or {})\n", 1),
        ("def submit(value: dict[str, object]):\n    return {**(value or {})}\n", 1),
        ("def submit(payload: dict | None):\n    return dict(payload or {})\n", 0),
        ("def submit(payload: dict = None):\n    return dict(payload or {})\n", 0),
        ("def submit(payload):\n    return dict(payload or {})\n", 0),
        ("def submit(payload: dict):\n    return dict(payload)\n", 0),
        ("def submit(payload: dict):\n    return dict(payload or {'key': 1})\n", 0),
        ("class dict: pass\ndef submit(payload: dict):\n    return payload or {}\n", 0),
        ("def submit(payload: dict):\n    payload = {'x': 1}\n    return dict(payload or {})\n", 0),
    ],
)
def test_required_mapping_fallback(tmp_path: Path, source: str, count: int) -> None:
    """Requiredness, not argument name, determines the finding."""
    (tmp_path / "input.py").write_text(source, encoding="utf-8")
    scan = RepositoryScanner(tmp_path, GuardConfig()).scan()
    found = [f for f in scan.findings if f.code == "QG066"]
    assert len(found) == count
    for f in found:
        assert f.evidence['classification'] == 'PROVEN_REQUIRED_CONTRACT'
        assert f.severity == 'warning'
