"""QG209 source binding must be visible without enabling a compiler provider."""

from __future__ import annotations

import ast

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.source_call_contracts import source_bound_call_findings


def _find(sources: dict[str, str]):
    snapshot = RepositoryAnalysisSnapshot(
        None,
        "DIRECTORY",
        {path: PythonUnit(path, src, ast.parse(src)) for path, src in sources.items()},
    )
    return source_bound_call_findings(snapshot)


def test_unique_module_function_without_optional_compiler():
    cases = {
        "writer.py": "def produce(first, second, *, enabled):\n    return first\n",
        "reader.py": (
            "from writer import produce as deliver\n"
            "import writer as module\n"
            "one = deliver(1, 2)\n"
            "two = module.produce(1, 2, enabled=True)\n"
        ),
    }
    findings = _find(cases)
    assert len(findings) == 1
    assert findings[0].code == "QG209"
    assert findings[0].path == "reader.py" and findings[0].line == 3
    assert findings[0].evidence["callee_path"] == "writer.py"
    assert findings[0].evidence["dispatch"] == "OPEN_WORLD"


def test_direct_local_missing_required_argument_and_default_positive():
    source = "def merge(left, right, tail=0):\n    return left\nok = merge(1, 2)\nfail = merge(1)\n"
    findings = _find({"module.py": source})
    assert len(findings) == 1 and findings[0].line == 4


def test_decorator_rebinding_shadow_and_dynamic_star_remain_unknown():
    cases = {
        "module.py": (
            "def other(value):\n    return value\n"
            "@other\n"
            "def wrapped(left, right):\n    return left\n"
            "wrapped(1)\n"
            "def rebound(left, right):\n    return left\n"
            "rebound = other\n"
            "rebound(1)\n"
            "def spread(left, right):\n    return left\n"
            "spread(*[1])\n"
        )
    }
    assert _find(cases) == ()


def test_import_shadowed_inside_nested_function_not_certified():
    sources = {
        "supplier.py": "def deliver(one, two):\n    return one\n",
        "other.py": "def deliver(one):\n    return one\n",
        "consumer.py": (
            "from supplier import deliver\n"
            "def work():\n"
            "    from other import deliver\n"
            "    return deliver(1)\n"
        ),
    }
    assert _find(sources) == ()


def test_no_source_proof_from_kwargs_or_methods():
    source = (
        "def send(left, right):\n    return left\n"
        "send(*(1,))\n"
        "send(**{'left': 1})\n"
        "class Sender:\n"
        "    def send(self, left, right): return left\n"
        "sender = Sender()\n"
        "sender.send(1)\n"
    )
    assert _find({"module.py": source}) == ()
