"""Frozen TP/TN Findings for real Core rules, observed through the scanner."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner

class CoreRuleObjectCase(TestCase):
    """Check rule-specific scanner Findings against independent source examples."""

    def get_enterpoint(self):
        """Invoke the repository's existing scan entry point directly."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Materialize the authored source, then supply the real scanner instance."""
        for case_id in ('qg062-unprotected-zip', 'qg062-strict', 'qg063-silent-length-repair', 'qg063-no-repair', 'qg069-assert-parameter', 'qg069-explicit-check', 'qg070-unused-result', 'qg070-return-result', 'qg064-field-alias-fallback', 'qg064-single-field', 'qg065-dict-and-attribute', 'qg065-single-protocol'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'core-rule-object-behavior.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-core-rule-'))
            (root / 'subject.py').write_text(row['source'], encoding='utf-8')
            scanner = RepositoryScanner(root, GuardConfig(require_docstrings=False, require_docstring_sections=False))
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Read the frozen rule identity and positive/negative Finding oracle."""
        for case_id in ('qg062-unprotected-zip', 'qg062-strict', 'qg063-silent-length-repair', 'qg063-no-repair', 'qg069-assert-parameter', 'qg069-explicit-check', 'qg070-unused-result', 'qg070-return-result', 'qg064-field-alias-fallback', 'qg064-single-field', 'qg065-dict-and-attribute', 'qg065-single-protocol'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'core-rule-object-behavior.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            yield (row['rule'], tuple((tuple(item) for item in row['expected'])))

    def equals(self, ground_truth, output):
        """Compare observed code, severity, line and owner for this QG only."""
        rule, expected = ground_truth
        observed = tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in output.findings if finding.code == rule))
        return observed == expected
