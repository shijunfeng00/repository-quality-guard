"""Independent published Python re-export and local call-graph behavior."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.python_import_resolution import resolve_local_import_symbol
from runtime.src.relation_graph import normalized_topology
from runtime.src.scanner import RepositoryScanner
from runtime.src.topology_facts import UsageKind

class ReexportAliasCase(TestCase):
    """Preserve transitive aliases, unresolved names and re-export cycles."""

    def get_enterpoint(self):
        """Return real import-alias resolver."""
        return resolve_local_import_symbol

    def get_input_data(self):
        """Pass original declared import graph and symbol set."""
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
        for row in next(iter(JSONDataLoader('tests/cases/reexport-functional.json')))['cases']:
            if row['scenario'] == 'alias':
                yield ((row['symbol'], row['imports'], set(row['known'])), {})

    def get_output_data(self):
        """Keep original authored exact resolution result."""
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
        for row in next(iter(JSONDataLoader('tests/cases/reexport-functional.json')))['cases']:
            if row['scenario'] == 'alias':
                yield row['expected']

    def equals(self, expected, actual):
        """Compare the entire produced alias identity."""
        return expected == actual

class ReexportScannerCase(TestCase):
    """Prove imported facades do not create invented QG002 ownership."""

    def get_enterpoint(self):
        """Return actual repository scanner."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build exact file map in an isolated repository."""
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
        for row in next(iter(JSONDataLoader('tests/cases/reexport-functional.json')))['cases']:
            if row['scenario'] == 'scanner':
                _case_arg_1_row = row
                _case_arg_1_for_scan = True
                while True:
                    root = Path(tempfile.mkdtemp(prefix='rqg-reexport-migrated-'))
                    for filename, source in _case_arg_1_row['files'].items():
                        target = root / filename
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(source, encoding='utf-8')
                    config = GuardConfig(require_docstrings=False)
                    if _case_arg_1_for_scan:
                        scanner = RepositoryScanner(root, config)
                        weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
                        _case_value_1 = ((scanner,), {})
                        break
                    snapshot = directory_analysis_snapshot(root, config)
                    graph = RepositoryRelationGraph(snapshot)
                    weakref.finalize(graph, shutil.rmtree, root, ignore_errors=True)
                    _case_value_1 = ((graph, StaticPythonDependencyResolver(())), {})
                    break
                    break
                yield _case_value_1

    def get_output_data(self):
        """Read authored expected owner symbols."""
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
        for row in next(iter(JSONDataLoader('tests/cases/reexport-functional.json')))['cases']:
            if row['scenario'] == 'scanner':
                yield row['expected']

    def equals(self, expected, actual):
        """Require exact QG002 symbols instead of matching the expected subset."""
        observed = sorted({item.symbol for item in actual.findings if item.code == 'QG002' and item.symbol == 'pkg.base.Tiny'})
        return observed == expected

class ReexportGraphCase(TestCase):
    """Preserve independent incoming-call identities across re-export boundaries."""

    def get_enterpoint(self):
        """Return native normalized relation-graph producer."""
        return normalized_topology

    def get_input_data(self):
        """Build source graph and direct dependency facts for the real producer."""
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
        for row in next(iter(JSONDataLoader('tests/cases/reexport-functional.json')))['cases']:
            if row['scenario'] == 'graph':
                _case_arg_2_row = row
                _case_arg_2_for_scan = False
                while True:
                    root = Path(tempfile.mkdtemp(prefix='rqg-reexport-migrated-'))
                    for filename, source in _case_arg_2_row['files'].items():
                        target = root / filename
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(source, encoding='utf-8')
                    config = GuardConfig(require_docstrings=False)
                    if _case_arg_2_for_scan:
                        scanner = RepositoryScanner(root, config)
                        weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
                        _case_value_2 = ((scanner,), {})
                        break
                    snapshot = directory_analysis_snapshot(root, config)
                    graph = RepositoryRelationGraph(snapshot)
                    weakref.finalize(graph, shutil.rmtree, root, ignore_errors=True)
                    _case_value_2 = ((graph, StaticPythonDependencyResolver(())), {})
                    break
                    break
                yield _case_value_2

    def get_output_data(self):
        """Read authored callers of the originally re-exported type."""
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
        for row in next(iter(JSONDataLoader('tests/cases/reexport-functional.json')))['cases']:
            if row['scenario'] == 'graph':
                yield row['expected']

    def equals(self, expected, actual):
        """Compare complete direct-call predecessors of the type."""
        observed = sorted({edge.source_id for edge in actual.incoming('pkg.base.Tiny', (UsageKind.DIRECT_CALL,))})
        return observed == expected
