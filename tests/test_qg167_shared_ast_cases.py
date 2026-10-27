"""QG167 inheritance contracts using real Git-backed architecture comparison."""
from __future__ import annotations
import atexit
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from rqgtest import TestCase
from runtime.src.architecture_diff import compare_git_architecture
from runtime.src.config import GuardConfig

class ShadowStateBehaviorCase(TestCase):
    """Compare independently authored reference-expression TP/TN cases."""

    def get_enterpoint(self):
        """Call the actual Git architecture comparison, not a test adapter."""
        return compare_git_architecture

    def get_input_data(self):
        """Create the Git baseline and changed inheritance source for one Case."""
        for case_id in ('qg167-reference-name', 'qg167-reference-attribute', 'qg167-reference-index', 'qg167-computed-expression-not-reference', 'qg167-call-not-reference'):
            cases_path = Path(__file__).parent / 'cases' / 'qg167-shared-ast.json'
            cases = json.loads(cases_path.read_text(encoding='utf-8'))['cases']
            row = next((item for item in cases if item['case_id'] == case_id))
            expression = row['expression']
            root = Path(tempfile.mkdtemp(prefix='rqg-qg167-git-'))
            atexit.register(shutil.rmtree, root, ignore_errors=True)
            for args in (('init', '-q'), ('config', 'user.name', 'QG167 Contract'), ('config', 'user.email', 'qg167@example.invalid')):
                subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
            source = root / 'inheritance.py'
            common = f'class Parent:\n    def __init__(self, source):\n        self.origin = {expression}\n\nclass Child(Parent):\n    def __init__(self, source):\n        super().__init__(source)\n'
            source.write_text(common, encoding='utf-8')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True, capture_output=True)
            subprocess.run(['git', 'commit', '-qm', 'baseline'], cwd=root, check=True, capture_output=True)
            source.write_text(common + f'        self.shadow = {expression}\n', encoding='utf-8')
            yield ((root, GuardConfig(), 'HEAD'), {})

    def get_output_data(self):
        """Freeze TP/TN expectation independently of the observed Findings."""
        for case_id in ('qg167-reference-name', 'qg167-reference-attribute', 'qg167-reference-index', 'qg167-computed-expression-not-reference', 'qg167-call-not-reference'):
            cases_path = Path(__file__).parent / 'cases' / 'qg167-shared-ast.json'
            cases = json.loads(cases_path.read_text(encoding='utf-8'))['cases']
            polarity = next((row['polarity'] for row in cases if row['case_id'] == case_id))
            yield ((('QG167', 'Child.shadow', 'critical'),) if polarity == 'TP' else ())

    def equals(self, ground_truth, output):
        """Match only QG167 verdicts and their identified source field."""
        observed = tuple(((finding.code, finding.symbol, finding.severity) for finding in output if finding.code == 'QG167'))
        return observed == ground_truth
