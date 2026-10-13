from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.src.cli import _profile_selection
from runtime.src.compact_report import render_compact_report
from runtime.src.model import ScanReport
from runtime.src.project_profiles import ProfileSelection


class TestProfileProvenance(unittest.TestCase):
    def _select(self, source: str, name: str = "fixture-profile") -> str:
        selection = ProfileSelection(
            reference=name if name else None,
            name=name,
            source=source,
            reason="test proven source",
        )
        output = io.StringIO()
        with patch("runtime.src.cli.resolve_profile_reference", return_value=selection):
            with contextlib.redirect_stdout(output):
                selected = _profile_selection(Path("/tmp/fixture"), None)
        self.assertEqual(selected, selection)
        return output.getvalue()

    def test_sealed_explicit_never_claims_generic(self) -> None:
        self.assertIn("source=sealed-installed-explicit", self._select("sealed-installed-explicit"))
        self.assertNotIn("generic", self._select("sealed-installed-explicit"))

    def test_all_authorized_sources_have_truthful_logs(self) -> None:
        for source in (
            "explicit-cli",
            "auto-directory-name",
            "sealed-installed",
            "sealed-installed-generic",
            "generic",
        ):
            with self.subTest(source=source):
                output = self._select(source, "" if source.endswith("generic") else "fixture-profile")
                self.assertIn(f"source={source}", output)

    def test_unknown_selection_source_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown Profile selection source"):
            self._select("unknown")

    def test_compact_report_persists_actual_profile_provenance(self) -> None:
        report = ScanReport(
            root=Path("/tmp/fixture"),
            files_scanned=0,
            findings=[],
            definitions=0,
            project_name="fixture-profile",
            profile_source="auto-directory-name",
        )
        rendered = render_compact_report(
            report, revision="", existing=None, debt_mode="progressive", audit_mode="normal"
        )
        self.assertIn("- Profile：`fixture-profile`", rendered)
        self.assertIn("- Profile 来源：`auto-directory-name`", rendered)
