"""Independent B30 field-flow and fail-closed compiler witness regressions."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import Outcome, TestCase
from runtime.src.relation_graph import normalized_topology
from runtime.src.compiler_contracts import r1_bound_call_findings

class B30FieldInitializationCase(TestCase):
    """Verify normal/exception path source-grounded field initialization."""

    def get_enterpoint(self):
        """Return the public normalized graph analyzer directly."""
        return normalized_topology

    def get_input_data(self):
        """Supply the three authored constructor/field source graphs."""
        import ast
        import hashlib
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/field-initialization-and-compiler-witnesses.json')))['cases']:
            if row['query'] == 'field_flow':
                _case_arg_1_row = row
                _case_arg_2_row = _case_arg_1_row
                path = _case_arg_2_row['path']
                source = _case_arg_2_row['source']
                _case_value_2 = RepositoryAnalysisSnapshot(Path('.'), 'DIRECTORY', {path: PythonUnit(path, source, ast.parse(source))})
                _case_value_1 = ((RepositoryRelationGraph(_case_value_2),), {})
                yield _case_value_1

    def get_output_data(self):
        """Preserve all independent expected initialization verdicts."""
        import ast
        import hashlib
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/field-initialization-and-compiler-witnesses.json')))['cases']:
            if row['query'] == 'field_flow':
                yield tuple(row['expected'])

    def equals(self, expected, actual):
        """Require complete source-grounded field-initialization verdict order."""
        return tuple((fact.verdict for fact in actual.field_initializations)) == expected

class B30CompilerWitnessAcceptedCase(TestCase):
    """Validate compiler-source identity and deduplicate authentic diagnostics."""

    def get_enterpoint(self):
        """Return genuine production compiler-witness validation."""
        return r1_bound_call_findings

    def get_input_data(self):
        """Feed only the originally valid source-bound compiler witness."""
        import ast
        import hashlib
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/field-initialization-and-compiler-witnesses.json')))['cases']:
            if row['query'] == 'compiler_witness' and (not row['expected']['rejected']):
                _case_arg_3_row = row
                digest = hashlib.sha256(_case_arg_3_row['source'].encode()).hexdigest()
                witness = {'verdict': 'STATIC_ARITY_MISMATCH_REVIEW_REQUIRED', 'runtime_dispatch': 'OPEN_WORLD', 'capability': 'COMPILER_UNIQUE_SOURCE_SIGNATURE', 'path': _case_arg_3_row['path'], 'line': 3, 'column': 1, 'symbol': 'module.deliver', 'caller_sha256': digest, 'callee_sha256': digest, 'callee_path': _case_arg_3_row['path'], 'witness': "missing a required argument: 'right'"}
                witness.update(_case_arg_3_row['change'])
                _case_arg_4_row = _case_arg_3_row
                path = _case_arg_4_row['path']
                source = _case_arg_4_row['source']
                _case_value_4 = RepositoryAnalysisSnapshot(Path('.'), 'DIRECTORY', {path: PythonUnit(path, source, ast.parse(source))})
                _case_value_3 = ((_case_value_4, (witness, witness)), {})
                yield _case_value_3

    def get_output_data(self):
        """Keep original independently authored QG209 Finding evidence."""
        import ast
        import hashlib
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/field-initialization-and-compiler-witnesses.json')))['cases']:
            if row['query'] == 'compiler_witness' and (not row['expected']['rejected']):
                yield tuple((tuple(item) for item in row['expected']['findings']))

    def equals(self, expected, actual):
        """Require exact source-bound warning and open-world dispatch capability."""
        return tuple(((f.code, f.severity, f.evidence['dispatch']) for f in actual)) == expected

class B30CompilerWitnessRejectedCase(TestCase):
    """Reject five independently forged or stale compiler evidence records."""

    def __init__(self):
        """Use real framework exception outcome rather than swallowing errors."""
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        """Return actual compiler-witness production validation."""
        return r1_bound_call_findings

    def get_input_data(self):
        """Supply forged caller, callee, dispatch and source locations."""
        import ast
        import hashlib
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/field-initialization-and-compiler-witnesses.json')))['cases']:
            if row['query'] == 'compiler_witness' and row['expected']['rejected']:
                _case_arg_5_row = row
                digest = hashlib.sha256(_case_arg_5_row['source'].encode()).hexdigest()
                witness = {'verdict': 'STATIC_ARITY_MISMATCH_REVIEW_REQUIRED', 'runtime_dispatch': 'OPEN_WORLD', 'capability': 'COMPILER_UNIQUE_SOURCE_SIGNATURE', 'path': _case_arg_5_row['path'], 'line': 3, 'column': 1, 'symbol': 'module.deliver', 'caller_sha256': digest, 'callee_sha256': digest, 'callee_path': _case_arg_5_row['path'], 'witness': "missing a required argument: 'right'"}
                witness.update(_case_arg_5_row['change'])
                _case_arg_6_row = _case_arg_5_row
                path = _case_arg_6_row['path']
                source = _case_arg_6_row['source']
                _case_value_6 = RepositoryAnalysisSnapshot(Path('.'), 'DIRECTORY', {path: PythonUnit(path, source, ast.parse(source))})
                _case_value_5 = ((_case_value_6, (witness, witness)), {})
                yield _case_value_5

    def get_output_data(self):
        """Require the original explicit invalid-witness ValueError contract."""
        import ast
        import hashlib
        import json
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from runtime.src.relation_graph import RepositoryRelationGraph
        for row in next(iter(JSONDataLoader('tests/cases/field-initialization-and-compiler-witnesses.json')))['cases']:
            if row['query'] == 'compiler_witness' and row['expected']['rejected']:
                yield ValueError

    def equals(self, expected, actual):
        """Disallow successful forgery or unrelated exception classes."""
        return type(actual) is expected
