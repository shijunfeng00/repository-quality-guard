"""Independent relation-edge oracles at the production topology boundary."""
from __future__ import annotations
import ast
import json
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology

class GraphObservableCase(TestCase):
    """Verify normalized source-bound Graph edges including proven non-edges."""

    def get_enterpoint(self):
        """Use production topology normalization to observe bound edges."""
        return normalized_topology

    def get_input_data(self):
        """Construct source graphs from independent archived behavior fixtures."""
        for case_id in ('graph-real-constructor-argument-reaches-field-and-method-call', 'graph-argument-alias-chain-preserves-known-receiver-type', 'graph-conflicting-injected-types-do-not-invent-unique-target', 'graph-unknown-injected-parameter-is-not-resolved-by-method-name', 'graph-mutated-alias-never-receives-stale-class-binding', 'graph-grandparent-method-resolves-but-ambiguous-diamond-does-not-guess', 'graph-invalid-duplicate-constructor-argument-never-fabricates-dispatch', 'graph-invalid-positional-only-keyword-never-fabricates-dispatch', 'graph-invalid-extra-constructor-keyword-never-fabricates-dispatch', 'graph-valid-keyword-only-constructor-binding-preserves-dispatch', 'graph-missing-required-constructor-parameter-never-fabricates-dispatch', 'graph-optional-constructor-parameter-does-not-block-valid-dispatch', 'graph-service-bound-receiver-and-no-guess', 'graph-package-search-unique-owner', 'graph-package-search-ambiguous-owner', 'b64-graph-dfs-public-hidden-state-and-recursion', 'b64-graph-fhq-insert-split-merge-recursive'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'graph-observable-edges.json').read_text(encoding='utf-8'))['cases'] if row['case_id'] == case_id))
            snapshot = RepositoryAnalysisSnapshot(None, 'DIRECTORY', {path: PythonUnit(path, source, ast.parse(source)) for path, source in row['files'].items()})
            yield ((RepositoryRelationGraph(snapshot),), {})

    def get_output_data(self):
        """Read frozen required edges and prohibited guessed edges."""
        for case_id in ('graph-real-constructor-argument-reaches-field-and-method-call', 'graph-argument-alias-chain-preserves-known-receiver-type', 'graph-conflicting-injected-types-do-not-invent-unique-target', 'graph-unknown-injected-parameter-is-not-resolved-by-method-name', 'graph-mutated-alias-never-receives-stale-class-binding', 'graph-grandparent-method-resolves-but-ambiguous-diamond-does-not-guess', 'graph-invalid-duplicate-constructor-argument-never-fabricates-dispatch', 'graph-invalid-positional-only-keyword-never-fabricates-dispatch', 'graph-invalid-extra-constructor-keyword-never-fabricates-dispatch', 'graph-valid-keyword-only-constructor-binding-preserves-dispatch', 'graph-missing-required-constructor-parameter-never-fabricates-dispatch', 'graph-optional-constructor-parameter-does-not-block-valid-dispatch', 'graph-service-bound-receiver-and-no-guess', 'graph-package-search-unique-owner', 'graph-package-search-ambiguous-owner', 'b64-graph-dfs-public-hidden-state-and-recursion', 'b64-graph-fhq-insert-split-merge-recursive'):
            yield next((row for row in json.loads((Path(__file__).parent / 'cases' / 'graph-observable-edges.json').read_text(encoding='utf-8'))['cases'] if row['case_id'] == case_id))

    def equals(self, ground_truth, output):
        """Require the expected edges to exist and contradictory edges to be absent."""
        observed = tuple(({'kind': edge.kind.name, 'source': edge.source_id, 'target': edge.target_id, 'path': edge.path.as_posix(), 'line': edge.line} for edge in output.edges))
        return all((any((all((value is None or key in actual and actual[key] == value for key, value in wanted.items())) for actual in observed)) for wanted in ground_truth['include'])) and all((not any((all((value is None or key in actual and actual[key] == value for key, value in wanted.items())) for actual in observed)) for wanted in ground_truth['absent']))
