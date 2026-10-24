"""Source-bound field access, schema and argument forwarding graph contracts."""
from __future__ import annotations
from rqgtest import JSONDataLoader
import hashlib
from rqgtest import TestCase
from runtime.src.relation_graph import normalized_topology

class FieldAccessTopologyCase(TestCase):
    """Check test_r2_field_access with original positive and negative graph facts."""

    def get_enterpoint(self):
        """Return the real production normalized graph function directly."""
        return normalized_topology

    def get_input_data(self):
        """Provide syntactically valid source graph inputs without invoking target."""
        import ast
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        _ENGINE_PATHS = {'test_r2_field_access': 'pkg/example.py', 'test_r3_schema_provenance': 'pkg/contracts.py', 'test_r3_parameter_forwarding': 'chain.py'}
        for row in next(iter(JSONDataLoader('tests/cases/semantic-topology-public-v02.json')))['cases']:
            if row['engine'] == 'test_r2_field_access':
                _case_arg_1_row = row
                source = _case_arg_1_row['source']
                files = source if isinstance(source, dict) else {_ENGINE_PATHS[_case_arg_1_row['engine']]: source}
                snapshot = RepositoryAnalysisSnapshot(Path('.'), 'DIRECTORY', {name: PythonUnit(name, content, ast.parse(content)) for name, content in files.items()})
                _case_value_1 = ((RepositoryRelationGraph(snapshot),), {})
                yield _case_value_1

    def get_output_data(self):
        """Preserve the originally authored graph projections and source proof checks."""
        import ast
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        _ENGINE_PATHS = {'test_r2_field_access': 'pkg/example.py', 'test_r3_schema_provenance': 'pkg/contracts.py', 'test_r3_parameter_forwarding': 'chain.py'}
        for row in next(iter(JSONDataLoader('tests/cases/semantic-topology-public-v02.json')))['cases']:
            if row['engine'] == 'test_r2_field_access':
                yield (row['expected'], tuple(row['checks']), row['source'])

    def equals(self, expected, actual):
        """Compare exact normalized graph facts and deterministic SHA256 witnesses."""
        truth, check_names, src = expected
        files = src if isinstance(src, dict) else {'pkg/example.py': src}
        observed = [[f.owner_symbol_id, f.method_symbol_id, f.operation, f.line] for f in actual.instance_field_accesses]
        verified = {'source_bound': all((f.source_sha256 == hashlib.sha256(files[f.path.as_posix()].encode()).hexdigest() for f in actual.instance_field_accesses))}
        return truth == {'observed': observed, 'checks': {name: verified[name] for name in check_names}}

class TypedSchemaTopologyCase(TestCase):
    """Check test_r3_schema_provenance with original positive and negative graph facts."""

    def get_enterpoint(self):
        """Return the real production normalized graph function directly."""
        return normalized_topology

    def get_input_data(self):
        """Provide syntactically valid source graph inputs without invoking target."""
        import ast
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        _ENGINE_PATHS = {'test_r2_field_access': 'pkg/example.py', 'test_r3_schema_provenance': 'pkg/contracts.py', 'test_r3_parameter_forwarding': 'chain.py'}
        for row in next(iter(JSONDataLoader('tests/cases/semantic-topology-public-v02.json')))['cases']:
            if row['engine'] == 'test_r3_schema_provenance':
                _case_arg_2_row = row
                source = _case_arg_2_row['source']
                files = source if isinstance(source, dict) else {_ENGINE_PATHS[_case_arg_2_row['engine']]: source}
                snapshot = RepositoryAnalysisSnapshot(Path('.'), 'DIRECTORY', {name: PythonUnit(name, content, ast.parse(content)) for name, content in files.items()})
                _case_value_2 = ((RepositoryRelationGraph(snapshot),), {})
                yield _case_value_2

    def get_output_data(self):
        """Preserve the originally authored graph projections and source proof checks."""
        import ast
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        _ENGINE_PATHS = {'test_r2_field_access': 'pkg/example.py', 'test_r3_schema_provenance': 'pkg/contracts.py', 'test_r3_parameter_forwarding': 'chain.py'}
        for row in next(iter(JSONDataLoader('tests/cases/semantic-topology-public-v02.json')))['cases']:
            if row['engine'] == 'test_r3_schema_provenance':
                yield (row['expected'], tuple(row['checks']), row['source'])

    def equals(self, expected, actual):
        """Compare exact normalized graph facts and deterministic SHA256 witnesses."""
        truth, check_names, src = expected
        files = src if isinstance(src, dict) else {'pkg/contracts.py': src}
        observed = {'schema': [[f.field, f.required, f.annotation, f.type_binding] for f in actual.typed_schema_fields], 'producers': [[p.kind, list(p.observed_literal_keys), p.producer_symbol_id] for p in actual.typed_producers]}
        verified = {'schema_source_bound': all((f.source_sha256 == hashlib.sha256(files[f.path.as_posix()].encode()).hexdigest() for f in actual.typed_schema_fields)), 'producer_schema_bound': all((p.schema_symbol_id in {f.schema_symbol_id for f in actual.typed_schema_fields} for p in actual.typed_producers)), 'open_container_capability': all((p.capability == 'SOURCE_DECLARED_PRODUCER_OPEN_RUNTIME' for p in actual.typed_producers))}
        return truth == {'observed': observed, 'checks': {name: verified[name] for name in check_names}}

