"""Public authenticated Case identity changes across isolated Git baselines."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.test_case_purpose_review import changed_case_purposes

class CasePurposeGitIdentityCase(TestCase):
    """Distinguish additions, modifications, removals, renames and spoofed roots."""

    def get_enterpoint(self):
        """Return true public Case purpose comparison, not the Git hook."""
        return changed_case_purposes

    def get_input_data(self):
        """Build isolated Git fixtures for the current declaration contract."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        _VALID = 'from rqgtest import TestCase\nclass BoundCase(TestCase):\n    def get_enterpoint(self): return int\n    def get_input_data(self): return ((1,), {})\n    def get_output_data(self): return 1\n    def equals(self, expected, actual): return expected == actual\n'
        for row in next(iter(JSONDataLoader('tests/cases/case-purpose-public-v02.json')))['cases']:
            _case_arg_1_row = row
            while True:
                root = Path(tempfile.mkdtemp(prefix='rqg-case-purpose-public-'))
                atexit.register(shutil.rmtree, root, ignore_errors=True)
                subprocess.run(('git', *('init', '-q')), cwd=root, check=True, capture_output=True)
                subprocess.run(('git', *('config', 'user.name', 'Test Fixture')), cwd=root, check=True, capture_output=True)
                subprocess.run(('git', *('config', 'user.email', 'fixture@example.invalid')), cwd=root, check=True, capture_output=True)
                (root / 'README.md').write_text('identity fixture\n', encoding='utf-8')
                source = root / 'tests/test_contract.py'
                source.parent.mkdir(parents=True, exist_ok=True)
                mode = _case_arg_1_row['mode']
                if mode in ('modify', 'rename', 'delete'):
                    source.write_text(_VALID, encoding='utf-8')
                subprocess.run(('git', *('add', '.')), cwd=root, check=True, capture_output=True)
                subprocess.run(('git', *('commit', '-qm', 'fixture baseline')), cwd=root, check=True, capture_output=True)
                if mode == 'rename':
                    subprocess.run(('git', *('mv', 'tests/test_contract.py', 'tests/test_renamed.py')), cwd=root, check=True, capture_output=True)
                elif mode == 'delete':
                    source.unlink()
                else:
                    written = {'modify': _VALID.replace('return 1\n', 'return 2\n'), 'add': _VALID, 'unrelated': 'from elsewhere import TestCase\nclass BoundCase(TestCase): pass\n', 'shadow': 'from rqgtest import TestCase\nTestCase = object\nclass BoundCase(TestCase): pass\n'}
                    source.write_text(written[mode], encoding='utf-8')
                if _case_arg_1_row['staged']:
                    subprocess.run(('git', *('add', '.')), cwd=root, check=True, capture_output=True)
                _case_value_1 = ((root, 'HEAD'), {'staged': _case_arg_1_row['staged']})
                break
                break
            yield _case_value_1

    def get_output_data(self):
        """Read independently authored file, class and change-kind expectations."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        _VALID = 'from rqgtest import TestCase\nclass BoundCase(TestCase):\n    def get_enterpoint(self): return int\n    def get_input_data(self): return ((1,), {})\n    def get_output_data(self): return 1\n    def equals(self, expected, actual): return expected == actual\n'
        for row in next(iter(JSONDataLoader('tests/cases/case-purpose-public-v02.json')))['cases']:
            yield tuple((tuple(item) for item in row['expected']))

    def equals(self, expected, actual):
        """Require the exact authenticated Case identity set, including empty sets."""
        return tuple(sorted(((item.path, item.case_name, item.kind) for item in actual))) == expected
