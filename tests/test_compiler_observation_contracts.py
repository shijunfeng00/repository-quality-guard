"""Native Pyright diagnostic source identity and R2 fact-binding contracts."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase, Outcome
from runtime.src.compiler_contracts import r2_initialization_observations

class CompilerBoundObservationsCase(TestCase):
    """Require source-authenticated observation fields for valid evidence."""

    def get_enterpoint(self):
        """Return the real source-bound diagnostic observation API."""
        return r2_initialization_observations

    def get_input_data(self):
        """Build the original nonexception compiler observation fixtures."""
        import atexit
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.project_profiles import QualityGuardProfile
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.scoped_rule_policy import ScopedRuleMatcher
        _DATA = Path(__file__).resolve().parents[1] / 'tests' / 'cases'
        for row in next(iter(JSONDataLoader('tests/cases/compiler-bound-fields.json')))['cases']:
            if not isinstance(row['expected'], dict):
                _case_arg_1_row = row
                import hashlib
                from runtime.src.analysis_snapshot import directory_analysis_snapshot
                from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
                source = str(_case_arg_1_row['source'])
                root = Path(tempfile.mkdtemp(prefix='rqg-r2-observation-migrated-'))
                target = root / 'package/example.py'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
                snapshot = directory_analysis_snapshot(root, GuardConfig())
                topology = normalized_topology(RepositoryRelationGraph(snapshot))
                evidence = {'native_rule': 'reportUninitializedInstanceVariable', 'source_path': 'package/example.py', 'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'line': 5, 'column': _case_arg_1_row['column']}
                evidence.update(_case_arg_1_row['changes'])
                identity = {'pyright_sha256': 'a' * 64, 'typeshed_sha256': 'b' * 64} if _case_arg_1_row['identity'] else {}
                atexit.register(shutil.rmtree, root, ignore_errors=True)
                _case_value_1 = ((snapshot, [evidence], topology), {'provider_identity': identity})
                yield _case_value_1

    def get_output_data(self):
        """Retain independent owner, member, binding and capability expectations."""
        import atexit
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.project_profiles import QualityGuardProfile
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.scoped_rule_policy import ScopedRuleMatcher
        _DATA = Path(__file__).resolve().parents[1] / 'tests' / 'cases'
        for row in next(iter(JSONDataLoader('tests/cases/compiler-bound-fields.json')))['cases']:
            if not isinstance(row['expected'], dict):
                yield row['expected']

    def equals(self, expected, actual):
        """Compare every returned production observation against the frozen rows."""
        observed = [{'owner': fact.owner_symbol_id, 'member': fact.member, 'binding': fact.binding, 'capability': fact.capability} for fact in actual]
        return expected == observed

class CompilerInvalidSourceCase(TestCase):
    """Reject stale compiler diagnostics and unauthenticated provider identities."""

    def __init__(self):
        """Declare that each tampered identity must raise an exception."""
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        """Return production observation function without a wrapper."""
        return r2_initialization_observations

    def get_input_data(self):
        """Provide original source-tampered compiler facts and absent identities."""
        import atexit
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.project_profiles import QualityGuardProfile
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.scoped_rule_policy import ScopedRuleMatcher
        _DATA = Path(__file__).resolve().parents[1] / 'tests' / 'cases'
        for row in next(iter(JSONDataLoader('tests/cases/compiler-bound-fields.json')))['cases']:
            if isinstance(row['expected'], dict):
                _case_arg_2_row = row
                import hashlib
                from runtime.src.analysis_snapshot import directory_analysis_snapshot
                from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
                source = str(_case_arg_2_row['source'])
                root = Path(tempfile.mkdtemp(prefix='rqg-r2-observation-migrated-'))
                target = root / 'package/example.py'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
                snapshot = directory_analysis_snapshot(root, GuardConfig())
                topology = normalized_topology(RepositoryRelationGraph(snapshot))
                evidence = {'native_rule': 'reportUninitializedInstanceVariable', 'source_path': 'package/example.py', 'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'line': 5, 'column': _case_arg_2_row['column']}
                evidence.update(_case_arg_2_row['changes'])
                identity = {'pyright_sha256': 'a' * 64, 'typeshed_sha256': 'b' * 64} if _case_arg_2_row['identity'] else {}
                atexit.register(shutil.rmtree, root, ignore_errors=True)
                _case_value_2 = ((snapshot, [evidence], topology), {'provider_identity': identity})
                yield _case_value_2

    def get_output_data(self):
        """Require the same explicit failure class as the original oracle."""
        import atexit
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.project_profiles import QualityGuardProfile
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.scoped_rule_policy import ScopedRuleMatcher
        _DATA = Path(__file__).resolve().parents[1] / 'tests' / 'cases'
        for row in next(iter(JSONDataLoader('tests/cases/compiler-bound-fields.json')))['cases']:
            if isinstance(row['expected'], dict):
                yield ValueError

    def equals(self, expected, actual):
        """Require exact ValueError class, not successful fallback output."""
        return type(actual) is expected
