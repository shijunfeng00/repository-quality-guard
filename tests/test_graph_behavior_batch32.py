"""Source-bound nested receiver identity is not inferred from a shadowed local."""
from __future__ import annotations
import ast
import json
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import UsageKind

class GraphBehaviorBatch32Case(TestCase):
    """Check that nested receiver rebinding changes the real direct-call graph."""

    def get_enterpoint(self):
        """Call the production normalized topology provider."""
        return normalized_topology

    def get_input_data(self):
        """Build immutable source units from the independently authored fixture."""
        for case_id in ('b32-graph-nested-captured', 'b32-graph-nested-rebound', 'b32-graph-nested-shadowed'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'b32-graph-observable-v1.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            sources = {path: PythonUnit(path, source, ast.parse(source)) for path, source in row['sources'].items()}
            snapshot = RepositoryAnalysisSnapshot(None, 'DIRECTORY', sources)
            yield ((RepositoryRelationGraph(snapshot),), {})

    def get_output_data(self):
        """Read the independent expected graph edge polarity."""
        for case_id in ('b32-graph-nested-captured', 'b32-graph-nested-rebound', 'b32-graph-nested-shadowed'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'b32-graph-observable-v1.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            yield row['expected']['captured_owner_direct_call']

    def equals(self, ground_truth, output):
        """Compare only the public, normalized direct-call edge relation."""
        direct = any((edge.kind is UsageKind.DIRECT_CALL and edge.source_id == 'sample.Owner.outer.inner' and (edge.target_id == 'sample.Owner.work') for edge in output.edges))
        return direct is ground_truth
