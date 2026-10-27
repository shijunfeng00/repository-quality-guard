"""C++ provider absence must not fabricate typed relations or diagnostics."""
from __future__ import annotations
import json
from pathlib import Path

from rqgtest import TestCase
from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
from runtime.src.cpp_topology import cpp_topology


class CppUnavailableProviderCase(TestCase):
    """Do not invent C++ symbols or edges without native provider evidence."""

    def get_enterpoint(self):
        """Return real C++ topology owner."""
        return cpp_topology

    def get_input_data(self):
        """Create snapshots using the original no-compiler source program."""
        rows = json.loads((Path(__file__).parent / 'cases' / 'native-missing-compiler.json').read_text(encoding='utf-8'))['cases']
        for row in rows:
            if row['kind'] != 'topology':
                continue
            path = 'src/sample.cpp'
            snapshot = RepositoryAnalysisSnapshot(
                Path('/repo'), 'DIRECTORY', {},
                {path: LanguageUnit(path, 'cpp', row['source'])},
            )
            yield ((snapshot, [path], None), {})

    def get_output_data(self):
        """Keep exact authored zero-fact oracle."""
        rows = json.loads((Path(__file__).parent / 'cases' / 'native-missing-compiler.json').read_text(encoding='utf-8'))['cases']
        for row in rows:
            if row['kind'] == 'topology':
                yield row['expected']

    def equals(self, expected, actual):
        """Verify no fabricated source-bound C++ facts."""
        return [len(actual.symbols), len(actual.edges)] == expected
