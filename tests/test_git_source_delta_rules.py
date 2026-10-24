"""Public Git source-delta and semantic candidate rule correctness."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.fallback_laundering import fallback_laundering_findings
from runtime.src.semantic_heuristic_candidates import semantic_heuristic_candidate_findings

class QG176GitFallbackDeltaCase(TestCase):
    """Check fallback deletion and explicit-missing semantics via real Git deltas."""

    def get_enterpoint(self):
        """Return the real Git fallback rule public function."""
        return fallback_laundering_findings

    def get_input_data(self):
        """Prepare authored before/after source snapshots."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/git-source-delta-rule-cases.json')))['cases']:
            if row['rule'] == 'QG176':
                _case_arg_1_row = row
                root = Path(tempfile.mkdtemp(prefix='rqg-architecture-rule-'))
                atexit.register(shutil.rmtree, root, ignore_errors=True)
                subprocess.run(('git', 'init', '-q'), cwd=root, check=True)
                subprocess.run(('git', 'config', 'user.name', 'Fixture'), cwd=root, check=True)
                subprocess.run(('git', 'config', 'user.email', 'fixture@example.invalid'), cwd=root, check=True)
                code = _case_arg_1_row['rule']
                positive = _case_arg_1_row['kind'] == 'TP'
                path = root / ('src/store.py' if code == 'QG176' else 'fixture.py')
                path.parent.mkdir(parents=True, exist_ok=True)
                if code == 'QG176':
                    before = 'def f(data,key):\n return data.get(key, None)\n'
                    after = 'def f(data,key):\n return data[key] if key in data else None\n' if positive else 'def f(data,key):\n return data[key]\n'
                else:
                    before = '# immutable origin\n'
                    is_protocol = (code == 'QG191') == positive
                    after = 'payload = {"type": "function", "function": {"name": "search", "arguments": "{}"}}\n' if is_protocol else 'flag = value in {"red", "blue"}\n'
                path.write_text(before, encoding='utf-8')
                subprocess.run(('git', 'add', '.'), cwd=root, check=True)
                subprocess.run(('git', 'commit', '-qm', 'baseline'), cwd=root, check=True)
                path.write_text(after, encoding='utf-8')
                _case_value_1 = ((root, GuardConfig(), 'HEAD'), {})
                yield _case_value_1

    def get_output_data(self):
        """Retain original complete Finding identities from frozen examples."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/git-source-delta-rule-cases.json')))['cases']:
            if row['rule'] == 'QG176':
                yield tuple((tuple(item) for item in row['expected_all']))

    def equals(self, expected, actual):
        """Reject missing or unexpected Findings with exact source identity."""
        return expected == tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))

class QG178QG191GitCandidateCase(TestCase):
    """Distinguish handwritten tool protocols from literal membership heuristics."""

    def get_enterpoint(self):
        """Return the genuine public candidate classifier, not a private visitor."""
        return semantic_heuristic_candidate_findings

    def get_input_data(self):
        """Prepare independent Git added-line evidence for both candidate types."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/git-source-delta-rule-cases.json')))['cases']:
            if row['rule'] in ('QG178', 'QG191'):
                _case_arg_2_row = row
                root = Path(tempfile.mkdtemp(prefix='rqg-architecture-rule-'))
                atexit.register(shutil.rmtree, root, ignore_errors=True)
                subprocess.run(('git', 'init', '-q'), cwd=root, check=True)
                subprocess.run(('git', 'config', 'user.name', 'Fixture'), cwd=root, check=True)
                subprocess.run(('git', 'config', 'user.email', 'fixture@example.invalid'), cwd=root, check=True)
                code = _case_arg_2_row['rule']
                positive = _case_arg_2_row['kind'] == 'TP'
                path = root / ('src/store.py' if code == 'QG176' else 'fixture.py')
                path.parent.mkdir(parents=True, exist_ok=True)
                if code == 'QG176':
                    before = 'def f(data,key):\n return data.get(key, None)\n'
                    after = 'def f(data,key):\n return data[key] if key in data else None\n' if positive else 'def f(data,key):\n return data[key]\n'
                else:
                    before = '# immutable origin\n'
                    is_protocol = (code == 'QG191') == positive
                    after = 'payload = {"type": "function", "function": {"name": "search", "arguments": "{}"}}\n' if is_protocol else 'flag = value in {"red", "blue"}\n'
                path.write_text(before, encoding='utf-8')
                subprocess.run(('git', 'add', '.'), cwd=root, check=True)
                subprocess.run(('git', 'commit', '-qm', 'baseline'), cwd=root, check=True)
                path.write_text(after, encoding='utf-8')
                _case_value_2 = ((root, GuardConfig(), 'HEAD'), {})
                yield _case_value_2

    def get_output_data(self):
        """Require both emitted classes of Finding, not filtered positive-only rows."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/git-source-delta-rule-cases.json')))['cases']:
            if row['rule'] in ('QG178', 'QG191'):
                yield tuple((tuple(item) for item in row['expected_all']))

    def equals(self, expected, actual):
        """Compare the complete set including Findings belonging to the other rule."""
        return expected == tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))
