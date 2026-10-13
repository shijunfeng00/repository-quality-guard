"""R3 source-bound swallowed error paths, with anti-inflation counterexamples."""

import ast

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.silent_handler_fallthrough import silent_handler_fallthrough_findings


def _scan(source):
    path = "owner.py"
    snapshot = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {path: PythonUnit(path, source, ast.parse(source))}
    )
    return silent_handler_fallthrough_findings(snapshot)


def test_pass_continue_and_sentinel_assignment_are_source_bound():
    src = (
        "def update(fetch):\n"
        "    try: fetch()\n"
        "    except Exception: pass\n"
        "def parse(fetch):\n"
        "    try: result = fetch()\n"
        "    except BaseException: result = None\n"
        "    return result\n"
        "def loop(fetch):\n"
        "    for item in [1]:\n"
        "        try: fetch(item)\n"
        "        except: continue\n"
    )
    rows = _scan(src)
    assert [(row.code, row.evidence["suppression"]) for row in rows] == [
        ("QG215", "pass"),
        ("QG215", "assign_sentinel"),
        ("QG215", "continue"),
    ]
    assert all(r.evidence["semantic_status"] == "REVIEW_NOT_PROVEN_DEFECT" for r in rows)


def test_observable_handling_and_nested_scopes_do_not_create_false_findings():
    src = (
        "def observed(fn):\n"
        "    try: fn()\n"
        "    except Exception as exc: logger.warning(str(exc))\n"
        "def reraised(fn):\n"
        "    try: fn()\n"
        "    except Exception: raise\n"
        "def narrow(fn):\n"
        "    try: fn()\n"
        "    except ValueError: pass\n"
        "def outer():\n"
        "    def inner():\n"
        "        try: broken()\n"
        "        except Exception: pass\n"
        "    return inner()\n"
        "class Cleanup:\n"
        "    def __del__(self):\n"
        "        try: self.close()\n"
        "        except Exception: pass\n"
    )
    assert _scan(src) == ()


def test_assigning_explicit_failure_state_is_not_categorically_a_bug():
    rows = _scan(
        "def validate(fn):\n"
        "    verified = True\n"
        "    try: fn()\n"
        "    except Exception: verified = False\n"
        "    return verified\n"
    )
    assert len(rows) == 1
    assert rows[0].severity == "warning"
    assert rows[0].evidence["local"] == "verified"
