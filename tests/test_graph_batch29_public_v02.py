"""Public Graph normalized topology observes inherited constructors and typed consumers."""
from __future__ import annotations
from rqgtest import TestCase
from runtime.src.relation_graph import normalized_topology

class GraphBatch29PublicCase(TestCase):
    """Match independent constructor and TypedDict evidence across real source modules."""

    def get_enterpoint(self):
        """Return the actual normalized topology production API."""
        return normalized_topology

    def get_input_data(self):
        """Build actual repository graph inputs from the original source fixtures."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph
        _DATA = Path(__file__).resolve().parents[1] / 'tests/cases/b29-graph-public-v02.json'
        _case_value_1 = json.loads(_DATA.read_text(encoding='utf-8'))['cases']
        for row in _case_value_1:
            _case_arg_2_row = row
            root = Path(tempfile.mkdtemp(prefix='rqg-b29-v02-'))
            atexit.register(shutil.rmtree, root, ignore_errors=True)
            for relative, source in _case_arg_2_row['sources'].items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
            snapshot = directory_analysis_snapshot(root, GuardConfig(include_tests=True))
            _case_value_2 = ((RepositoryRelationGraph(snapshot),), {})
            yield _case_value_2

    def get_output_data(self):
        """Preserve the original authored query discriminant and expected facts."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph
        _DATA = Path(__file__).resolve().parents[1] / 'tests/cases/b29-graph-public-v02.json'
        _case_value_3 = json.loads(_DATA.read_text(encoding='utf-8'))['cases']
        for row in _case_value_3:
            yield (row['query'], row['expected'])

    def equals(self, expected, actual):
        """Project public topology facts and require exact original expectations."""
        query, truth = expected
        if query == 'constructor_chain':
            facts = [f for f in actual.constructor_lifecycles if f.owner_symbol_id == 'pkg.classes.Child']
            return len(facts) == 1 and truth == {'mro': list(facts[0].mro_symbol_ids), 'constructor': facts[0].constructor_symbol_id, 'super_calls': [list(x) for x in facts[0].super_init_calls], 'capability': facts[0].capability}
        if query == 'inherited_constructor':
            return truth == [{'owner': f.owner_symbol_id, 'constructor': f.constructor_symbol_id, 'mro': list(f.mro_symbol_ids)} for f in actual.constructor_lifecycles if f.owner_symbol_id == 'pkg.classes.Deriv']
        if query == 'dynamic_unknown':
            return truth == [{'owner': f.owner_symbol_id, 'unknown': f.capability == 'UNKNOWN_DYNAMIC_OR_UNBOUND_HIERARCHY', 'no_proven_constructor': not bool(f.constructor_symbol_id), 'no_proven_mro': not bool(f.mro_symbol_ids)} for f in actual.constructor_lifecycles if f.owner_symbol_id != 'pkg.classes.Good']
        if query == 'method_entries':
            return truth == [{'owner': f.owner_symbol_id, 'constructor': f.constructor_symbol_id, 'entries': [list(x) for x in f.post_init_method_entries], 'unknown': list(f.unknown_method_entries), 'capability': f.capability} for f in actual.constructor_lifecycles if f.owner_symbol_id.endswith(('.Child', '.B', '.Z'))]
        if query == 'guard_consumer':
            return truth == [{'field': f.field, 'required': f.required, 'guard_line': f.presence_witness_line} for f in actual.typed_consumers]
        if query == 'typed_consumer':
            return truth == [{'schema': f.schema_symbol_id, 'field': f.field, 'required': f.required, 'consumer': f.consumer_symbol_id, 'guard': f.guard_observation} for f in actual.typed_consumers]
        return False
