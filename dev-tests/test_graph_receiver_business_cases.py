"""Observable graph cases: target existence, caller path and counterexamples."""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import UsageKind


SOURCES = {
    "domain.py": """class Ledger:
    def record(self, event):
        return event

class Impostor:
    def record(self, event):
        return "WRONG"
""",
    "service.py": """from domain import Ledger as Store, Impostor

class Handler:
    def __init__(self):
        self.store = Store()

    def submit(self, event):
        return self.store.record(event)

def direct(entry: Store):
    return entry.record("ok")

def build():
    ledger = Store()
    return ledger.record("ok")

def inline():
    return Store().record("ok")

def pass_through(entry: Store):
    return direct(entry)

def unknown(x):
    return x.record("unknown")

def overridden(entry: Store):
    entry = Impostor()
    return entry.record("wrong")

def conditional(entry: Store, flag):
    if flag:
        entry = Impostor()
    return entry.record("either")

def nested(entry: Store):
    def helper():
        return entry.record("nested")
    return helper()
""",
}


def _graph():
    snapshot = RepositoryAnalysisSnapshot(
        None,
        "DIRECTORY",
        {name: PythonUnit(name, src, ast.parse(src)) for name, src in SOURCES.items()},
    )
    return normalized_topology(RepositoryRelationGraph(snapshot))


def test_source_bound_possible_call_edges_match_business_oracle():
    graph = _graph()
    actual = {
        (e.source_id, e.target_id)
        for e in graph.edges
        if e.kind == UsageKind.MAY_DISPATCH
    }
    correct = {
        ("service.Handler.submit", "domain.Ledger.record"),
        ("service.direct", "domain.Ledger.record"),
        ("service.build", "domain.Ledger.record"),
        ("service.inline", "domain.Ledger.record"),
    }
    assert correct <= actual
    assert ("service.unknown", "domain.Ledger.record") not in actual
    assert ("service.unknown", "domain.Impostor.record") not in actual
    assert ("service.overridden", "domain.Ledger.record") not in actual
    assert ("service.conditional", "domain.Ledger.record") not in actual
    assert ("service.nested", "domain.Ledger.record") not in actual


def test_multistep_call_path_and_source_line_are_available():
    graph = _graph()
    from_callers = graph.incoming("domain.Ledger.record", (UsageKind.MAY_DISPATCH,))
    sources = {edge.source_id: edge for edge in from_callers}
    assert "service.Handler.submit" in sources
    assert sources["service.Handler.submit"].path == Path("service.py")
    assert sources["service.Handler.submit"].line == 8
    direct_callers = graph.incoming("service.direct", (UsageKind.DIRECT_CALL,))
    assert any(edge.source_id == "service.pass_through" for edge in direct_callers)
    # Reverse A→B→C without rewriting business code or inventing runtime stack.
    assert any(edge.source_id == "service.pass_through" for edge in direct_callers)
    assert any(edge.source_id == "service.direct" for edge in from_callers)


def test_source_package_root_from_real_import_identity_not_basename_guess():
    """Resolve authored package roots and refuse duplicate plausible owners."""
    source = {
        "sources/domain.py": "class Ledger:\n    def commit(self): return 1\n",
        "task.py": "from domain import Ledger\ndef entry():\n    return Ledger().commit()\n",
    }
    snapshot = RepositoryAnalysisSnapshot(
        None,
        "DIRECTORY",
        {
            path: PythonUnit(path, text, ast.parse(text))
            for path, text in source.items()
        },
    )
    graph = normalized_topology(RepositoryRelationGraph(snapshot))
    actual = {
        (x.source_id, x.target_id)
        for x in graph.edges
        if x.kind == UsageKind.MAY_DISPATCH
    }
    assert ("task.entry", "sources.domain.Ledger.commit") in actual
    # Two potentially importable roots cannot be resolved by search ranking.
    source["src/domain.py"] = source["sources/domain.py"]
    snapshot = RepositoryAnalysisSnapshot(
        None,
        "DIRECTORY",
        {
            path: PythonUnit(path, text, ast.parse(text))
            for path, text in source.items()
        },
    )
    graph = normalized_topology(RepositoryRelationGraph(snapshot))
    actual = {
        (x.source_id, x.target_id)
        for x in graph.edges
        if x.kind == UsageKind.MAY_DISPATCH
    }
    assert not any(source == "task.entry" for source, _ in actual)
