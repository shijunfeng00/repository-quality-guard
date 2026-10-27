"""Contract tests for explicit data-only directories and optional loader modules."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "src"

CASE = """from rqgtest import TestCase
from ops import identity
from dataloaders.custom import CustomLoader
class SourceCase(TestCase):
    def get_enterpoint(self): return identity
    def get_input_data(self):
        for item in CustomLoader("tests/cases/sample.jsonl"):
            yield ((item["x"],), {})
    def get_output_data(self):
        for item in CustomLoader("tests/cases/sample.jsonl"):
            yield item["x"]
    def equals(self, expected, actual): return expected == actual
"""
LOADER = """from rqgtest import JSONLDataLoader
class CustomLoader(JSONLDataLoader):
    pass
"""


class LayoutContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rqgtest-data-layout-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("tests/cases", "tests/dataloaders", "tests/sub"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        (self.root / "ops.py").write_text("def identity(x): return x\n")
        (self.root / "tests/sub/test_source.py").write_text(CASE)
        (self.root / "tests/dataloaders/custom.py").write_text(LOADER)
        (self.root / "tests/cases/sample.jsonl").write_text('{"x": 1}\n{"x": 3}\n')
        (self.root / "tests/cases/broken.py").write_text("not valid Python ####?\n")

    def run_cli(self, *args):
        env = dict(os.environ, PYTHONPATH=f"{SOURCE}{os.pathsep}{self.root}")
        return subprocess.run(
            [sys.executable, "-m", "rqgtest.run", *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )

    def test_full_discovery_with_one_loader_and_data_fixtures(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2/2 PASS", result.stdout)

    def test_selected_file_still_works(self):
        result = self.run_cli("--file", "tests/sub/test_source.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2/2 PASS", result.stdout)

    def test_freestanding_loader_helpers_fail_full_validation(self):
        loader = self.root / "tests/dataloaders/custom.py"
        loader.write_text(LOADER + "\ndef make_oracle(): return 1\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("only imports and one class", result.stderr)

    def test_multiple_loader_classes_fail_full_validation(self):
        loader = self.root / "tests/dataloaders/custom.py"
        loader.write_text(LOADER + "\nclass OtherLoader(JSONLDataLoader):\n    pass\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Exactly one DataLoader subclass", result.stderr)

    def test_one_local_dataloader_may_share_test_module_with_case(self):
        source = self.root / "tests/sub/test_source.py"
        source.write_text("""from rqgtest import TestCase, JSONLDataLoader
from ops import identity
class LocalLoader(JSONLDataLoader):
    pass
class LocalCase(TestCase):
    def get_enterpoint(self): return identity
    def get_input_data(self):
        for row in LocalLoader('tests/cases/sample.jsonl'):
            yield ((row['x'],), {})
    def get_output_data(self):
        for row in LocalLoader('tests/cases/sample.jsonl'):
            yield row['x']
    def equals(self, expected, actual): return expected == actual
""")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2/2 PASS", result.stdout)

    def test_loader_only_module_is_allowed_as_non_executable(self):
        (self.root / "tests/test_data_loader.py").write_text(LOADER)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2/2 PASS", result.stdout)

    def test_non_loader_class_fails_full_validation(self):
        loader = self.root / "tests/dataloaders/custom.py"
        loader.write_text("class OtherThing(object):\n    pass\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("must directly extend", result.stderr)

    def test_unknown_helpers_under_tests_still_fail(self):
        (self.root / "tests/helper.py").write_text("helper = 123\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Non-test Python module", result.stderr)


if __name__ == "__main__":
    unittest.main()
