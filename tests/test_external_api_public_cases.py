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

class ExternalApiPublicCase(TestCase):
    """Validate frozen production Finding contracts."""

    def get_enterpoint(self):
        """Execute the existing production scanner, without any test adapter."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build the original source fixture and pass an actual scanner instance."""
        for case_id in ('external-binding-canonical', 'external-binding-aliases', 'external-binding-unimported', 'external-binding-renamed', 'external-binding-shadow1', 'external-binding-shadow2', 'external-binding-shadow3', 'external-binding-shadow4', 'external-binding-shadow5', 'external-binding-shadow6', 'external-binding-shadow7', 'external-binding-unknown-splat', 'external-binding-explicit-timeout', 'external-binding-blocking-async', 'external-binding-local-module', 'external-binding-comprehension-shadow', 'external-binding-httpx-constructor', 'external-binding-multiple-real-calls', 'external-binding-multiple-shadowed-calls', 'external-binding-local-package-repeated', 'external-binding-other-local-package-independent'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'bound-external-api-functional.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-case-'))
            (root / 'module.py').write_text(row['source'], encoding='utf-8')
            for name, source in row['files'].items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
            scanner = RepositoryScanner(root, GuardConfig())
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Read the independently frozen expected result."""
        for case_id in ('external-binding-canonical', 'external-binding-aliases', 'external-binding-unimported', 'external-binding-renamed', 'external-binding-shadow1', 'external-binding-shadow2', 'external-binding-shadow3', 'external-binding-shadow4', 'external-binding-shadow5', 'external-binding-shadow6', 'external-binding-shadow7', 'external-binding-unknown-splat', 'external-binding-explicit-timeout', 'external-binding-blocking-async', 'external-binding-local-module', 'external-binding-comprehension-shadow', 'external-binding-httpx-constructor', 'external-binding-multiple-real-calls', 'external-binding-multiple-shadowed-calls', 'external-binding-local-package-repeated', 'external-binding-other-local-package-independent'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'bound-external-api-functional.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            yield tuple(row['expected'])

    def equals(self, ground_truth, output):
        """Judge actual production Findings, including required absence."""
        observed = tuple(sorted({f.code for f in output.findings if f.path == 'module.py' and f.code in {'QG053', 'QG054', 'QG055'}}))
        return observed == ground_truth
