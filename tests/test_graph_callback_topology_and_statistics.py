"""Source-bound nested receiver identity is not inferred from a shadowed local."""
from __future__ import annotations
import ast
import json
import tempfile
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import UsageKind
from runtime.src.config import GuardConfig

class GraphBehaviorBatch32Case(TestCase):
    """Check that nested receiver rebinding changes the real direct-call graph."""

    def get_enterpoint(self):
        """Call the production normalized topology provider."""
        return normalized_topology

    def get_input_data(self):
        """Build immutable source units from the independently authored fixture."""
        for case_id in ('b32-graph-nested-captured', 'b32-graph-nested-rebound', 'b32-graph-nested-shadowed'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'graph-callback-topology-and-statistics.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            sources = {path: PythonUnit(path, source, ast.parse(source)) for path, source in row['sources'].items()}
            snapshot = RepositoryAnalysisSnapshot(None, 'DIRECTORY', sources)
            yield ((RepositoryRelationGraph(snapshot),), {})

    def get_output_data(self):
        """Read the independent expected graph edge polarity."""
        for case_id in ('b32-graph-nested-captured', 'b32-graph-nested-rebound', 'b32-graph-nested-shadowed'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'graph-callback-topology-and-statistics.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            yield row['expected']['captured_owner_direct_call']

    def equals(self, ground_truth, output):
        """Compare only the public, normalized direct-call edge relation."""
        direct = any((edge.kind is UsageKind.DIRECT_CALL and edge.source_id == 'sample.Owner.outer.inner' and (edge.target_id == 'sample.Owner.work') for edge in output.edges))
        return direct is ground_truth


class GraphCallbackTopologyBatch32Case(TestCase):
    """Assert callback registration is not fabricated as a direct call."""

    def get_enterpoint(self):
        return normalized_topology

    def get_input_data(self):
        fixture = json.loads((Path(__file__).parent / 'cases' / 'graph-callback-topology-and-statistics.json').read_text(encoding='utf-8'))
        row = next(item for item in fixture['cases'] if item['scenario'] == 'callback')
        sources = {path: PythonUnit(path, source, ast.parse(source)) for path, source in row['sources'].items()}
        snapshot = RepositoryAnalysisSnapshot(None, 'DIRECTORY', sources)
        yield ((RepositoryRelationGraph(snapshot),), {})

    def get_output_data(self):
        fixture = json.loads((Path(__file__).parent / 'cases' / 'graph-callback-topology-and-statistics.json').read_text(encoding='utf-8'))
        row = next(item for item in fixture['cases'] if item['scenario'] == 'callback')
        yield row['expected']

    def equals(self, expected, output):
        edges = [edge for edge in output.edges if edge.source_id == 'workflow.Worker.configure' and edge.target_id == 'workflow.Worker.task']
        return (
            any(edge.kind is UsageKind.CALLBACK_REGISTRATION for edge in edges) is expected['callback_edge']
            and all(edge.kind is not UsageKind.DIRECT_CALL for edge in edges) is expected['direct_call_not_fabricated']
        )


class GraphCallbackStatisticsBatch32Case(TestCase):
    """Keep callback registration and component statistics as a normal Case."""

    def get_enterpoint(self):
        from scripts.evidence_statistics import graph_statistics
        return graph_statistics

    def get_input_data(self):
        from scripts.evidence_graph import _serialize
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        fixture = json.loads((Path(__file__).parent / 'cases' / 'graph-callback-topology-and-statistics.json').read_text(encoding='utf-8'))
        row = next(item for item in fixture['cases'] if item['scenario'] == 'callback')
        with tempfile.TemporaryDirectory(prefix='rqg-graph-callback-stats-') as directory:
            root = Path(directory)
            for name, content in row['sources'].items():
                (root / name).write_text(content, encoding='utf-8')
            snapshot = directory_analysis_snapshot(root, GuardConfig())
            topology = normalized_topology(RepositoryRelationGraph(snapshot))
            yield ((_serialize(topology),), {})

    def get_output_data(self):
        fixture = json.loads((Path(__file__).parent / 'cases' / 'graph-callback-topology-and-statistics.json').read_text(encoding='utf-8'))
        row = next(item for item in fixture['cases'] if item['scenario'] == 'callback')
        yield row['expected']

    def equals(self, expected, actual):
        return (
            ('注册边 1' in actual) is expected['one_registration']
            and ('弱连通分量：3' in actual) is expected['three_components']
            and ('弱连通分量 2' in actual) is expected['second_component']
        )


class GraphCallbackTraceBatch32Case(TestCase):
    """Preserve the rendered callback evidence and its explicit limitations."""

    def get_enterpoint(self):
        from scripts.evidence_graph import render
        return render

    def get_input_data(self):
        from scripts.evidence_graph import TraceOptions, _serialize
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        fixture = json.loads((Path(__file__).parent / 'cases' / 'graph-callback-topology-and-statistics.json').read_text(encoding='utf-8'))
        row = next(item for item in fixture['cases'] if item['scenario'] == 'callback')
        with tempfile.TemporaryDirectory(prefix='rqg-graph-callback-trace-') as directory:
            root = Path(directory)
            for name, content in row['sources'].items():
                (root / name).write_text(content, encoding='utf-8')
            snapshot = directory_analysis_snapshot(root, GuardConfig())
            topology = normalized_topology(RepositoryRelationGraph(snapshot))
            graph = _serialize(topology)
            anchor = next(symbol for symbol in graph['symbols'] if symbol['id'] == 'workflow.Worker.task')
            options = TraceOptions(direction='callers', depth=2, budget=8, radius=1, variable=None, line=None, cache_info='test')
            yield ((snapshot, graph, anchor, options), {})

    def get_output_data(self):
        fixture = json.loads((Path(__file__).parent / 'cases' / 'graph-callback-topology-and-statistics.json').read_text(encoding='utf-8'))
        row = next(item for item in fixture['cases'] if item['scenario'] == 'callback')
        yield row['expected']

    def equals(self, expected, actual):
        return (
            ('[CALLBACK_ARGUMENT]' in actual) is expected['traced_callback']
            and ("graph.add_node('step', self.task)" in actual) is expected['exact_call_site']
            and ('不证明实际调度' in actual) is expected['not_actual_dispatch']
            and ('[DIRECT_CALL, resolved]' not in actual) is expected['no_fake_resolved_direct_call']
        )
