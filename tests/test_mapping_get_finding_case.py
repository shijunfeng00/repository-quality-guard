"""Independent observable behavior against the real production scanner."""
from __future__ import annotations
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner

class MappingGetFindingCase(TestCase):
    """Validate frozen production Finding contracts."""

    def get_enterpoint(self):
        """Execute the existing production scanner, without any test adapter."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build the original source fixture and pass an actual scanner instance."""
        for case_id in ('mapping_grandparent_and_self_dict_remain_detected', 'wrapper_get_does_not_hide_inner_mapping_fallback', 'optional_return_must_not_hide_required_schema_key', 'unknown_sentinel_still_detected_even_for_optional_result', 'empty_without_a_sparse_producer_remains_a_review_candidate', 'optional_return_does_not_hide_required_self_typed_dict_field', 'multiple_inheritance_with_ambiguous_get_is_not_proven_mapping', 'builtin_mapping_get_without_required_key_is_not_accepted', 'qg004-optional-absent-is-legitimate', 'qg003-typed-required-alias', 'qg003-typed-required-chain', 'qg003-typed-optional', 'qg003-typed-nontotal', 'qg003-typed-invalidated-alias', 'qg003-required-guarded', 'qg003-not-guarded', 'qg003-typed-source-reassigned', 'qg003-no-dominating-guard', 'qg004-optional-default', 'qg004-nontotal-required', 'qg003-notrequired-not-authenticated'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'mapping-get-contract.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-case-'))
            (root / 'sample.py').write_text(str(row['source']), encoding='utf-8')
            scanner = RepositoryScanner(root, GuardConfig())
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Read the independently frozen expected result."""
        for case_id in ('mapping_grandparent_and_self_dict_remain_detected', 'wrapper_get_does_not_hide_inner_mapping_fallback', 'optional_return_must_not_hide_required_schema_key', 'unknown_sentinel_still_detected_even_for_optional_result', 'empty_without_a_sparse_producer_remains_a_review_candidate', 'optional_return_does_not_hide_required_self_typed_dict_field', 'multiple_inheritance_with_ambiguous_get_is_not_proven_mapping', 'builtin_mapping_get_without_required_key_is_not_accepted', 'qg004-optional-absent-is-legitimate', 'qg003-typed-required-alias', 'qg003-typed-required-chain', 'qg003-typed-optional', 'qg003-typed-nontotal', 'qg003-typed-invalidated-alias', 'qg003-required-guarded', 'qg003-not-guarded', 'qg003-typed-source-reassigned', 'qg003-no-dominating-guard', 'qg004-optional-default', 'qg004-nontotal-required', 'qg003-notrequired-not-authenticated'):
            row = next((row for row in json.loads((Path(__file__).parent / 'cases' / 'mapping-get-contract.json').read_text(encoding='utf-8'))['cases'] if str(row['case_id']) == case_id))
            yield (row['must_contain'], row['must_omit'])

    def equals(self, ground_truth, output):
        """Judge actual production Findings, including required absence."""
        required, forbidden = ground_truth
        observed = {(f.code, f.line) for f in output.findings if f.code in {'QG003', 'QG004', 'QG026'}}
        return all((tuple(pair) in observed for pair in required)) and all((tuple(pair) not in observed for pair in forbidden))
