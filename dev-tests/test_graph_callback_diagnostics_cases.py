"""Syntactic callback connections must not become executable call edges."""

import ast
from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import UsageKind
from scripts.evidence_graph import _serialize, render, TraceOptions
from scripts.evidence_statistics import graph_statistics


def test_explicit_callback_argument_shown_separately(tmp_path):
    """A registered method is discoverable without an invented direct dispatch."""
    source = (
        "class Worker:\n"
        "    def task(self, state):\n"
        "        return state\n"
        "    def configure(self, graph):\n"
        "        graph.add_node('step', self.task)\n"
        "    def unrelated(self):\n"
        "        return True\n"
    )
    snapshot = RepositoryAnalysisSnapshot(
        tmp_path, 'DIRECTORY',
        {'workflow.py': PythonUnit('workflow.py', source, ast.parse(source))},
    )
    topology = normalized_topology(RepositoryRelationGraph(snapshot))
    edges = [e for e in topology.edges if e.source_id == 'workflow.Worker.configure' and e.target_id == 'workflow.Worker.task']
    assert any(e.kind == UsageKind.CALLBACK_REGISTRATION for e in edges)
    assert all(e.kind != UsageKind.DIRECT_CALL for e in edges)
    graph = _serialize(topology)
    stats = graph_statistics(graph)
    assert '注册边 1' in stats
    assert '弱连通分量：3' in stats
    assert '弱连通分量 2' in stats
    anchor = next(s for s in graph['symbols'] if s['id'] == 'workflow.Worker.task')
    output = render(snapshot, graph, anchor, TraceOptions(direction='callers', depth=2, budget=8, radius=1, variable=None, line=None, cache_info='test'))
    assert '[CALLBACK_ARGUMENT]' in output
    assert "graph.add_node('step', self.task)" in output
    assert '不证明实际调度' in output
    assert '[DIRECT_CALL, resolved]' not in output
