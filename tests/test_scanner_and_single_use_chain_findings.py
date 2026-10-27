"""Distinct source/graph Finding contracts migrated from legacy B26 oracles."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.scanner import RepositoryScanner
from runtime.src.call_chain_rules import single_use_chain_findings

class B26ScannerFindingCase(TestCase):
    """Check independent source-linked Finding projections from real scanner rules."""

    def get_enterpoint(self):
        """Return unbound real repository scanner API, never a wrapper."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Feed original positive/negative module programs into production scanner."""
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
        for row in next(iter(JSONDataLoader('tests/cases/scanner-and-single-use-chain-findings.json')))['cases']:
            if row['rule'] != 'QG168':
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
                topology = normalized_topology(RepositoryRelationGraph(snapshot), resolver)
                _case_value_1 = ((RepositoryScanner(root, config, snapshot, topology),), {})
                yield _case_value_1

    def get_output_data(self):
        """Preserve rule, required evidence fields and frozen exact Finding set."""
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
        for row in next(iter(JSONDataLoader('tests/cases/scanner-and-single-use-chain-findings.json')))['cases']:
            if row['rule'] != 'QG168':
                yield (row['rule'], tuple(row['fields']) if 'fields' in row else (), row['expected'])

    def equals(self, expected, actual):
        """Require complete rule-specific Finding multiplicity and source locations."""
        code, fields, truth = expected
        observed = [{'symbol': f.symbol, 'line': f.line, 'severity': f.severity, 'evidence': {name: f.evidence[name] if name in f.evidence else None for name in fields}} for f in actual.findings if f.code == code]
        return observed == truth

class B26SingleUseChainCase(TestCase):
    """Check QG168 single-use chain findings with original source-derived facts."""

    def get_enterpoint(self):
        """Return authentic single-use chain detector."""
        return single_use_chain_findings

    def get_input_data(self):
        """Provide only QG168 graph facts without calling the tested rule."""
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
        for row in next(iter(JSONDataLoader('tests/cases/scanner-and-single-use-chain-findings.json')))['cases']:
            if row['rule'] == 'QG168':
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
                _case_value_3 = ((root, config, normalized_topology(RepositoryRelationGraph(snapshot), resolver)), {})
                yield _case_value_3

    def get_output_data(self):
        """Keep all four original QG168 positive/negative outcomes."""
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
        for row in next(iter(JSONDataLoader('tests/cases/scanner-and-single-use-chain-findings.json')))['cases']:
            if row['rule'] == 'QG168':
                yield (row['rule'], tuple(row['fields']), row['expected'])

    def equals(self, expected, actual):
        """Compare exact QG168 line, symbol, severity and specified evidence."""
        code, fields, truth = expected
        observed = [{'symbol': f.symbol, 'line': f.line, 'severity': f.severity, 'evidence': {name: f.evidence[name] if name in f.evidence else None for name in fields}} for f in actual if f.code == code]
        return observed == truth
