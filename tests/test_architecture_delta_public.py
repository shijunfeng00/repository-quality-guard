"""Public architecture delta regression evidence for QG162–167."""
from __future__ import annotations
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.architecture_diff import compare_git_architecture

class ArchitectureDeltaPublicCase(TestCase):
    """Validate original Git-source deltas via a real production entrypoint."""

    def get_enterpoint(self):
        """Return the existing public architecture delta comparison function."""
        return compare_git_architecture

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/architecture-delta-input-oracle.pkl'):
            if record['case'] == 'ArchitectureDeltaPublicCase':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/architecture-delta-input-oracle.pkl'):
            if record['case'] == 'ArchitectureDeltaPublicCase':
                yield record['expected']

    def equals(self, expected, actual):
        """Forbid missing, additional, or mislocated cross-rule findings."""
        return expected == tuple(((finding.code, finding.severity, finding.path, finding.symbol) for finding in actual))
