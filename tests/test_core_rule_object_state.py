"""Independent QG signature and module-rule regression fixtures."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner

class BatchFiveCoreObjectCase(TestCase):
    """Check the existing scanner against frozen signature/module rule TP/TN."""

    def get_enterpoint(self):
        """Execute the production scan pipeline and its actual rule owners."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build a representative module, including utility-module naming semantics."""
        for case_id in ('qg031-trigger', 'qg031-allowed', 'qg032-trigger', 'qg032-allowed', 'qg033-trigger', 'qg033-allowed', 'qg034-trigger', 'qg034-allowed', 'qg039-trigger', 'qg039-allowed', 'qg040-trigger', 'qg040-allowed', 'qg042-trigger', 'qg042-allowed', 'qg145-trigger', 'qg145-allowed', 'qg053-trigger', 'qg053-allowed', 'qg054-trigger', 'qg054-allowed', 'qg055-trigger', 'qg055-allowed', 'qg056-trigger', 'qg056-allowed', 'qg091-trigger', 'qg091-allowed', 'qg092-trigger', 'qg092-allowed', 'qg093-trigger', 'qg093-allowed', 'qg094-trigger', 'qg094-allowed', 'qg095-trigger', 'qg095-allowed', 'qg096-trigger', 'qg096-allowed', 'qg097-trigger', 'qg097-allowed', 'qg102-trigger', 'qg102-allowed', 'qg106-trigger', 'qg106-allowed'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'core-rule-object-state.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-core-signature-'))
            filename = 'utils.py' if row['rule'] == 'QG091' else 'subject.py'
            (root / filename).write_text(row['source'], encoding='utf-8')
            scanner = RepositoryScanner(root, GuardConfig(require_docstrings=False, require_docstring_sections=False))
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Use the authored rule/position contract, not the scanner's output."""
        for case_id in ('qg031-trigger', 'qg031-allowed', 'qg032-trigger', 'qg032-allowed', 'qg033-trigger', 'qg033-allowed', 'qg034-trigger', 'qg034-allowed', 'qg039-trigger', 'qg039-allowed', 'qg040-trigger', 'qg040-allowed', 'qg042-trigger', 'qg042-allowed', 'qg145-trigger', 'qg145-allowed', 'qg053-trigger', 'qg053-allowed', 'qg054-trigger', 'qg054-allowed', 'qg055-trigger', 'qg055-allowed', 'qg056-trigger', 'qg056-allowed', 'qg091-trigger', 'qg091-allowed', 'qg092-trigger', 'qg092-allowed', 'qg093-trigger', 'qg093-allowed', 'qg094-trigger', 'qg094-allowed', 'qg095-trigger', 'qg095-allowed', 'qg096-trigger', 'qg096-allowed', 'qg097-trigger', 'qg097-allowed', 'qg102-trigger', 'qg102-allowed', 'qg106-trigger', 'qg106-allowed'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'core-rule-object-state.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            yield (row['rule'], tuple((tuple(item) for item in row['expected'])))

    def equals(self, ground_truth, output):
        """Require every QG-specific public Finding field to match the oracle."""
        rule, expected = ground_truth
        observed = tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in output.findings if finding.code == rule))
        return observed == expected
