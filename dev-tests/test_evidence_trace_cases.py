"""Cases verify that repository evidence actually explains inputs and callers."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts import evidence_lookup

SOURCE = """from typing import TypedDict, NotRequired

class Payload(TypedDict):
    id: str
    note: NotRequired[str]

class Base:
    def get(self):
        return 42

class Plugin(Base):
    def get(self):
        return super().get()

def consume(identifier: str) -> str:
    return identifier

def normalize(payload: Payload) -> str:
    identifier = payload["id"]
    return consume(identifier)

def entry() -> str:
    return normalize({"id": "A"})
"""


class TestRepositoryEvidence(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        (self.root / "sample.py").write_text(SOURCE, encoding="utf-8")
        self.cache_dir = self.root / "../evidence-index"
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.cache_dir, ignore_errors=True)
        )

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = evidence_lookup.main(
                ["--repo", str(self.root), "--cache-dir", str(self.cache_dir), *extra]
            )
        return result, out.getvalue(), err.getvalue()

    def test_real_portable_cli_without_rule(self):
        """Real shell invocation works outside the source directory without --rule."""
        import subprocess
        import sys

        script = Path(evidence_lookup.__file__).resolve()
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.root),
                "--file",
                "sample.py",
                "--line",
                "15",
                "--name",
                "identifier",
                "--kind",
                "variable",
                "--cache-dir",
                str(self.cache_dir),
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sample.consume", result.stdout)
        self.assertIn("sample.normalize", result.stdout)

    def test_parameter_source_is_actual_upstream_business_case(self):
        """At id consumer, obtain assignment, actual call and legal producer input."""
        code, out, err = self.run_cli(
            "--file",
            "sample.py",
            "--line",
            "15",
            "--name",
            "identifier",
            "--kind",
            "variable",
            "--direction",
            "callers",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("sample.normalize", out)
        self.assertIn("`identifier`", out)
        self.assertIn("payload['id']", out)
        self.assertIn("`sample.py:20`", out)
        self.assertIn("`sample.py:19`", out)
        self.assertIn("`{'id': 'A'}`", out)
        self.assertIn("OPEN_WORLD", out)

    def test_method_is_not_dictionary_get_and_inheritance_available(self):
        """Resolve the method owner and user-authored inheritance, not the name get."""
        code, out, err = self.run_cli(
            "--file", "sample.py", "--line", "12", "--name", "Plugin", "--kind", "class"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("`sample.Plugin`", out)
        self.assertIn("Base", out)
        self.assertIn("inheritance", out)

    def test_real_rqg_sparse_index_source_is_visible(self):
        """Real QG004 source shows its valid zero-incoming producer and writer."""
        root = Path(evidence_lookup.__file__).resolve().parents[1]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = evidence_lookup.main(
                [
                    "--repo",
                    str(root),
                    "--file",
                    "runtime/src/topology_facts.py",
                    "--line",
                    "396",
                    "--name",
                    "_incoming_index",
                    "--kind",
                    "variable",
                    "--max-edges",
                    "4",
                    "--cache-dir",
                    str(self.cache_dir),
                ]
            )
        self.assertEqual(result, 0, err.getvalue())
        self.assertIn("incoming.setdefault(edge.target_id, [])", out.getvalue())
        self.assertIn("RepositoryTopology.__post_init__", out.getvalue())
        self.assertIn("MAY_DISPATCH, declared type", out.getvalue())
        self.assertIn(
            "runtime.src.call_chain_rules._incoming_direct_edges", out.getvalue()
        )
        self.assertIn("runtime/src/call_chain_rules.py:37", out.getvalue())

    def test_real_rqg_parameter_source_is_visible(self):
        """Real QG033 source identifies authored set producer, not just a type hint."""
        root = Path(evidence_lookup.__file__).resolve().parents[1]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = evidence_lookup.main(
                [
                    "--repo",
                    str(root),
                    "--symbol",
                    "runtime.src.contract_rules._calls_declared_registration_operation",
                    "--variable",
                    "operations",
                    "--depth",
                    "3",
                    "--max-edges",
                    "8",
                    "--cache-dir",
                    str(self.cache_dir),
                ]
            )
        self.assertEqual(result, 0, err.getvalue())
        self.assertIn("abstract_operations =", out.getvalue())
        self.assertIn("_verified_registration_guard", out.getvalue())
        self.assertIn("operations", out.getvalue())

    def test_named_local_in_auto_mode_and_nonexistent_variable(self):
        code, out, err = self.run_cli(
            "--file", "sample.py", "--line", "15", "--name", "identifier"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("`sample.py:19`", out)
        code, out, err = self.run_cli(
            "--file", "sample.py", "--line", "15", "--name", "invented"
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("变量不在选定函数", err)

    def test_snapshot_is_cached_but_source_change_invalidates_it(self):
        """A change preserving file size must not reuse stale graph facts."""
        args = (
            "--file",
            "sample.py",
            "--line",
            "15",
            "--name",
            "identifier",
            "--kind",
            "variable",
        )
        a = self.run_cli(*args)
        b = self.run_cli(*args)
        self.assertEqual(a[0], 0, a[2])
        self.assertIn("cache=MISS/REBUILT", a[1])
        self.assertIn("cache=HIT", b[1])
        path = self.root / "sample.py"
        path.write_text(SOURCE.replace("return 42", "return 43"), encoding="utf-8")
        c = self.run_cli(*args)
        self.assertEqual(c[0], 0, c[2])
        self.assertIn("cache=MISS/REBUILT", c[1])

    def test_ambiguous_location_or_outside_repo_fail_closed(self):
        code, out, err = self.run_cli("--file", "../untrusted.py", "--line", "5")
        self.assertEqual(code, 2)
        self.assertFalse(out)
        self.assertIn("必须位于指定仓库内", err)


if __name__ == "__main__":
    unittest.main()
