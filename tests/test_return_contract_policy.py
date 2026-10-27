"""Direct TestUnit behavior contract for production rule detection."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner

class ReturnContractCase(TestCase):
    """Compare optional and mandatory return paths via production Scanner."""

    def get_enterpoint(self):
        """Call the production repository Scanner with a real source snapshot."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build independent nullable and required-return source fixtures."""
        for case_id in ('nullable-return-implicit-none', 'required-return-implicit-none'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'qg046-return-contract.json').read_text(encoding='utf-8'))['cases'] if row['case_id'] == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-qg046-'))
            (root / 'sample.py').write_text(row['source'], encoding='utf-8')
            scanner = RepositoryScanner(root, GuardConfig(require_docstrings=False, require_docstring_sections=False, include_tests=True))
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Read the fixed QG046 expectation for each return contract."""
        for case_id in ('nullable-return-implicit-none', 'required-return-implicit-none'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'qg046-return-contract.json').read_text(encoding='utf-8'))['cases'] if row['case_id'] == case_id))
            yield row['expected']

    def equals(self, ground_truth, output):
        """Compare the QG046 presence predicate against its independent Oracle."""
        return any((f.code == 'QG046' for f in output.findings)) is ground_truth
