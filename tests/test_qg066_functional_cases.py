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

class RequiredMappingFallbackCase(TestCase):
    """Validate frozen production Finding contracts."""

    def get_enterpoint(self):
        """Execute the existing production scanner, without any test adapter."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build the original source fixture and pass an actual scanner instance."""
        for case_id in ('qg066-b28-required-dict-dict-constructor', 'qg066-b28-required-generic-mapping', 'qg066-b28-optional-union', 'qg066-b28-default-none', 'qg066-b28-untyped-unknown', 'qg066-b28-required-no-fallback', 'qg066-b28-nonempty-fallback', 'qg066-b28-shadowed-dict', 'qg066-b28-reassigned-mapping'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'qg066-functional-v1.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-case-'))
            for name, source in row['files'].items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
            scanner = RepositoryScanner(root, GuardConfig())
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Read the independently frozen expected result."""
        for case_id in ('qg066-b28-required-dict-dict-constructor', 'qg066-b28-required-generic-mapping', 'qg066-b28-optional-union', 'qg066-b28-default-none', 'qg066-b28-untyped-unknown', 'qg066-b28-required-no-fallback', 'qg066-b28-nonempty-fallback', 'qg066-b28-shadowed-dict', 'qg066-b28-reassigned-mapping'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'qg066-functional-v1.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            yield (row['rule'], row['expected'])

    def equals(self, ground_truth, output):
        """Judge actual production Findings, including required absence."""
        rule, expected = ground_truth
        observed = [{'path': f.path, 'severity': f.severity, 'classification': f.evidence['classification']} for f in output.findings if f.code == rule]
        return observed == expected
