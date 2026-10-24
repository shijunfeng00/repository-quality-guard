"""Profile nonblocking path handling, observed in real scanner Findings."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.project_profiles import ProjectProfile
from runtime.src.scanner import RepositoryScanner

class ProfileNonblockingPathCase(TestCase):
    """Keep QG149 visible while checking a Profile's chosen severity."""

    def get_enterpoint(self):
        """Call the scanner that applies the actual nonblocking policy."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Construct the source owner and the authored Profile for this identity."""
        for case_id in ('profile-private-import-explicit-nonblocking', 'profile-private-import-default-blocking'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'profile-nonblocking-path-v1.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-nonblocking-profile-'))
            source = root / 'utils' / 'tracing.py'
            source.parent.mkdir(parents=True)
            source.write_text('from pkg._internal import _Profiler\n', encoding='utf-8')
            profile = ProjectProfile(name='nonblocking-path-fixture', nonblocking_paths=tuple(row['nonblocking']))
            config = GuardConfig(include_tests=True, require_docstrings=False, require_docstring_sections=False).with_project_profile(profile)
            scanner = RepositoryScanner(root, config)
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Keep the independently declared severity as the only accepted outcome."""
        for case_id in ('profile-private-import-explicit-nonblocking', 'profile-private-import-default-blocking'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'profile-nonblocking-path-v1.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            yield [row['expected_severity']]

    def equals(self, ground_truth, output):
        """Prove the scanner reported QG149 instead of silently filtering it."""
        return [finding.severity for finding in output.findings if finding.code == 'QG149'] == ground_truth
