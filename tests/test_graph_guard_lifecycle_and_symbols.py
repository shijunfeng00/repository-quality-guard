"""Legacy B26 graph semantics through the one real normalized topology analyzer."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.relation_graph import normalized_topology
from runtime.src.topology_facts import UsageKind

class B26GuardWitnessCase(TestCase):
    """Assert original typed presence-witness boundaries for independent sources."""

    def get_enterpoint(self):
        """Return real production topology normalization without test wrappers."""
        return normalized_topology

    def get_input_data(self):
        """Provide original source-backed graph inputs for each guard scenario."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/graph-guard-lifecycle-and-symbols.json')))['cases']:
            if row['query'] == 'guard':
                _case_arg_1_row = row
                base = Path(tempfile.mkdtemp(prefix='rqg-b26-graph-'))
                atexit.register(shutil.rmtree, base, ignore_errors=True)
                root = base / 'project'
                root.mkdir()
                for relative, source in _case_arg_1_row['sources'].items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                deps = base / 'deps'
                for relative, source in _case_arg_1_row.get('stubs', {}).items():
                    path = deps / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                config = GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True, strict_get=bool(_case_arg_1_row.get('strict_get', False)), state_types=tuple(_case_arg_1_row.get('state_types', ())), tool_decorators=tuple(_case_arg_1_row.get('tool_decorators', ())), tool_registration_methods=tuple(_case_arg_1_row.get('tool_registration_methods', ())), tool_base_classes=tuple(_case_arg_1_row.get('tool_base_classes', ())))
                snapshot = directory_analysis_snapshot(root, config)
                resolver = StaticPythonDependencyResolver((deps,)) if 'stubs' in _case_arg_1_row else StaticPythonDependencyResolver.from_environment()
                _case_value_1 = ((RepositoryRelationGraph(snapshot), resolver), {})
                yield _case_value_1

    def get_output_data(self):
        """Use the original fixed positive/negative presence-witness expectations."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/graph-guard-lifecycle-and-symbols.json')))['cases']:
            if row['query'] == 'guard':
                yield (row['field'], row['expected'])

    def equals(self, expected, actual):
        """Require identical guarded field witness locations, including absence."""
        field, truth = expected
        matches = [fact.presence_witness_line for fact in actual.typed_consumers if fact.field == field]
        return (matches if matches else [0]) == truth

class B26LifecycleCase(TestCase):
    """Test original constructor lifecycle/MRO edges in both directions."""

    def get_enterpoint(self):
        """Return actual production normalized topology computation."""
        return normalized_topology

    def get_input_data(self):
        """Build authored class and method declarations as graph inputs."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/graph-guard-lifecycle-and-symbols.json')))['cases']:
            if row['query'] == 'lifecycle':
                _case_arg_2_row = row
                base = Path(tempfile.mkdtemp(prefix='rqg-b26-graph-'))
                atexit.register(shutil.rmtree, base, ignore_errors=True)
                root = base / 'project'
                root.mkdir()
                for relative, source in _case_arg_2_row['sources'].items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                deps = base / 'deps'
                for relative, source in _case_arg_2_row.get('stubs', {}).items():
                    path = deps / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                config = GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True, strict_get=bool(_case_arg_2_row.get('strict_get', False)), state_types=tuple(_case_arg_2_row.get('state_types', ())), tool_decorators=tuple(_case_arg_2_row.get('tool_decorators', ())), tool_registration_methods=tuple(_case_arg_2_row.get('tool_registration_methods', ())), tool_base_classes=tuple(_case_arg_2_row.get('tool_base_classes', ())))
                snapshot = directory_analysis_snapshot(root, config)
                resolver = StaticPythonDependencyResolver((deps,)) if 'stubs' in _case_arg_2_row else StaticPythonDependencyResolver.from_environment()
                _case_value_2 = ((RepositoryRelationGraph(snapshot), resolver), {})
                yield _case_value_2

    def get_output_data(self):
        """Preserve complete independent lifecycle and capability observations."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/graph-guard-lifecycle-and-symbols.json')))['cases']:
            if row['query'] == 'lifecycle':
                yield (row['owner'], row['expected'])

    def equals(self, expected, actual):
        """Check full lifecycle facts rather than only whether a constructor exists."""
        owner, truth = expected
        fact = next((item for item in actual.constructor_lifecycles if item.owner_symbol_id == owner), None)
        if fact is None:
            return truth is None
        observed = {'constructor': fact.constructor_symbol_id, 'mro': list(fact.mro_symbol_ids), 'super': [list(item) for item in fact.super_init_calls], 'methods': dict(fact.post_init_method_entries), 'unknown': list(fact.unknown_method_entries), 'capability': fact.capability}
        return all((observed[key] == value for key, value in truth.items()))

class B26SymbolCase(TestCase):
    """Preserve declared visibility and ownership of public graph symbols."""

    def get_enterpoint(self):
        """Return production graph normalization directly."""
        return normalized_topology

    def get_input_data(self):
        """Construct source-backed symbol declarations."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/graph-guard-lifecycle-and-symbols.json')))['cases']:
            if row['query'] == 'symbol':
                _case_arg_3_row = row
                base = Path(tempfile.mkdtemp(prefix='rqg-b26-graph-'))
                atexit.register(shutil.rmtree, base, ignore_errors=True)
                root = base / 'project'
                root.mkdir()
                for relative, source in _case_arg_3_row['sources'].items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                deps = base / 'deps'
                for relative, source in _case_arg_3_row.get('stubs', {}).items():
                    path = deps / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                config = GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True, strict_get=bool(_case_arg_3_row.get('strict_get', False)), state_types=tuple(_case_arg_3_row.get('state_types', ())), tool_decorators=tuple(_case_arg_3_row.get('tool_decorators', ())), tool_registration_methods=tuple(_case_arg_3_row.get('tool_registration_methods', ())), tool_base_classes=tuple(_case_arg_3_row.get('tool_base_classes', ())))
                snapshot = directory_analysis_snapshot(root, config)
                resolver = StaticPythonDependencyResolver((deps,)) if 'stubs' in _case_arg_3_row else StaticPythonDependencyResolver.from_environment()
                _case_value_3 = ((RepositoryRelationGraph(snapshot), resolver), {})
                yield _case_value_3

    def get_output_data(self):
        """Keep the frozen symbol visibility, kind, and owner Oracle."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/graph-guard-lifecycle-and-symbols.json')))['cases']:
            if row['query'] == 'symbol':
                yield (row['symbol'], row['expected'])

    def equals(self, expected, actual):
        """Compare all four original symbol attributes, or expected absence."""
        symbol, truth = expected
        fact = next((item for item in actual.symbols if item.symbol_id == symbol), None)
        if fact is None:
            return truth is None
        observed = {'visibility': fact.visibility.value, 'exported': fact.exported, 'kind': fact.kind, 'owner_id': fact.owner_id}
        return all((observed[key] == value for key, value in truth.items()))

class B26EdgePresenceCase(TestCase):
    """Validate authored direct/callback/static relation edge existence."""

    def get_enterpoint(self):
        """Return true public normalized relation topology."""
        return normalized_topology

    def get_input_data(self):
        """Build source-backed edge facts without executing production code."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/graph-guard-lifecycle-and-symbols.json')))['cases']:
            if row['query'] == 'edges_present':
                _case_arg_4_row = row
                base = Path(tempfile.mkdtemp(prefix='rqg-b26-graph-'))
                atexit.register(shutil.rmtree, base, ignore_errors=True)
                root = base / 'project'
                root.mkdir()
                for relative, source in _case_arg_4_row['sources'].items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                deps = base / 'deps'
                for relative, source in _case_arg_4_row.get('stubs', {}).items():
                    path = deps / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                config = GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True, strict_get=bool(_case_arg_4_row.get('strict_get', False)), state_types=tuple(_case_arg_4_row.get('state_types', ())), tool_decorators=tuple(_case_arg_4_row.get('tool_decorators', ())), tool_registration_methods=tuple(_case_arg_4_row.get('tool_registration_methods', ())), tool_base_classes=tuple(_case_arg_4_row.get('tool_base_classes', ())))
                snapshot = directory_analysis_snapshot(root, config)
                resolver = StaticPythonDependencyResolver((deps,)) if 'stubs' in _case_arg_4_row else StaticPythonDependencyResolver.from_environment()
                _case_value_4 = ((RepositoryRelationGraph(snapshot), resolver), {})
                yield _case_value_4

    def get_output_data(self):
        """Keep each expected edge-existence boolean in its original order."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/graph-guard-lifecycle-and-symbols.json')))['cases']:
            if row['query'] == 'edges_present':
                yield (row['edges'], row['expected'])

    def equals(self, expected, actual):
        """Compare all expected edge presences, including explicit non-edges."""
        wanted, truth = expected
        observed = {(edge.source_id, edge.target_id, edge.kind.value) for edge in actual.edges}
        return [tuple(item) in observed for item in wanted] == truth

class B26IncomingCase(TestCase):
    """Verify incoming edges have correct source owner, kind and source line."""

    def get_enterpoint(self):
        """Return real production topology normalization."""
        return normalized_topology

    def get_input_data(self):
        """Provide independently authored incoming-edge source examples."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/graph-guard-lifecycle-and-symbols.json')))['cases']:
            if row['query'] == 'incoming':
                _case_arg_5_row = row
                base = Path(tempfile.mkdtemp(prefix='rqg-b26-graph-'))
                atexit.register(shutil.rmtree, base, ignore_errors=True)
                root = base / 'project'
                root.mkdir()
                for relative, source in _case_arg_5_row['sources'].items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                deps = base / 'deps'
                for relative, source in _case_arg_5_row.get('stubs', {}).items():
                    path = deps / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source, encoding='utf-8')
                config = GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True, strict_get=bool(_case_arg_5_row.get('strict_get', False)), state_types=tuple(_case_arg_5_row.get('state_types', ())), tool_decorators=tuple(_case_arg_5_row.get('tool_decorators', ())), tool_registration_methods=tuple(_case_arg_5_row.get('tool_registration_methods', ())), tool_base_classes=tuple(_case_arg_5_row.get('tool_base_classes', ())))
                snapshot = directory_analysis_snapshot(root, config)
                resolver = StaticPythonDependencyResolver((deps,)) if 'stubs' in _case_arg_5_row else StaticPythonDependencyResolver.from_environment()
                _case_value_5 = ((RepositoryRelationGraph(snapshot), resolver), {})
                yield _case_value_5

    def get_output_data(self):
        """Preserve all original input target, edge-kind filter and expected edges."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/graph-guard-lifecycle-and-symbols.json')))['cases']:
            if row['query'] == 'incoming':
                yield (row['target'], tuple(row['kinds']), row['expected'])

    def equals(self, expected, actual):
        """Match every kind-and-line-qualified incoming graph edge."""
        target, kinds, truth = expected
        observed = actual.incoming(target, tuple((UsageKind(name) for name in kinds)))
        return [[item.source_id, item.kind.value, item.line] for item in observed] == truth