class ParameterForwardingTopologyCase(TestCase):
    """Check test_r3_parameter_forwarding with original positive and negative graph facts."""

    def get_enterpoint(self):
        """Return the real production normalized graph function directly."""
        return normalized_topology

    def get_input_data(self):
        """Provide syntactically valid source graph inputs without invoking target."""
        import ast
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        _ENGINE_PATHS = {'test_r2_field_access': 'pkg/example.py', 'test_r3_schema_provenance': 'pkg/contracts.py', 'test_r3_parameter_forwarding': 'chain.py'}
        for row in next(iter(JSONDataLoader('tests/cases/semantic-topology-public-v02.json')))['cases']:
            if row['engine'] == 'test_r3_parameter_forwarding':
                _case_arg_3_row = row
                source = _case_arg_3_row['source']
                files = source if isinstance(source, dict) else {_ENGINE_PATHS[_case_arg_3_row['engine']]: source}
                snapshot = RepositoryAnalysisSnapshot(Path('.'), 'DIRECTORY', {name: PythonUnit(name, content, ast.parse(content)) for name, content in files.items()})
                _case_value_3 = ((RepositoryRelationGraph(snapshot),), {})
                yield _case_value_3

    def get_output_data(self):
        """Preserve the originally authored graph projections and source proof checks."""
        import ast
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        _ENGINE_PATHS = {'test_r2_field_access': 'pkg/example.py', 'test_r3_schema_provenance': 'pkg/contracts.py', 'test_r3_parameter_forwarding': 'chain.py'}
        for row in next(iter(JSONDataLoader('tests/cases/semantic-topology-public-v02.json')))['cases']:
            if row['engine'] == 'test_r3_parameter_forwarding':
                yield (row['expected'], tuple(row['checks']), row['source'])

    def equals(self, expected, actual):
        """Compare exact normalized graph facts and deterministic SHA256 witnesses."""
        truth, check_names, src = expected
        files = src if isinstance(src, dict) else {'chain.py': src}
        observed = [[edge.source_symbol_id, edge.target_symbol_id, edge.source_parameter_index, edge.target_parameter_index] for edge in actual.parameter_forwardings]
        verified = {'graph_source_bound': all((edge.caller_sha256 == hashlib.sha256(files[edge.path.as_posix()].encode()).hexdigest() for edge in actual.parameter_forwardings)), 'open_runtime': all((edge.capability.endswith('OPEN_RUNTIME') for edge in actual.parameter_forwardings))}
        if 'cross_source_bound' in check_names:
            verified['cross_source_bound'] = all((edge.callee_sha256 == hashlib.sha256(files['pkg/producer.py'].encode()).hexdigest() for edge in actual.parameter_forwardings))
        return truth == {'observed': observed, 'checks': {name: verified[name] for name in check_names}}
