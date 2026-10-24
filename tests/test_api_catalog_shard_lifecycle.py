"""Public API catalog generation removes obsolete shards while retaining live records."""
from __future__ import annotations

import atexit
import json
from pathlib import Path
import shutil
import tempfile

from rqgtest import TestCase
from runtime.src.api_catalog import build_api_catalog
from runtime.src.config import GuardConfig


class ApiCatalogStaleShardCase(TestCase):
    """Regenerating the public catalog removes a stale package shard."""

    def get_enterpoint(self):
        """Use the exported catalog builder rather than a test-specific shim."""
        return build_api_catalog

    def get_input_data(self):
        """Prepare a real source tree and preexisting obsolete Markdown shards."""
        root = Path(tempfile.mkdtemp(prefix="rqg-api-shard-"))
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        source = root / "pkg" / "active.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            'def active() -> bool:\n    """Return the active package status."""\n'
            '    return True\n',
            encoding="utf-8",
        )
        output = root / "api_docs"
        output.mkdir()
        (output / "retired.md").write_text("stale package\n", encoding="utf-8")
        (output / "pkg.md").write_text("old package\n", encoding="utf-8")
        yield ((root, output, GuardConfig(require_docstrings=False)), {})

    def get_output_data(self):
        """Expect exactly one live symbol and regenerated output artifacts."""
        yield (1, "pkg.active.active")

    def equals(self, expected, actual):
        """Check materialized shards and index instead of a void return value."""
        symbol_count, symbol = expected
        if not isinstance(actual, dict) or actual.get("symbols") != symbol_count:
            return False
        output = Path(actual["output"])
        live = output / "pkg.md"
        index = output / "INDEX.md"
        catalog = output / "catalog.json"
        if (output / "retired.md").exists():
            return False
        if not (live.is_file() and index.is_file() and catalog.is_file()):
            return False
        payload = json.loads(catalog.read_text(encoding="utf-8"))
        return (
            symbol in live.read_text(encoding="utf-8")
            and "pkg" in index.read_text(encoding="utf-8")
            and [record["symbol"] for record in payload["records"]] == [symbol]
        )
