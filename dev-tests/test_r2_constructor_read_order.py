"""R2 bounded constructor read-before-write facts cannot invent state errors."""

from __future__ import annotations

import ast

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.constructor_read_order import constructor_read_order_findings


def _scan(src: str):
    path = "pkg/state.py"
    snap = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {path: PythonUnit(path, src, ast.parse(src))}
    )
    return constructor_read_order_findings(snap)


def test_uninitialized_read_in_straight_line_is_visible():
    rows = _scan(
        "class Store:\n"
        "    def __init__(this):\n"
        "        previous = this.records\n"
        "        this.records = {}\n"
    )
    assert len(rows) == 1
    assert rows[0].code == "QG210"
    assert rows[0].evidence["first_write_line"] == 4
    assert rows[0].evidence["semantic_status"] == "REVIEW_NOT_PROVEN_RUNTIME_FAILURE"


def test_augmented_read_before_write_and_initialized_field():
    assert len(_scan("class S:\n def __init__(q):\n  q.count += 1\n")) == 1
    assert not _scan("class S:\n def __init__(q):\n  q.count = 1\n  x = q.count\n")


def test_class_member_decorator_branch_and_call_are_open_world():
    assert not _scan(
        "class S:\n field = 10\n def __init__(q):\n  x = q.field\n  q.field = 3\n"
    )
    assert not _scan(
        "@decorate\nclass S:\n def __init__(q):\n  x = q.field\n  q.field = 3\n"
    )
    assert not _scan(
        "class S:\n def __init__(q, flag):\n  if flag: q.field = 1\n  x = q.field\n  q.field = 3\n"
    )
    assert not _scan(
        "class S:\n def __init__(q, fn):\n  fn(q)\n  x = q.field\n  q.field = 3\n"
    )
    assert not _scan(
        "class S:\n def __getattr__(q, name): return 1\n def __init__(q):\n  x = q.field\n  q.field = 3\n"
    )
