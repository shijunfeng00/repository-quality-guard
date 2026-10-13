"""Behavior-family evidence must be derived from real scanner findings."""

from runtime.src.behavior_families import family_rows
from runtime.src.compact_report import _behavior_family_rows
from runtime.src.model import Finding
from types import SimpleNamespace


def _finding(code: str, path: str, line: int) -> Finding:
    return Finding(
        code=code,
        severity="warning",
        confidence="medium",
        path=path,
        line=line,
        column=1,
        symbol="sample",
        message="sample",
        suggestion="sample",
    )


def test_group_only_actual_findings_and_preserve_evidence_classes() -> None:
    entries = [
        _finding("QG003", "a.py", 4),
        _finding("QG004", "a.py", 5),
        _finding("QG157", "b.py", 10),
        _finding("QG216", "c.py", 12),
        _finding("QG214", "d.py", 14),
        _finding("QG999", "e.py", 16),
    ]
    rows = family_rows(entries)
    assert sum(row[1] for row in rows) == 5
    assert rows[0][1:6] == (3, 0, 1, 2, 3)
    assert rows[2][1:6] == (1, 1, 0, 0, 1)
    assert rows[1][1:6] == (1, 0, 1, 0, 1)
    text = "\n".join(_behavior_family_rows(SimpleNamespace(findings=entries)))
    assert "QG216@c.py:12" in text
    assert "QG999" not in text


def test_empty_families_are_stable_and_do_not_invent_findings() -> None:
    rows = family_rows([])
    assert len(rows) == 5
    assert all(row[1] == 0 and row[-1] == "—" for row in rows)
