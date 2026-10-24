"""Production QG060 and QG066 Finding behavior against authored TP/TN cases."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path

from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


class RuleFindingBehaviorCase(TestCase):
    """Require QG060/QG066 published severity and evidence contracts."""

    def get_enterpoint(self):
        """Return the production repository scanner without an adapter."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Create source roots from the existing independent fixture programs."""
        for name in ('qg060.json', 'rule-finding-behavior-v1.json'):
            cases = json.loads((Path(__file__).parent / 'cases' / name).read_text(encoding='utf-8'))['cases']
            for row in cases:
                root = Path(tempfile.mkdtemp(prefix='rqg-rule-finding-'))
                (root / 'subject.py').write_text(row['source'], encoding='utf-8')
                scanner = RepositoryScanner(root, GuardConfig(
                    require_docstrings=False, require_docstring_sections=False,
                ))
                weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
                yield ((scanner,), {})

    def get_output_data(self):
        """Read independently frozen severities without scanning actual results."""
        for name in ('qg060.json', 'rule-finding-behavior-v1.json'):
            cases = json.loads((Path(__file__).parent / 'cases' / name).read_text(encoding='utf-8'))['cases']
            for row in cases:
                rule = 'QG060' if name == 'qg060.json' else row['rule']
                yield (rule, tuple(row['expected']))

    def equals(self, expected, actual):
        """Compare code-specific severity and required source-provenance evidence."""
        rule, severities = expected
        found = tuple(f for f in actual.findings if f.code == rule)
        evidence_ok = all(
            (
                f.evidence['classification'] in {'PROVEN', 'REVIEW'}
                and (f.severity != 'critical' or
                     (f.evidence['relation'] == 'disjoint_builtin' and f.confidence == 'high'))
            ) if rule == 'QG060' else (
                f.severity == 'warning'
                and f.evidence['classification'] == 'PROVEN_REQUIRED_CONTRACT'
            )
            for f in found
        )
        return tuple(f.severity for f in found) == severities and evidence_ok
