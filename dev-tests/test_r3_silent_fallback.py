"""R3: distinguish a proved silent broad exception path from a proved bug."""

from __future__ import annotations

import ast

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.silent_exception_fallback import silent_exception_fallback_findings


def _find(source: str):
    path = "pkg/owner.py"
    snapshot = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {path: PythonUnit(path, source, ast.parse(source))}
    )
    return silent_exception_fallback_findings(snapshot)


def test_broad_silent_return_and_default_parameter_are_detected():
    rows = _find(
        "def load(fetch, default=None):\n"
        "    try:\n"
        "        return fetch()\n"
        "    except Exception:\n"
        "        return default\n"
        "def query():\n"
        "    try: return unknown()\n"
        "    except BaseException: return {}\n"
    )
    assert [(row.code, row.line, row.evidence["fallback"]) for row in rows] == [
        ("QG214", 5, "parameter:default"),
        ("QG214", 8, "{}"),
    ]
    assert all(
        row.evidence["semantic_status"] == "REVIEW_NOT_PROVEN_DEFECT" for row in rows
    )


def test_observed_and_explicitly_narrow_error_handling_is_not_a_silent_fallback():
    rows = _find(
        "def read(fn):\n"
        "    try: return fn()\n"
        "    except ValueError: return None\n"
        "def guarded(fn):\n"
        "    try: return fn()\n"
        "    except Exception as exc:\n"
        "        logger.exception(str(exc))\n"
        "        return None\n"
        "def rethrow(fn):\n"
        "    try: return fn()\n"
        "    except Exception:\n"
        "        raise\n"
    )
    assert rows == ()


def test_nested_handlers_are_scoped_to_the_actual_function():
    rows = _find(
        "def outer():\n"
        "    def inner():\n"
        "        try: return bad()\n"
        "        except Exception: return False\n"
        "    return inner()\n"
    )
    # No duplicated inheritance from the lexical outer scope.
    assert rows == ()


def test_finalizer_exception_suppression_follows_cpython_protocol():
    rows = _find(
        "class Cleanup:\n"
        "    def __del__(self):\n"
        "        try: self.close()\n"
        "        except Exception: return None\n"
    )
    assert rows == ()
