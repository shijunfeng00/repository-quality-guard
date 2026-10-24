"""Positive and negative external Findings for the expanded Core rule families."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner

class ExpandedCoreRuleCase(TestCase):
    """Preserve author-frozen Findings without reconstructing rule internals."""

    def get_enterpoint(self):
        """Exercise scanner-owned rule dispatch rather than a test-specific adapter."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Provide one standalone fixture repository to the production scanner."""
        for case_id in ('qg060-disjoint', 'qg060-compatible', 'qg061-wrap', 'qg061-no-wrap', 'qg066-default', 'qg066-required', 'qg045-incompatible', 'qg045-compatible', 'qg144-shape', 'qg144-uniform', 'qg046-implicit', 'qg046-covered', 'qg047-protocol', 'qg047-text', 'qg159-legacy', 'qg159-canonical', 'qg160-vague', 'qg160-math'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'core-expanded-rule-object-cases.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-expanded-rule-'))
            (root / 'subject.py').write_text(row['source'], encoding='utf-8')
            scanner = RepositoryScanner(root, GuardConfig(require_docstrings=False, require_docstring_sections=False))
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Return the independent severity/position oracle from the fixture set."""
        for case_id in ('qg060-disjoint', 'qg060-compatible', 'qg061-wrap', 'qg061-no-wrap', 'qg066-default', 'qg066-required', 'qg045-incompatible', 'qg045-compatible', 'qg144-shape', 'qg144-uniform', 'qg046-implicit', 'qg046-covered', 'qg047-protocol', 'qg047-text', 'qg159-legacy', 'qg159-canonical', 'qg160-vague', 'qg160-math'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'core-expanded-rule-object-cases.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            yield (row['rule'], tuple((tuple(item) for item in row['expected'])))

    def equals(self, ground_truth, output):
        """Require exactly the authored Findings, including empty negative cases."""
        rule, expected = ground_truth
        observed = tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in output.findings if finding.code == rule))
        return observed == expected
