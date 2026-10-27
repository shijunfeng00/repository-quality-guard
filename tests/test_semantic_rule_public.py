"""High-confidence frozen source-bound semantic findings through published APIs."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.silent_exception_fallback import silent_exception_fallback_findings
from runtime.src.silent_handler_fallthrough import silent_handler_fallthrough_findings
from runtime.src.partial_commit_flow import partial_commit_findings
from runtime.src.constructor_read_order import constructor_read_order_findings
from runtime.src.scanner import RepositoryScanner

class SilentFallbackCase(TestCase):
    """Check independently authored QG214 semantic positive and negative scenarios."""

    def get_enterpoint(self):
        """Return the real production rule entrypoint."""
        return silent_exception_fallback_findings

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/semantic-rule-input-oracle.pkl'):
            if record['case'] == 'SilentFallbackCase':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/semantic-rule-input-oracle.pkl'):
            if record['case'] == 'SilentFallbackCase':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare full semantic identity and independently authored evidence checks."""
        truth, check_names = expected
        observed = [[f.line, f.severity, f.evidence['fallback']] for f in actual]
        verified = {}
        if 'finding_identity' in check_names:
            verified['finding_identity'] = all((f.code == 'QG214' for f in actual))
        if 'semantic_review' in check_names:
            verified['semantic_review'] = all((f.evidence['semantic_status'] in {'REVIEW_NOT_PROVEN_DEFECT', 'REVIEW_NOT_PROVEN_RUNTIME_FAILURE'} for f in actual))
        if 'conditional_review' in check_names:
            verified['conditional_review'] = all((f.evidence['status'] == 'CONDITIONAL_REVIEW_NOT_CONFIRMED_FAILURE' for f in actual))
        return truth == {'observed': observed, 'checks': verified}

class HandlerFallthroughCase(TestCase):
    """Check independently authored QG215 semantic positive and negative scenarios."""

    def get_enterpoint(self):
        """Return the real production rule entrypoint."""
        return silent_handler_fallthrough_findings

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/semantic-rule-input-oracle.pkl'):
            if record['case'] == 'HandlerFallthroughCase':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/semantic-rule-input-oracle.pkl'):
            if record['case'] == 'HandlerFallthroughCase':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare full semantic identity and independently authored evidence checks."""
        truth, check_names = expected
        observed = [[f.line, f.severity, f.evidence['suppression'], f.evidence['local']] for f in actual]
        verified = {}
        if 'finding_identity' in check_names:
            verified['finding_identity'] = all((f.code == 'QG215' for f in actual))
        if 'semantic_review' in check_names:
            verified['semantic_review'] = all((f.evidence['semantic_status'] in {'REVIEW_NOT_PROVEN_DEFECT', 'REVIEW_NOT_PROVEN_RUNTIME_FAILURE'} for f in actual))
        if 'conditional_review' in check_names:
            verified['conditional_review'] = all((f.evidence['status'] == 'CONDITIONAL_REVIEW_NOT_CONFIRMED_FAILURE' for f in actual))
        return truth == {'observed': observed, 'checks': verified}

class PartialCommitFlowCase(TestCase):
    """Check independently authored QG213 semantic positive and negative scenarios."""

    def get_enterpoint(self):
        """Return the real production rule entrypoint."""
        return partial_commit_findings

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/semantic-rule-input-oracle.pkl'):
            if record['case'] == 'PartialCommitFlowCase':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/semantic-rule-input-oracle.pkl'):
            if record['case'] == 'PartialCommitFlowCase':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare full semantic identity and independently authored evidence checks."""
        truth, check_names = expected
        observed = [[f.line, f.severity, f.evidence['first_line'], f.evidence['second_line']] for f in actual]
        verified = {}
        if 'finding_identity' in check_names:
            verified['finding_identity'] = all((f.code == 'QG213' for f in actual))
        if 'semantic_review' in check_names:
            verified['semantic_review'] = all((f.evidence['semantic_status'] in {'REVIEW_NOT_PROVEN_DEFECT', 'REVIEW_NOT_PROVEN_RUNTIME_FAILURE'} for f in actual))
        if 'conditional_review' in check_names:
            verified['conditional_review'] = all((f.evidence['status'] == 'CONDITIONAL_REVIEW_NOT_CONFIRMED_FAILURE' for f in actual))
        return truth == {'observed': observed, 'checks': verified}

