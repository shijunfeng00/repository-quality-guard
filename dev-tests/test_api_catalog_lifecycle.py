from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime.src.api_catalog import search_api_catalog
from runtime.src.config import GuardConfig, is_tool_generated_path
from runtime.src.release_identity_rules import release_identity_findings
from runtime.src.report_contract import _changed_files
from runtime.src.workflow import _append_search_ledger


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


class TestApiCatalogLifecycle(unittest.TestCase):
    def test_doc_search_strongly_refreshes_preserved_stat_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _git(root, "init", "-q")
            source = root / "service.py"
            source.write_text(
                'def alpha():\n    """alpha capability"""\n    return "alpha"\n',
                encoding="utf-8",
            )
            output = root / "docs" / "api-reference"
            config = GuardConfig(include_tests=False)

            first, first_metrics = search_api_catalog(
                root, output, config, "alpha capability"
            )
            self.assertTrue(any(hit.record.symbol.endswith("alpha") for hit in first))
            self.assertEqual(first_metrics["refresh_mode"], "full")
            original = source.stat()

            source.write_text(
                'def bravo():\n    """bravo capability"""\n    return "bravo"\n',
                encoding="utf-8",
            )
            self.assertEqual(source.stat().st_size, original.st_size)
            os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))

            second, second_metrics = search_api_catalog(
                root, output, config, "bravo capability"
            )

            self.assertEqual(second_metrics["refresh_mode"], "auto-incremental")
            self.assertEqual(second_metrics["changed_files"], 1)
            self.assertGreaterEqual(second_metrics["hashed_files"], 1)
            self.assertNotEqual(
                first_metrics["source_digest"], second_metrics["source_digest"]
            )
            self.assertTrue(any(hit.record.symbol.endswith("bravo") for hit in second))
            self.assertFalse(any(hit.record.symbol.endswith("alpha") for hit in second))

    def test_doc_search_cli_refreshes_current_worktree_before_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _git(root, "init", "-q")
            source = root / "service.py"
            source.write_text(
                'def alpha():\n    """alpha capability"""\n    return "alpha"\n',
                encoding="utf-8",
            )
            output = root / "docs" / "api-reference"

            script = (
                Path(__file__).resolve().parents[1] / "scripts" / "quality_guard.py"
            )
            command = [
                sys.executable,
                str(script),
                "doc-search",
                str(root),
                "alpha capability",
                "--output",
                str(output),
            ]
            first_cli = subprocess.run(
                command, check=False, capture_output=True, text=True
            )
            self.assertEqual(first_cli.returncode, 0, first_cli.stderr)
            original = source.stat()
            source.write_text(
                'def bravo():\n    """bravo capability"""\n    return "bravo"\n',
                encoding="utf-8",
            )
            self.assertEqual(source.stat().st_size, original.st_size)
            os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))

            command[4] = "bravo capability"
            second_cli = subprocess.run(
                command, check=False, capture_output=True, text=True
            )
            self.assertEqual(second_cli.returncode, 0, second_cli.stderr)
            catalog = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
            symbols = {record["symbol"] for record in catalog["records"]}
            self.assertTrue(any(symbol.endswith("bravo") for symbol in symbols))
            self.assertFalse(any(symbol.endswith("alpha") for symbol in symbols))

    def test_search_ledger_is_scoped_to_current_source_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            _append_search_ledger(output, "old", [], {"source_digest": "old-digest"})
            _append_search_ledger(output, "new", [], {"source_digest": "new-digest"})

            ledger = output / "search-ledger.jsonl"
            rows = [
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["query"] for row in rows], ["new"])
            self.assertEqual(rows[0]["source_digest"], "new-digest")

    def test_generated_api_reference_is_not_a_candidate_change_or_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _git(root, "init", "-q")
            _git(root, "config", "user.name", "QG Test")
            _git(root, "config", "user.email", "qg@example.invalid")
            (root / "base.py").write_text("BASE = True\n", encoding="utf-8")
            _git(root, "add", ".")
            _git(root, "commit", "-qm", "base")

            generated = root / "docs" / "api-reference"
            generated.mkdir(parents=True)
            generated_digest = "a" * 64
            (generated / "INDEX.md").write_text(
                f"# Generated API Catalog\nSource digest: `{generated_digest}`\n",
                encoding="utf-8",
            )
            (root / "feature.py").write_text("TOKEN = 'v2.8-rc3'\n", encoding="utf-8")

            changed = _changed_files(root, "HEAD")
            self.assertEqual([item.path for item in changed], ["feature.py"])
            self.assertTrue(is_tool_generated_path("docs/api-reference/INDEX.md"))

            findings = release_identity_findings(root, GuardConfig())
            self.assertFalse(
                any(item.path.startswith("docs/api-reference/") for item in findings)
            )
            self.assertTrue(any(item.path == "feature.py" for item in findings))


if __name__ == "__main__":
    unittest.main()
