"""Frozen Graph import, export and protocol-edge evidence."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology

class GraphProjectionFunctionalCase(TestCase):
    """Check published Graph edges and proven nonedges from authored sources."""

    def get_enterpoint(self):
        """Use the existing normalized graph entry point without reconstruction."""
        return normalized_topology

    def get_input_data(self):
        """Build the source snapshot and optional isolated external stubs."""
        for case_id in ('b31-graph-inheritance-and-closure', 'b31-graph-local-class-field', 'b31-graph-lexical-boundaries', 'b31-graph-unimported-stub-protocol', 'b31-graph-unimported-tensorflow-alias'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'b31-graph-projection-v1.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-b31-projection-'))
            project = root / 'project'
            project.mkdir()
            for relative, source in row['sources'].items():
                path = project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding='utf-8')
            resolver = None
            if row['stubs']:
                deps = root / 'deps'
                for relative, source in row['stubs'].items():
                    path = deps / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                resolver = StaticPythonDependencyResolver((deps,))
            snapshot = directory_analysis_snapshot(project, GuardConfig())
            graph = RepositoryRelationGraph(snapshot)
            weakref.finalize(graph, shutil.rmtree, root, ignore_errors=True)
            yield ((graph, resolver), {})

    def get_output_data(self):
        """Read the required edges, symbols and ownership facts without scanning."""
        for case_id in ('b31-graph-inheritance-and-closure', 'b31-graph-local-class-field', 'b31-graph-lexical-boundaries', 'b31-graph-unimported-stub-protocol', 'b31-graph-unimported-tensorflow-alias'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'b31-graph-projection-v1.json').read_text(encoding='utf-8'))['cases']
            yield (next((item['checks'] for item in rows if item['case_id'] == case_id)), next((item['expected'] for item in rows if item['case_id'] == case_id)))

    def equals(self, ground_truth, output):
        """Check whether each independent authored graph assertion actually holds."""
        checks, expected = ground_truth
        edges = {(edge.source_id, edge.target_id, str(edge.kind.value)) for edge in output.edges}
        symbols = {symbol.symbol_id: symbol for symbol in output.symbols}
        observed = []
        for check in checks:
            kind = check['kind']
            match kind:
                case 'edge':
                    actual = tuple(check['value']) in edges
                case 'symbol':
                    actual = check['value'] in symbols
                case 'exported':
                    actual = symbols[check['value']].exported
                case 'internal':
                    actual = symbols[check['value']].visibility.value == 'internal'
                case 'component':
                    actual = any(set(check['members']).issubset(set(component)) for component in output.state_components(check['value']))
                case _:
                    raise ValueError(f'Unknown authored Graph assertion: {kind}')
            observed.append(actual)
        return observed == expected
