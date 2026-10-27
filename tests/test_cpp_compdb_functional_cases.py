"""Compile-database normalization tested through its published parser."""
from __future__ import annotations
import atexit
import json
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.cpp_build_contract import load_compilation_database

class CppCompdbFunctionalCase(TestCase):
    """Compare portable compiler command contracts with authored databases."""

    def get_enterpoint(self):
        """Invoke the production compile_commands.json loader directly."""
        return load_compilation_database

    def get_input_data(self):
        """Freeze a source snapshot with normalized synthetic build paths."""
        for case_id in ('b31-cpp-gcc-compile-flags', 'b31-cpp-malformed-arguments-fail-closed', 'b31-cpp-clang-command', 'b31-cpp-ccache-wrapper', 'b31-cpp-msvc-syntax-contract', 'b31-cpp-two-subproject-tu', 'b31-cpp-duplicate-conflict-no-guess', 'b31-cpp-revision-does-not-steal-worktree'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'cpp-compilation-database.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-cpp-compdb-'))
            atexit.register(shutil.rmtree, root, ignore_errors=True)
            for relative in row['sources']:
                source = root / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text('int main() { return 0; }\n', encoding='utf-8')
            for relative, entries in row['databases'].items():
                database = root / relative / 'compile_commands.json'
                database.parent.mkdir(parents=True, exist_ok=True)
                normalized = []
                for entry in entries:
                    normalized.append({key: [str(root / part[1:]) if part.startswith('@') else part for part in value] if isinstance(value, list) else str(root / value[1:]) if isinstance(value, str) and value.startswith('@') else value for key, value in entry.items()})
                database.write_text(json.dumps(normalized), encoding='utf-8')
            snapshot = directory_analysis_snapshot(root, GuardConfig())
            if 'label' in row:
                snapshot = replace(snapshot, label=str(row['label']))
            yield ((snapshot,), {})

    def get_output_data(self):
        """Load the authored compiler family, arguments and syntax command Oracle."""
        for case_id in ('b31-cpp-gcc-compile-flags', 'b31-cpp-malformed-arguments-fail-closed', 'b31-cpp-clang-command', 'b31-cpp-ccache-wrapper', 'b31-cpp-msvc-syntax-contract', 'b31-cpp-two-subproject-tu', 'b31-cpp-duplicate-conflict-no-guess', 'b31-cpp-revision-does-not-steal-worktree'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'cpp-compilation-database.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            yield (row['expected'], bool(row['assert_wrapper']))

    def equals(self, ground_truth, output):
        """Compare stable compiler properties, ignoring temporary root path identity."""
        expected, assert_wrapper = ground_truth
        observed = {}
        for relative, spec in output.items():
            command = spec.native_syntax_command()
            observed[relative] = {'family': spec.family.value, 'compiler': spec.compiler, 'flags': [flag for flag in spec.arguments if flag.startswith(('-D', '-std', '/std:'))], 'prefix': list(spec.arguments[:2]) if assert_wrapper else [], 'syntax_flag': '-fsyntax-only' if '-fsyntax-only' in command else '/Zs' if '/Zs' in command else '', 'no_object_output': 'main.o' not in command, 'no_compile_only': '-c' not in command}
        return observed == expected
