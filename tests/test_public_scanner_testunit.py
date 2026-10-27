"""Independent observable behavior against the real production scanner."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner

class PublicScannerFindingCase(TestCase):
    """Validate frozen production Finding contracts."""

    def get_enterpoint(self):
        """Execute the existing production scanner, without any test adapter."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build the original source fixture and pass an actual scanner instance."""
        for case_id in ('qg149-production-private-import', 'qg149-production-public-import', 'qg149-test-private-import', 'qg149-test-public-import', 'qg149-cross-owner-private-attribute', 'qg149-same-owner-private-attribute', 'qg149-subclass-inherited-private', 'qg149-subclass-explicit-override', 'qg149-untyped-peer-access', 'qg149-typed-peer-access', 'qg149-private-explicit-export', 'qg149-public-explicit-export', 'qg149-private-alias-export', 'qg149-public-alias-export', 'qg149-test-private-reflection', 'qg149-test-public-reflection', 'qg149-test-own-class-private-reflection', 'qg149-dunder-protocol-is-public', 'qg149-source-derived-test-private-import-tp', 'qg149-source-derived-test-public-import-tn', 'qg149-source-derived-test-reflection-tp', 'qg149-source-derived-public-reflection-tn', 'qg149-source-derived-cross-owner-field-tp', 'qg149-source-derived-public-view-tn', 'qg048-source-tp-0', 'qg048-source-tn-1', 'qg049-source-tp-2', 'qg049-source-tn-3', 'qg051-source-tp-4', 'qg051-source-tn-5', 'qg052-source-tp-6', 'qg052-source-tn-7'):
            row = next((row for filename in ('qg149-private-boundary.json', 'qg048-052-public-effects.json') for row in json.loads((Path(__file__).parent / 'cases' / filename).read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-case-'))
            for name, source in row['files'].items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
            scanner = RepositoryScanner(root, GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True))
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Read the independently frozen expected result."""
        for case_id in ('qg149-production-private-import', 'qg149-production-public-import', 'qg149-test-private-import', 'qg149-test-public-import', 'qg149-cross-owner-private-attribute', 'qg149-same-owner-private-attribute', 'qg149-subclass-inherited-private', 'qg149-subclass-explicit-override', 'qg149-untyped-peer-access', 'qg149-typed-peer-access', 'qg149-private-explicit-export', 'qg149-public-explicit-export', 'qg149-private-alias-export', 'qg149-public-alias-export', 'qg149-test-private-reflection', 'qg149-test-public-reflection', 'qg149-test-own-class-private-reflection', 'qg149-dunder-protocol-is-public', 'qg149-source-derived-test-private-import-tp', 'qg149-source-derived-test-public-import-tn', 'qg149-source-derived-test-reflection-tp', 'qg149-source-derived-public-reflection-tn', 'qg149-source-derived-cross-owner-field-tp', 'qg149-source-derived-public-view-tn', 'qg048-source-tp-0', 'qg048-source-tn-1', 'qg049-source-tp-2', 'qg049-source-tn-3', 'qg051-source-tp-4', 'qg051-source-tn-5', 'qg052-source-tp-6', 'qg052-source-tn-7'):
            row = next((row for filename in ('qg149-private-boundary.json', 'qg048-052-public-effects.json') for row in json.loads((Path(__file__).parent / 'cases' / filename).read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            yield (row['rule'], row['expected'], row['evidence_required'])

    def equals(self, ground_truth, output):
        """Judge actual production Findings, including required absence."""
        rule, expected, required_evidence = ground_truth
        observed = [f for f in output.findings if f.code == rule]
        if [(f.path, f.line, f.severity) for f in observed] != [tuple(item) for item in expected]:
            return False
        if len(required_evidence) != len(observed):
            return False
        return all((all((key in f.evidence and f.evidence[key] == value for key, value in criteria.items())) for f, criteria in zip(observed, required_evidence)))