class ConstructorOrderCase(TestCase):
    """Check independently authored QG210 semantic positive and negative scenarios."""

    def get_enterpoint(self):
        """Return the real production rule entrypoint."""
        return constructor_read_order_findings

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/semantic-rule-input-oracle.pkl'):
            if record['case'] == 'ConstructorOrderCase':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/semantic-rule-input-oracle.pkl'):
            if record['case'] == 'ConstructorOrderCase':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare full semantic identity and independently authored evidence checks."""
        truth, check_names = expected
        observed = [[f.line, f.severity, f.evidence['first_write_line']] for f in actual]
        verified = {}
        if 'finding_identity' in check_names:
            verified['finding_identity'] = all((f.code == 'QG210' for f in actual))
        if 'semantic_review' in check_names:
            verified['semantic_review'] = all((f.evidence['semantic_status'] in {'REVIEW_NOT_PROVEN_DEFECT', 'REVIEW_NOT_PROVEN_RUNTIME_FAILURE'} for f in actual))
        if 'conditional_review' in check_names:
            verified['conditional_review'] = all((f.evidence['status'] == 'CONDITIONAL_REVIEW_NOT_CONFIRMED_FAILURE' for f in actual))
        return truth == {'observed': observed, 'checks': verified}

class RequiredLiteralProducerCase(TestCase):
    """Check independently authored QG216 semantic positive and negative scenarios."""

    def get_enterpoint(self):
        """Return the real production rule entrypoint."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Supply source-based AST and graph facts, never execute the target."""
        import ast
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot, directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        _ENGINE_PATHS = {'test_r3_silent_fallback': 'pkg/owner.py', 'test_r3_silent_handler_fallthrough': 'owner.py', 'test_r3_partial_commit_flow': 'module.py', 'test_r2_constructor_read_order': 'pkg/state.py', 'test_r3_required_literal_producer': 'contract.py'}
        for row in next(iter(JSONDataLoader('tests/cases/semantic-rule-public.json')))['cases']:
            if row['engine'] == 'test_r3_required_literal_producer':
                _case_arg_1_row = row
                while True:
                    engine = _case_arg_1_row['engine']
                    source = _case_arg_1_row['source']
                    paths = source if isinstance(source, dict) else {_ENGINE_PATHS[engine]: source}
                    if engine == 'test_r3_required_literal_producer':
                        root = Path(tempfile.mkdtemp(prefix='rqg-schema-rule-public-'))
                        atexit.register(shutil.rmtree, root, ignore_errors=True)
                        for name, content in paths.items():
                            p = root / name
                            p.parent.mkdir(parents=True, exist_ok=True)
                            p.write_text(content, encoding='utf-8')
                        config = GuardConfig(include_tests=True, require_docstrings=False)
                        snapshot = directory_analysis_snapshot(root, config)
                        topology = normalized_topology(RepositoryRelationGraph(snapshot))
                        _case_value_1 = ((RepositoryScanner(root, config, analysis_snapshot=snapshot, topology=topology),), {})
                        break
                    snapshot = RepositoryAnalysisSnapshot(Path('.'), 'DIRECTORY', {name: PythonUnit(name, content, ast.parse(content)) for name, content in paths.items()})
                    _case_value_1 = ((snapshot,), {})
                    break
                    break
                yield _case_value_1

    def get_output_data(self):
        """Read fixed source-bound Finding projections and explicitly required checks."""
        import ast
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot, directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.scanner import RepositoryScanner
        _ENGINE_PATHS = {'test_r3_silent_fallback': 'pkg/owner.py', 'test_r3_silent_handler_fallthrough': 'owner.py', 'test_r3_partial_commit_flow': 'module.py', 'test_r2_constructor_read_order': 'pkg/state.py', 'test_r3_required_literal_producer': 'contract.py'}
        for row in next(iter(JSONDataLoader('tests/cases/semantic-rule-public.json')))['cases']:
            if row['engine'] == 'test_r3_required_literal_producer':
                yield (row['expected'], tuple(row['checks']))

    def equals(self, expected, actual):
        """Compare full semantic identity and independently authored evidence checks."""
        truth, check_names = expected
        observed = [[f.code, f.line, f.severity, f.evidence['missing_required_fields'], f.evidence['schema_field_lines']] for f in actual.findings if f.code == 'QG216']
        validations = {}
        return truth == {'observed': observed, 'checks': {key: validations[key] for key in check_names}}
