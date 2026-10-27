"""Script/markup topology is proven only by observable source-bound edges."""
from __future__ import annotations
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from rqgtest import TestCase
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.multilang_topology import normalized_multilang_topology

class ScriptTopologyBatch32Case(TestCase):
    """Match script symbols and edges without a test-authored topology wrapper."""

    def get_enterpoint(self):
        """Return the actual normalized multi-language topology producer."""
        return normalized_multilang_topology

    def get_input_data(self):
        """Create the independent script fixture and freeze its source snapshot."""
        for case_id in ('b32-script-matching-name-does-not-prove-call', 'b32-markup-styles-have-no-callable-topology'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'multilang-script-topology.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            with TemporaryDirectory(prefix='rqg-b32-script-') as dirname:
                root = Path(dirname)
                for name, source in row['sources'].items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                snapshot = directory_analysis_snapshot(root, GuardConfig())
            yield ((snapshot,), {'script_facts': row['script_facts']})

    def get_output_data(self):
        """Read the authored symbol, visibility, ownership and edge expectations."""
        for case_id in ('b32-script-matching-name-does-not-prove-call', 'b32-markup-styles-have-no-callable-topology'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'multilang-script-topology.json').read_text(encoding='utf-8'))['cases']
            yield next((item['expected'] for item in rows if item['case_id'] == case_id))

    def equals(self, ground_truth, output):
        """Project actual public topology fields into the frozen behavior oracle."""
        observed = {'symbols': sorted((item.name for item in output.symbols)), 'visibility': {item.name: item.visibility.value for item in output.symbols}, 'call_edges': len(output.edges), 'owners': len(output.owners)}
        return observed == ground_truth
