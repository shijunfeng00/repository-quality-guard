"""Portable C++ coverage evidence from real authored translation units."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.compact_report import render_compact_report
from runtime.src.config import GuardConfig
from runtime.src.gate_status import code_status
from runtime.src.model import ScanReport
from runtime.src.multilang import multilang_findings

class CppCoverageFunctionalCase(TestCase):
    """Compare native build coverage, public findings and compact diagnostics."""

    def get_enterpoint(self):
        """Bind the actual multi-language native-coverage producer."""
        return multilang_findings

    def get_input_data(self):
        """Prepare real source files and an optional authentic compilation database."""
        for case_id in ('b32-cpp-missing-three', 'b32-cpp-missing-report', 'b32-cpp-vendor-header-only', 'b32-cpp-partial-compdb', 'b32-cpp-complete-compdb', 'b32-cpp-unavailable-compiler'):
            rows = json.loads((Path(__file__).parent / 'cases/cpp-coverage.json').read_text(encoding='utf-8'))['cases']
            row = next((candidate for candidate in rows if candidate['case_id'] == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-b32-cpp-coverage-'))
            weakref.finalize(self, shutil.rmtree, root, ignore_errors=True)
            for relative, body in row['sources'].items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding='utf-8')
            if 'compdb' in row:
                entries = []
                for entry in row['compdb']:
                    entries.append({'directory': str(root), 'file': str(root / entry['file']), 'arguments': [str(root / item[1:]) if item.startswith('@') else item for item in entry['arguments']]})
                (root / 'compile_commands.json').write_text(json.dumps(entries), encoding='utf-8')
            yield ((directory_analysis_snapshot(root, GuardConfig()), GuardConfig()), {})

    def get_output_data(self):
        """Read the external C++ coverage expectations, not any scanner result."""
        for case_id in ('b32-cpp-missing-three', 'b32-cpp-missing-report', 'b32-cpp-vendor-header-only', 'b32-cpp-partial-compdb', 'b32-cpp-complete-compdb', 'b32-cpp-unavailable-compiler'):
            rows = json.loads((Path(__file__).parent / 'cases/cpp-coverage.json').read_text(encoding='utf-8'))['cases']
            yield next((row['expected'] for row in rows if row['case_id'] == case_id))

    def equals(self, ground_truth, output):
        """Verify exact findings, summary and the public compact report section."""
        findings, summary = output
        missing = [finding for finding in findings if finding.code == 'QG208']
        report = ScanReport(Path('/fixture'), 0, findings, 0)
        document = render_compact_report(report, revision='')
        native = document.split('<!-- RQG:AUTO:BEGIN native_cpp_diagnostics -->', 1)[1].split('<!-- RQG:AUTO:END native_cpp_diagnostics -->', 1)[0]
        observed = {'missing_count': len(missing), 'missing_paths': [list(f.evidence['missing_paths'] if f.evidence['capability'] == 'MISSING_AUTHORITATIVE_CPP_TU_CONTRACT' else ()) for f in missing], 'missing_severity': [f.severity for f in missing], 'translation_units': summary['cpp_translation_units'], 'missing_native': summary['cpp_missing_native_build_contracts'], 'coverage': summary['cpp_semantic_coverage'], 'unavailable': summary['cpp_native_unavailable'], 'build_contracts': summary['cpp_build_contracts'], 'status': code_status(report), 'native_section': {'has_coverage_gap': '| `QG208` |' in native, 'not_measured': 'NOT_MEASURED' in native, 'named_first': 'src/first.cpp' in native, 'two_units': '2 translation units' in native}}
        return observed == ground_truth
