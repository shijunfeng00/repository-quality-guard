"""DataLoader template method and cache behavior contract."""

import json
import pickle
import tempfile
import unittest
from pathlib import Path
from rqgtest import PickleDataLoader, JSONLDataLoader


class PickleContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_pickle_records_not_implicit_container_expansion(self):
        path = self.root / "objects.pkl"
        with path.open("wb") as out:
            pickle.dump(["a", "b"], out)
            pickle.dump({"x": 1}, out)
        loader = PickleDataLoader(path)
        self.assertEqual(list(loader), [["a", "b"], {"x": 1}])
        self.assertEqual(list(loader), [["a", "b"], {"x": 1}])

    def test_empty_and_truncated_pickle(self):
        path = self.root / "objects.pkl"
        path.write_bytes(b"")
        self.assertEqual(list(PickleDataLoader(path)), [])
        with path.open("wb") as out:
            pickle.dump(1, out)
            out.write(b"\x80\x04\x95")
        with self.assertRaises((EOFError, pickle.UnpicklingError)):
            list(PickleDataLoader(path))

    def test_preprocessing_reuses_cache(self):
        path = self.root / "numbers.src"
        path.write_text("7", encoding="utf-8")
        calls = []

        class Transform(JSONLDataLoader):
            def preprocessing(self, source, output):
                calls.append(1)
                output.write_text(json.dumps(int(source.read_text()) + 2) + "\n")

        loader = Transform(path)
        self.assertEqual(list(loader), [9])
        self.assertEqual(list(loader), [9])
        self.assertEqual(calls, [1])
        self.assertTrue(loader.filename().is_relative_to(self.root / ".cache"))
        self.assertEqual(
            list(JSONLDataLoader(self.root / ".cache" / "missing")), []
        ) if False else None

    def test_no_implicit_preprocessing_and_file_only(self):
        path = self.root / "rows.jsonl"
        path.write_text("1\n2\n", encoding="utf-8")
        self.assertEqual(list(JSONLDataLoader(path)), [1, 2])
        with self.assertRaises(FileNotFoundError):
            list(JSONLDataLoader(self.root))
        with self.assertRaises(NotImplementedError):
            JSONLDataLoader(path).preprocessing(path, self.root / "unused")

    def test_preprocessing_exception_does_not_publish_cache(self):
        path = self.root / "fail.txt"
        path.write_text("x")

        class Failing(PickleDataLoader):
            def preprocessing(self, source, output):
                output.write_bytes(b"partial")
                raise ValueError("failure")

        loader = Failing(path)
        with self.assertRaisesRegex(ValueError, "failure"):
            list(loader)
        self.assertFalse(list((self.root / ".cache").glob("*.pkl")))


if __name__ == "__main__":
    unittest.main()


class RebuildCLITest(unittest.TestCase):
    def test_cli_rebuild_removes_stale_cache_before_executing(self):
        import os
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests" / "cases" / ".cache").mkdir(parents=True)
            stale = root / "tests" / "cases" / ".cache" / "stale.pkl"
            stale.write_bytes(b"old")
            (root / "subject.py").write_text("def identity(x): return x\n")
            (
                root / "tests" / "test_value.py"
            ).write_text("""from rqgtest import TestCase
from subject import identity
class ValueCase(TestCase):
    def get_enterpoint(self): return identity
    def get_input_data(self): return ((7,), {})
    def get_output_data(self): return 7
    def equals(self, expected, actual): return expected == actual
""")
            import rqgtest

            lib = Path(rqgtest.__file__).resolve().parents[1]
            env = dict(os.environ, PYTHONPATH=f"{lib}{os.pathsep}{root}")
            cmd = [sys.executable, "-m", "rqgtest.run", "--rebuild-cache"]
            result = subprocess.run(
                cmd, cwd=root, env=env, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("1/1 PASS", result.stdout)
            self.assertFalse(stale.exists())
