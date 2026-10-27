"""End-to-end CLI regressions: selection, strict full scans, shared pool."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'src'

class CLIFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rqgtest-cli-')
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / 'tests').mkdir()
        (self.project / 'ops.py').write_text(
            'def identity(value):\n    return value\n', encoding='utf-8')
        (self.project / 'tests/test_good.py').write_text('''from rqgtest import TestCase
from ops import identity
class IdentityCase(TestCase):
    def get_enterpoint(self): return identity
    def get_input_data(self): return ((42,), {})
    def get_output_data(self): return 42
    def equals(self, expected, actual): return expected == actual
''', encoding='utf-8')

    def cli(self, *args, force_spawn=False):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(SOURCE) + os.pathsep + str(self.project)
        if force_spawn:
            # For portability checks, run the actual CLI with spawn workers.
            # The targeted and full scan must have the same meaning on Windows.
            command = [sys.executable, '-c',
                       'import importlib, multiprocessing as m;'
                       'real=m.get_context; m.get_context=lambda _:real("spawn");'
                       'r=importlib.import_module("rqgtest.run");'
                       'raise SystemExit(r.main())']
        else:
            command = [sys.executable, '-m', 'rqgtest.run']
        return subprocess.run([*command, *args], cwd=self.project,
                              env=env, capture_output=True, text=True, timeout=25)

    def test_target_file_ignores_unrelated_invalid_module(self):
        (self.project / 'tests/helper.py').write_text('THIS = 1\n', encoding='utf-8')
        (self.project / 'tests/test_bad.py').write_text('invalid syntax ###?\n', encoding='utf-8')
        good = self.cli('--file', 'tests/test_good.py')
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn('1/1 PASS', good.stdout)
        selected_case = self.cli('--case', 'tests/test_good.py::IdentityCase')
        self.assertEqual(selected_case.returncode, 0, selected_case.stderr)
        full = self.cli()
        self.assertEqual(full.returncode, 2, full.stderr)
        self.assertIn('Non-test Python module', full.stderr)

    def test_bad_selected_module_fails_instead_of_zero_pass(self):
        (self.project / 'tests/test_invalid.py').write_text('invalid syntax ###?\n', encoding='utf-8')
        bad = self.cli('--file', 'tests/test_invalid.py')
        self.assertEqual(bad.returncode, 2)
        self.assertIn('SyntaxError', bad.stderr)
        empty = self.cli('--case', 'tests/test_good.py::MissingCase')
        self.assertEqual(empty.returncode, 2)
        self.assertIn('No matching TestCase', empty.stderr)
        missing = self.cli('--file', 'tests/test_not_here.py')
        self.assertEqual(missing.returncode, 2)

    def test_spawn_target_does_not_reload_invalid_unrelated_tests(self):
        (self.project / 'tests/helper.py').write_text('THIS = 1\n', encoding='utf-8')
        result = self.cli('--file', 'tests/test_good.py', force_spawn=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1/1 PASS', result.stdout)

    def test_partial_oracle_failure_preserves_prior_samples(self):
        (self.project / 'tests/test_good.py').write_text('''from rqgtest import TestCase
from ops import identity
class InterruptedCase(TestCase):
    def get_enterpoint(self): return identity
    def get_input_data(self):
        yield ((4,), {})
        yield ((5,), {})
    def get_output_data(self):
        yield 4
        raise RuntimeError("oracle stream interrupted")
    def equals(self, expected, actual): return expected == actual
''', encoding='utf-8')
        result = self.cli('--file', 'tests/test_good.py', '--report', 'interrupted.json')
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads((self.project / 'interrupted.json').read_text())
        self.assertEqual((report['passed'], report['total']), (1, 2))
        self.assertEqual([x['status'] for x in report['results']], ['PASS', 'INVALID'])
        self.assertIn('oracle stream interrupted', report['invalid'])

    def test_one_process_per_case_not_per_sample(self):
        (self.project / 'ops.py').write_text('''import os
from pathlib import Path

def record(value):
    with Path("observed-pids.txt").open("a", encoding="ascii") as fp:
        fp.write(f"{os.getpid()}:{value}\\n")
    return value
''', encoding='utf-8')
        (self.project / 'tests/test_good.py').write_text('''from rqgtest import TestCase
from ops import record
class BulkCase(TestCase):
    def get_enterpoint(self): return record
    def get_input_data(self):
        for i in range(30): yield ((i,), {})
    def get_output_data(self):
        for i in range(30): yield i
    def equals(self, expected, actual): return expected == actual
''', encoding='utf-8')
        result = self.cli('--report', 'result.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('30/30 PASS', result.stdout)
        pid_lines = (self.project / 'observed-pids.txt').read_text().splitlines()
        self.assertEqual(len(pid_lines), 30)
        self.assertEqual(len({line.split(':')[0] for line in pid_lines}), 1)
        report = json.loads((self.project / 'result.json').read_text())
        self.assertEqual((report['passed'], report['total']), (30, 30))

if __name__ == '__main__':
    unittest.main()

class FilesSelectionRegression(unittest.TestCase):
    cli = CLIFeatures.cli
    """Exercise multi-file selection against the real CLI and Wheel API."""

    def setUp(self):
        CLIFeatures.setUp(self)
        (self.project / 'tests/test_other.py').write_text('''from rqgtest import TestCase
from ops import identity
class OtherCase(TestCase):
    def get_enterpoint(self): return identity
    def get_input_data(self): return ((24,), {})
    def get_output_data(self): return 24
    def equals(self, expected, actual): return expected == actual
''', encoding='utf-8')
        (self.project / 'tests/test_corrupt.py').write_text('this is not valid python ####?\n', encoding='utf-8')

    def test_files_targets_only_selected_modules(self):
        result = self.cli('--files', 'tests/test_good.py', 'tests/test_other.py',
                          '--report', 'report.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2/2 PASS', result.stdout)
        report = json.loads((self.project/'report.json').read_text())
        self.assertEqual(len(report['case_sha256']), 2)

    def test_files_with_bad_selected_module_fails(self):
        result = self.cli('--files', 'tests/test_good.py', 'tests/test_corrupt.py')
        self.assertEqual(result.returncode, 2)
        self.assertIn('SyntaxError', result.stderr)

    def test_files_rejects_duplicate_and_escape(self):
        duplicate = self.cli('--files', 'tests/test_good.py', 'tests/test_good.py')
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn('Duplicate selected', duplicate.stderr)
        escaped = self.cli('--files', '../outside/test_unknown.py')
        self.assertEqual(escaped.returncode, 2)

    def test_files_conflict_with_case_path_and_file(self):
        mismatch = self.cli('--files', 'tests/test_other.py',
                            '--case', 'tests/test_good.py::IdentityCase')
        self.assertEqual(mismatch.returncode, 2)
        self.assertIn('must select the same', mismatch.stderr)
        conflict = self.cli('--file', 'tests/test_good.py',
                            '--files', 'tests/test_other.py')
        self.assertNotEqual(conflict.returncode, 0)

    def test_files_case_subset(self):
        result = self.cli('--files', 'tests/test_good.py', 'tests/test_other.py',
                          '--case', 'tests/test_good.py::IdentityCase')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1/1 PASS', result.stdout)

    def test_files_spawn_subset(self):
        result = self.cli('--files', 'tests/test_good.py', 'tests/test_other.py',
                          force_spawn=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2/2 PASS', result.stdout)

    def test_selected_package_marker_is_not_ignored(self):
        (self.project/'tests/__init__.py').write_text('danger = True\n', encoding='utf-8')
        result = self.cli('--files', 'tests/test_good.py')
        self.assertEqual(result.returncode, 2)
        self.assertIn('package marker', result.stderr)
