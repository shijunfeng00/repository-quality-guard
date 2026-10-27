"""Clang-backed C++ changes require proven QG204 complexity regressions."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.multilang import multilang_findings

class CppInvalidCompilerDeltaCase(TestCase):
    """Compare authentic baseline and target Clang evidence, including both failure directions."""

    def get_enterpoint(self):
        """Use the actual public cross-language quality scanner."""
        return multilang_findings

    def get_input_data(self):
        """Provide two independent real-Clang compiler database snapshots."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/cpp-semantic-compiler-delta.json')))['cases']:
            _case_arg_1_row = row
            compiler = shutil.which('clang++') or shutil.which('clang')
            if compiler is None:
                raise RuntimeError('C++ diagnostic contract requires a real Clang compiler')
            root = Path(tempfile.mkdtemp(prefix='rqg-b33-native-'))
            atexit.register(shutil.rmtree, root, ignore_errors=True)
            path = root / 'src/sample.cpp'
            path.parent.mkdir(parents=True)
            path.write_text(_case_arg_1_row['target'], encoding='utf-8')
            build = root / 'build'
            build.mkdir()
            (build / 'compile_commands.json').write_text(json.dumps([{'directory': str(build), 'file': str(path), 'arguments': [compiler, '-std=c++20', '-c', str(path)]}]), encoding='utf-8')
            relative = 'src/sample.cpp'
            base = RepositoryAnalysisSnapshot(root, 'BASE_REVISION', {}, {relative: LanguageUnit(relative, 'cpp', _case_arg_1_row['base'])})
            target = RepositoryAnalysisSnapshot(root, 'DIRECTORY', {}, {relative: LanguageUnit(relative, 'cpp', _case_arg_1_row['target'])})
            config = GuardConfig(max_function_lines=1, max_branches=0, max_nesting=0)
            _case_value_1 = ((target, config), {'base': base})
            yield _case_value_1

    def get_output_data(self):
        """Retain independently authored no-fabricated-delta QG204 expectations."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/cpp-semantic-compiler-delta.json')))['cases']:
            yield tuple(row['expected_codes'])

    def equals(self, expected, actual):
        """Reject fabricated QG204 Findings when source compilation is unavailable."""
        findings, summary = actual
        changed = tuple((item.code for item in findings if item.code == 'QG204'))
        return changed == expected
