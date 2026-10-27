"""Independent observable behavior against the real production scanner."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from dataclasses import asdict
from pathlib import Path
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner

class ExactFindingOracleCase(TestCase):
    """Validate frozen production Finding contracts."""

    def get_enterpoint(self):
        """Execute the existing production scanner, without any test adapter."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build the original source fixture and pass an actual scanner instance."""
        for case_id in ('qg149-private-import-tp', 'qg149-public-import-tn', 'qg066-required-dict-tp', 'qg066-optional-dict-tn', 'qg183-private-reflection', 'qg183-public-direct', 'qg184-typed-reflection', 'qg184-unknown-probe', 'qg186-class-monkeypatch', 'qg186-public-direct', 'oracle-qg062-unprotected-zip', 'oracle-qg062-strict', 'oracle-qg063-silent-length-repair', 'oracle-qg063-no-repair', 'oracle-qg069-assert-parameter', 'oracle-qg069-explicit-check', 'oracle-qg070-unused-result', 'oracle-qg070-return-result', 'oracle-qg064-field-alias-fallback', 'oracle-qg064-single-field', 'oracle-qg065-dict-and-attribute', 'oracle-qg065-single-protocol'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'exact-finding-oracles.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-case-'))
            target = root / 'subject.py'
            target.write_text(str(row['source']), encoding='utf-8')
            scanner = RepositoryScanner(root, GuardConfig(require_docstrings=False, require_docstring_sections=False))
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Read the independently frozen expected result."""
        for case_id in ('qg149-private-import-tp', 'qg149-public-import-tn', 'qg066-required-dict-tp', 'qg066-optional-dict-tn', 'qg183-private-reflection', 'qg183-public-direct', 'qg184-typed-reflection', 'qg184-unknown-probe', 'qg186-class-monkeypatch', 'qg186-public-direct', 'oracle-qg062-unprotected-zip', 'oracle-qg062-strict', 'oracle-qg063-silent-length-repair', 'oracle-qg063-no-repair', 'oracle-qg069-assert-parameter', 'oracle-qg069-explicit-check', 'oracle-qg070-unused-result', 'oracle-qg070-return-result', 'oracle-qg064-field-alias-fallback', 'oracle-qg064-single-field', 'oracle-qg065-dict-and-attribute', 'oracle-qg065-single-protocol'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'exact-finding-oracles.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            yield (row['rule'], row['findings'])

    def equals(self, ground_truth, output):
        """Judge actual production Findings, including required absence."""
        rule, expected = ground_truth
        return [asdict(f) for f in output.findings if f.code == rule] == expected
