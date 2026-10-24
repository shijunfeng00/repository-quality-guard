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

class PrivateReflectionPriorityCase(TestCase):
    """Validate frozen production Finding contracts."""

    def get_enterpoint(self):
        """Execute the existing production scanner, without any test adapter."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build the original source fixture and pass an actual scanner instance."""
        for case_id in ('boundary-qg149-private-member-tp', 'boundary-qg149-public-member-tn', 'boundary-qg149-private-import-tp', 'boundary-qg149-public-import-tn', 'boundary-qg183-private-reflection-tp', 'boundary-qg183-dunder-reflection-tp', 'boundary-qg183-public-static-member-tn', 'boundary-qg183-private-unknown-source-tp', 'boundary-qg184-typed-reflection-tp', 'boundary-qg184-typed-probe-tp', 'boundary-qg184-unknown-probe-tn', 'boundary-qg184-public-direct-tn', 'boundary-qg186-class-mutation-tp', 'boundary-qg186-untyped-instance-tn', 'boundary-qg186-public-call-tn'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'private-reflection-priority-v1.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-case-'))
            target = root / 'subject.py'
            target.write_text(str(row['source']), encoding='utf-8')
            scanner = RepositoryScanner(root, GuardConfig(require_docstrings=False, require_docstring_sections=False))
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Read the independently frozen expected result."""
        for case_id in ('boundary-qg149-private-member-tp', 'boundary-qg149-public-member-tn', 'boundary-qg149-private-import-tp', 'boundary-qg149-public-import-tn', 'boundary-qg183-private-reflection-tp', 'boundary-qg183-dunder-reflection-tp', 'boundary-qg183-public-static-member-tn', 'boundary-qg183-private-unknown-source-tp', 'boundary-qg184-typed-reflection-tp', 'boundary-qg184-typed-probe-tp', 'boundary-qg184-unknown-probe-tn', 'boundary-qg184-public-direct-tn', 'boundary-qg186-class-mutation-tp', 'boundary-qg186-untyped-instance-tn', 'boundary-qg186-public-call-tn'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'private-reflection-priority-v1.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            yield row['expected']

    def equals(self, ground_truth, output):
        """Judge actual production Findings, including required absence."""
        observed = {rule: [[f.severity, f.path, f.line] for f in output.findings if f.code == rule] for rule in ('QG149', 'QG183', 'QG184', 'QG186')}
        return observed == ground_truth
