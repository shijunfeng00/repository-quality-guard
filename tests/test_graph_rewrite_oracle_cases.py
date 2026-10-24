"""Independent observable behavior against the real production scanner."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology

class GraphRewriteOracleCase(TestCase):
    """Validate frozen production Finding contracts."""

    def get_enterpoint(self):
        """Execute the repository topology normalizer directly."""
        return normalized_topology

    def get_input_data(self):
        """Build the source-bound repository graph for normalization."""
        for case_id in ('direct-receiver-dispatch-tp', 'local-binding-rename-preserves-call', 'method-reference-not-call-tn', 'unreachable-member-no-call-tn', 'callback-registration-not-dispatch', 'callback-no-registration-tn', 'distinct-target-direct-call-tp', 'distinct-target-unreferenced-tn'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'graph-rewrite-oracles-v1.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-case-'))
            (root / 'flow.py').write_text(row['source'], encoding='utf-8')
            snapshot = directory_analysis_snapshot(root, GuardConfig())
            graph = RepositoryRelationGraph(snapshot)
            weakref.finalize(graph, shutil.rmtree, root, ignore_errors=True)
            yield ((graph,), {})

    def get_output_data(self):
        """Read the independently frozen expected result."""
        for case_id in ('direct-receiver-dispatch-tp', 'local-binding-rename-preserves-call', 'method-reference-not-call-tn', 'unreachable-member-no-call-tn', 'callback-registration-not-dispatch', 'callback-no-registration-tn', 'distinct-target-direct-call-tp', 'distinct-target-unreferenced-tn'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'graph-rewrite-oracles-v1.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            yield row['expected']

    def equals(self, ground_truth, output):
        """Compare the complete normalized source edges."""
        observed = [[edge.kind.name, edge.source_id, edge.target_id, edge.path.as_posix(), edge.line] for edge in output.edges]
        return observed == ground_truth
