"""Complementary public graph and scanner evidence regressions for B26."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.scanner import RepositoryScanner
from runtime.src.relation_graph import normalized_topology

class B26GraphFindingEdgesCase(TestCase):
    """Validate declared graph-finding relation edges against frozen edge presence."""

    def get_enterpoint(self):
        """Return the true production normalizer."""
        return normalized_topology

    def get_input_data(self):
        """Construct independently authored source program facts."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/graph-finding-edges-and-typed-contracts.json')))['cases']:
            if row['query'] == 'graph_finding':
                _case_arg_1_row = row
                _case_arg_2_row = _case_arg_1_row
                base = Path(tempfile.mkdtemp(prefix='rqg-b26-finding-'))
                atexit.register(shutil.rmtree, base, ignore_errors=True)
                root = base / 'project'
                root.mkdir()
                for name, text in _case_arg_2_row['sources'].items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                deps = base / 'deps'
                for name, text in _case_arg_2_row.get('stubs', {}).items():
                    path = deps / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                config = GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True, strict_get=bool(_case_arg_2_row.get('strict_get', False)), state_types=tuple(_case_arg_2_row.get('state_types', ())), tool_decorators=tuple(_case_arg_2_row.get('tool_decorators', ())), tool_registration_methods=tuple(_case_arg_2_row.get('tool_registration_methods', ())), tool_base_classes=tuple(_case_arg_2_row.get('tool_base_classes', ())))
                snapshot = directory_analysis_snapshot(root, config)
                resolver = StaticPythonDependencyResolver((deps,)) if _case_arg_2_row.get('stubs') else StaticPythonDependencyResolver.from_environment()
                _case_value_2 = (root, config, snapshot, resolver)
                root, config, snapshot, resolver = _case_value_2
                _case_value_1 = ((RepositoryRelationGraph(snapshot), resolver), {})
                yield _case_value_1

    def get_output_data(self):
        """Keep each declared graph edge and its independent truth value."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/graph-finding-edges-and-typed-contracts.json')))['cases']:
            if row['query'] == 'graph_finding':
                yield (row['edges'], row['expected']['edges'])

    def equals(self, expected, actual):
        """Reject missing or incorrectly inferred graph relations."""
        edges, truth = expected
        observed = {(edge.source_id, edge.target_id, edge.kind.name) for edge in actual.edges}
        return [tuple(item) in observed for item in edges] == truth

class B26GraphFindingRulesCase(TestCase):
    """Retain exact QG010/QG185 Finding identity for corresponding graph sources."""

    def get_enterpoint(self):
        """Return the true production scanner method."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build authentic input scanners from the source samples."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/graph-finding-edges-and-typed-contracts.json')))['cases']:
            if row['query'] == 'graph_finding':
                _case_arg_3_row = row
                _case_arg_4_row = _case_arg_3_row
                base = Path(tempfile.mkdtemp(prefix='rqg-b26-finding-'))
                atexit.register(shutil.rmtree, base, ignore_errors=True)
                root = base / 'project'
                root.mkdir()
                for name, text in _case_arg_4_row['sources'].items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                deps = base / 'deps'
                for name, text in _case_arg_4_row.get('stubs', {}).items():
                    path = deps / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                config = GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True, strict_get=bool(_case_arg_4_row.get('strict_get', False)), state_types=tuple(_case_arg_4_row.get('state_types', ())), tool_decorators=tuple(_case_arg_4_row.get('tool_decorators', ())), tool_registration_methods=tuple(_case_arg_4_row.get('tool_registration_methods', ())), tool_base_classes=tuple(_case_arg_4_row.get('tool_base_classes', ())))
                snapshot = directory_analysis_snapshot(root, config)
                resolver = StaticPythonDependencyResolver((deps,)) if _case_arg_4_row.get('stubs') else StaticPythonDependencyResolver.from_environment()
                _case_value_4 = (root, config, snapshot, resolver)
                root, config, snapshot, resolver = _case_value_4
                topology = normalized_topology(RepositoryRelationGraph(snapshot), resolver)
                _case_value_3 = ((RepositoryScanner(root, config, snapshot, topology),), {})
                yield _case_value_3

    def get_output_data(self):
        """Preserve every independently authored warning and critical location."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/graph-finding-edges-and-typed-contracts.json')))['cases']:
            if row['query'] == 'graph_finding':
                yield (row['rule'], row['expected']['findings'])

    def equals(self, expected, actual):
        """Check full code-specific Finding multiplicity and location."""
        code, truth = expected
        observed = [{'symbol': f.symbol, 'line': f.line, 'severity': f.severity, 'evidence': {}} for f in actual.findings if f.code == code]
        return observed == truth

class B26TypedChainFactsCase(TestCase):
    """Prove direct producer/consumer identity and shared typed schema binding."""

    def get_enterpoint(self):
        """Return genuine normalized topology."""
        return normalized_topology

    def get_input_data(self):
        """Supply separately authored typed-schema module graphs."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/graph-finding-edges-and-typed-contracts.json')))['cases']:
            if row['query'] == 'typed_chain':
                _case_arg_5_row = row
                _case_arg_6_row = _case_arg_5_row
                base = Path(tempfile.mkdtemp(prefix='rqg-b26-finding-'))
                atexit.register(shutil.rmtree, base, ignore_errors=True)
                root = base / 'project'
                root.mkdir()
                for name, text in _case_arg_6_row['sources'].items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                deps = base / 'deps'
                for name, text in _case_arg_6_row.get('stubs', {}).items():
                    path = deps / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                config = GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True, strict_get=bool(_case_arg_6_row.get('strict_get', False)), state_types=tuple(_case_arg_6_row.get('state_types', ())), tool_decorators=tuple(_case_arg_6_row.get('tool_decorators', ())), tool_registration_methods=tuple(_case_arg_6_row.get('tool_registration_methods', ())), tool_base_classes=tuple(_case_arg_6_row.get('tool_base_classes', ())))
                snapshot = directory_analysis_snapshot(root, config)
                resolver = StaticPythonDependencyResolver((deps,)) if _case_arg_6_row.get('stubs') else StaticPythonDependencyResolver.from_environment()
                _case_value_6 = (root, config, snapshot, resolver)
                root, config, snapshot, resolver = _case_value_6
                _case_value_5 = ((RepositoryRelationGraph(snapshot), resolver), {})
                yield _case_value_5

    def get_output_data(self):
        """Keep all declared source edges and shared schema IDs."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/graph-finding-edges-and-typed-contracts.json')))['cases']:
            if row['query'] == 'typed_chain':
                yield (row['edges'], row['producer_symbol'], row['consumer_symbol'], row['expected']['edges'], row['expected']['shared_schema'])

    def equals(self, expected, actual):
        """Require direct edge evidence and exact producer/consumer schema intersection."""
        edges, producer, consumer, edge_truth, schema_truth = expected
        observed = {(edge.source_id, edge.target_id, edge.kind.name) for edge in actual.edges}
        producer_schemas = {p.schema_symbol_id for p in actual.typed_producers if p.producer_symbol_id == producer}
        consumer_schemas = {c.schema_symbol_id for c in actual.typed_consumers if c.consumer_symbol_id == consumer}
        return [tuple(item) in observed for item in edges] == edge_truth and sorted(producer_schemas & consumer_schemas) == schema_truth

class B26TypedChainFindingsCase(TestCase):
    """Require associated independently expected QG003/QG216 Finding projections."""

    def get_enterpoint(self):
        """Return real production scanner API."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Provide actual scanner inputs for typed producer sources."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/graph-finding-edges-and-typed-contracts.json')))['cases']:
            if row['query'] == 'typed_chain':
                _case_arg_7_row = row
                _case_arg_8_row = _case_arg_7_row
                base = Path(tempfile.mkdtemp(prefix='rqg-b26-finding-'))
                atexit.register(shutil.rmtree, base, ignore_errors=True)
                root = base / 'project'
                root.mkdir()
                for name, text in _case_arg_8_row['sources'].items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                deps = base / 'deps'
                for name, text in _case_arg_8_row.get('stubs', {}).items():
                    path = deps / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                config = GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True, strict_get=bool(_case_arg_8_row.get('strict_get', False)), state_types=tuple(_case_arg_8_row.get('state_types', ())), tool_decorators=tuple(_case_arg_8_row.get('tool_decorators', ())), tool_registration_methods=tuple(_case_arg_8_row.get('tool_registration_methods', ())), tool_base_classes=tuple(_case_arg_8_row.get('tool_base_classes', ())))
                snapshot = directory_analysis_snapshot(root, config)
                resolver = StaticPythonDependencyResolver((deps,)) if _case_arg_8_row.get('stubs') else StaticPythonDependencyResolver.from_environment()
                _case_value_8 = (root, config, snapshot, resolver)
                root, config, snapshot, resolver = _case_value_8
                topology = normalized_topology(RepositoryRelationGraph(snapshot), resolver)
                _case_value_7 = ((RepositoryScanner(root, config, snapshot, topology),), {})
                yield _case_value_7

    def get_output_data(self):
        """Preserve original Finding codes, symbols, and shared schema sources."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/graph-finding-edges-and-typed-contracts.json')))['cases']:
            if row['query'] == 'typed_chain':
                yield (tuple(row['codes']), row['expected']['findings'])

    def equals(self, expected, actual):
        """Compare all selected source-linked code, symbol and schema witnesses."""
        codes, truth = expected
        observed = [[f.code, f.symbol, f.evidence['schema_symbol_id']] for f in actual.findings if f.code in codes]
        return observed == truth

class B26ContractFindingCase(TestCase):
    """Verify source ownership violations using the real scanner's public findings."""

    def get_enterpoint(self):
        """Return real scanner method without an adapter."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Construct original module and configured ownership rule inputs."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/graph-finding-edges-and-typed-contracts.json')))['cases']:
            if row['query'] == 'contract':
                _case_arg_9_row = row
                _case_arg_10_row = _case_arg_9_row
                base = Path(tempfile.mkdtemp(prefix='rqg-b26-finding-'))
                atexit.register(shutil.rmtree, base, ignore_errors=True)
                root = base / 'project'
                root.mkdir()
                for name, text in _case_arg_10_row['sources'].items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                deps = base / 'deps'
                for name, text in _case_arg_10_row.get('stubs', {}).items():
                    path = deps / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                config = GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True, strict_get=bool(_case_arg_10_row.get('strict_get', False)), state_types=tuple(_case_arg_10_row.get('state_types', ())), tool_decorators=tuple(_case_arg_10_row.get('tool_decorators', ())), tool_registration_methods=tuple(_case_arg_10_row.get('tool_registration_methods', ())), tool_base_classes=tuple(_case_arg_10_row.get('tool_base_classes', ())))
                snapshot = directory_analysis_snapshot(root, config)
                resolver = StaticPythonDependencyResolver((deps,)) if _case_arg_10_row.get('stubs') else StaticPythonDependencyResolver.from_environment()
                _case_value_10 = (root, config, snapshot, resolver)
                root, config, snapshot, resolver = _case_value_10
                topology = normalized_topology(RepositoryRelationGraph(snapshot), resolver)
                _case_value_9 = ((RepositoryScanner(root, config, snapshot, topology),), {})
                yield _case_value_9

    def get_output_data(self):
        """Keep original rule codes, Finding locations and optional evidence fields."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/graph-finding-edges-and-typed-contracts.json')))['cases']:
            if row['query'] == 'contract':
                yield (tuple(row['codes']), row['expected']['findings'], row['finding_details'], row['expected']['details'])

    def equals(self, expected, actual):
        """Compare all selected Findings and individually source-bound details."""
        codes, truth, details, detail_truth = expected
        observed = [[f.code, f.line, f.severity, f.evidence['contract_ownership']] for f in actual.findings if f.code in codes]
        values = [[code, line, field, next((f.evidence[field] for f in actual.findings if f.code == code and f.line == line), None)] for code, line, field in details] if details is not None else None
        return observed == truth and values == detail_truth
