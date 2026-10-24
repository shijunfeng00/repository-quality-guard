"""Public report output retains the selected Profile and its source identity."""
from __future__ import annotations


from pathlib import Path

from rqgtest import TestCase
from runtime.src.compact_report import render_compact_report
from runtime.src.model import ScanReport


class ProfileProvenanceCase(TestCase):
    """Verify the report exposes the actual Profile identity and origin."""

    def get_enterpoint(self):
        """Exercise the existing public report renderer directly."""
        return render_compact_report

    def get_input_data(self):
        """Provide a complete, explicitly attributed report input."""
        report = ScanReport(
            root=Path('/tmp/fixture'),
            files_scanned=0,
            findings=[],
            definitions=0,
            project_name='fixture-profile',
            profile_source='auto-directory-name',
        )
        return ((report,), {
            'revision': '',
            'existing': None,
            'debt_mode': 'progressive',
            'audit_mode': 'normal',
        })

    def get_output_data(self):
        """Freeze the public identity and source expected in the report."""
        return ('- Profile：`fixture-profile`', '- Profile 来源：`auto-directory-name`')

    def equals(self, ground_truth, output):
        """Require both independently specified report fields."""
        return all(field in output for field in ground_truth)
