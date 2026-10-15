"""Only a captured original class receiver authorizes nested-call owner binding."""

import ast

import pytest

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import UsageKind


@pytest.mark.parametrize("body,expected", [
    ("        def inner():\n            return self.work()\n", True),
    ("        self = Other()\n        def inner():\n            return self.work()\n", False),
    ("        def inner(self):\n            return self.work()\n", False),
])
def test_nested_receiver_binding_requires_real_uncorrupted_capture(tmp_path, body, expected):
    code = (
        "class Other:\n"
        "    def work(self):\n        return 2\n"
        "class Owner:\n"
        "    def work(self):\n        return 1\n"
        "    def outer(self):\n" + body + "        return inner\n"
    )
    snapshot = RepositoryAnalysisSnapshot(tmp_path, "DIRECTORY", {
        "sample.py": PythonUnit("sample.py", code, ast.parse(code))
    })
    edges = normalized_topology(RepositoryRelationGraph(snapshot)).edges
    hits = [edge for edge in edges if edge.kind == UsageKind.DIRECT_CALL
            and edge.source_id == "sample.Owner.outer.inner"
            and edge.target_id == "sample.Owner.work"]
    assert bool(hits) is expected
