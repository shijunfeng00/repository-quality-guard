"""Production scanner behavior for documented abstract and inherited overrides."""
from __future__ import annotations
import ast
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.config import GuardConfig
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.scanner import RepositoryScanner

class InheritedDocumentationCase(TestCase):
    """Observe the real scanner's documentation findings and eligible count."""

    def get_enterpoint(self):
        """Call the public scanner rather than a test-local orchestration wrapper."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build a snapshot and graph from authored inheritance source."""
        for case_id in ('QG027-DOC-INHERIT-single-abstract-override', 'QG027-DOC-INHERIT-multiple-abstract-with-unrelated-first', 'QG027-DOC-INHERIT-multiple-first-undocumented-shadows', 'QG027-DOC-INHERIT-same-name-without-inheritance', 'QG027-DOC-INHERIT-concrete-parent-no-abstract-marker', 'QG027-DOC-INHERIT-incomplete-abstract-parent-doc', 'QG027-DOC-INHERIT-signature-drift', 'QG027-DOC-INHERIT-local-incomplete-doc-keeps-QG028', 'QG027-DOC-INHERIT-unresolved-leading-base-no-exemption', 'QG027-DOC-INHERIT-multiple-first-contract-takes-precedence'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'qg027-inherited-documentation-v1.json').read_text(encoding='utf-8'))['cases']
            row = next((entry for entry in rows if entry['case_id'] == case_id))
            source = str(row['source'])
            root = Path(tempfile.mkdtemp(prefix='rqg-doc-override-'))
            (root / 'fixture.py').write_text(source, encoding='utf-8')
            snapshot = RepositoryAnalysisSnapshot(root, 'DIRECTORY', {'fixture.py': PythonUnit('fixture.py', source, ast.parse(source))})
            topology = normalized_topology(RepositoryRelationGraph(snapshot))
            scanner = RepositoryScanner(root, GuardConfig(), analysis_snapshot=snapshot, topology=topology)
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Read the original independent per-fixture documentation oracle."""
        for case_id in ('QG027-DOC-INHERIT-single-abstract-override', 'QG027-DOC-INHERIT-multiple-abstract-with-unrelated-first', 'QG027-DOC-INHERIT-multiple-first-undocumented-shadows', 'QG027-DOC-INHERIT-same-name-without-inheritance', 'QG027-DOC-INHERIT-concrete-parent-no-abstract-marker', 'QG027-DOC-INHERIT-incomplete-abstract-parent-doc', 'QG027-DOC-INHERIT-signature-drift', 'QG027-DOC-INHERIT-local-incomplete-doc-keeps-QG028', 'QG027-DOC-INHERIT-unresolved-leading-base-no-exemption', 'QG027-DOC-INHERIT-multiple-first-contract-takes-precedence'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'qg027-inherited-documentation-v1.json').read_text(encoding='utf-8'))['cases']
            row = next((entry for entry in rows if entry['case_id'] == case_id))
            yield row['expected']

    def equals(self, ground_truth, output):
        """Compare missing/short findings and true documentation denominator."""
        observed = {'missing': any((f.code == 'QG027' and f.symbol == 'fixture.Child.go' for f in output.findings)), 'short': any((f.code == 'QG028' and f.symbol == 'fixture.Child.go' for f in output.findings)), 'eligible': output.docstring_definitions}
        return ground_truth == observed
