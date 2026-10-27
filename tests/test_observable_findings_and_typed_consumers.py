"""Source-bound QG and Graph behavior checked without test-owned execution adapters."""
from __future__ import annotations
import ast
import json
import shutil
import tempfile
import weakref
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot, directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.scanner import RepositoryScanner

class MultiFileFindingCase(TestCase):
    """Validate ordered code/path/line/evidence from a real multi-file scanner."""

    def get_enterpoint(self):
        """Use the production scanner's public invocation protocol."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build the authored multi-file input and source-bound QG209 graph when needed."""
        for case_id in ('b25-qg149-private_class_reexport_through_init_is_critical', 'b25-qg149-private_module_reexport_through_init_is_critical', 'b25-qg149-public_import_through_init_is_not_private', 'b25-qg149-parent_private_member_is_forbidden_via_self_or_super', 'b25-qg149-same_class_private_and_explicit_private_override_are_allowed', 'b25-qg149-multilevel_inheritance_private_member_is_blocked', 'b25-qg149-typed_other_instance_of_same_class_is_legal', 'b25-qg149-untyped_other_instance_is_not_assumed_same_owner', 'b25-qg149-inherited_private_field_not_method_only', 'b25-qg149-private_module_attribute_remains_blocked', 'b25-qg149-tests_are_warning_not_critical', 'b25-qg149-dunder_private_export_is_critical', 'b25-qg149-module_private_class_cannot_be_publicly_aliased', 'b25-qg149-dunder_protocol_is_not_private', 'b25-qg209-unique_module_function_without_optional_compiler', 'b25-qg209-direct_local_missing_required_argument_and_default_positive', 'b25-qg209-decorator_rebinding_shadow_and_dynamic_star_remain_unknown', 'b25-qg209-import_shadowed_inside_nested_function_not_certified', 'b25-qg209-no_source_proof_from_kwargs_or_methods', 'b70-qg209-cross-file-two-import-forms', 'b70-qg209-dynamic-module-getattr-open-world'):
            row = next((item for item in json.loads((Path(__file__).parent / 'cases' / 'observable-findings-and-typed-consumers.json').read_text(encoding='utf-8'))['cases'] if item['case_id'] == case_id))
            root = Path(tempfile.mkdtemp(prefix='rqg-functional-b25-'))
            for name, source in row['sources'].items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding='utf-8')
            config = GuardConfig(include_tests=True, require_docstrings=False, require_docstring_sections=False)
            if row['rule'] == 'QG209':
                snapshot = directory_analysis_snapshot(root, config)
                graph = normalized_topology(RepositoryRelationGraph(snapshot))
                scanner = RepositoryScanner(root, config, snapshot, graph)
            else:
                scanner = RepositoryScanner(root, config)
            weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
            yield ((scanner,), {})

    def get_output_data(self):
        """Read the frozen rule-specific Finding witness fields."""
        for case_id in ('b25-qg149-private_class_reexport_through_init_is_critical', 'b25-qg149-private_module_reexport_through_init_is_critical', 'b25-qg149-public_import_through_init_is_not_private', 'b25-qg149-parent_private_member_is_forbidden_via_self_or_super', 'b25-qg149-same_class_private_and_explicit_private_override_are_allowed', 'b25-qg149-multilevel_inheritance_private_member_is_blocked', 'b25-qg149-typed_other_instance_of_same_class_is_legal', 'b25-qg149-untyped_other_instance_is_not_assumed_same_owner', 'b25-qg149-inherited_private_field_not_method_only', 'b25-qg149-private_module_attribute_remains_blocked', 'b25-qg149-tests_are_warning_not_critical', 'b25-qg149-dunder_private_export_is_critical', 'b25-qg149-module_private_class_cannot_be_publicly_aliased', 'b25-qg149-dunder_protocol_is_not_private', 'b25-qg209-unique_module_function_without_optional_compiler', 'b25-qg209-direct_local_missing_required_argument_and_default_positive', 'b25-qg209-decorator_rebinding_shadow_and_dynamic_star_remain_unknown', 'b25-qg209-import_shadowed_inside_nested_function_not_certified', 'b25-qg209-no_source_proof_from_kwargs_or_methods', 'b70-qg209-cross-file-two-import-forms', 'b70-qg209-dynamic-module-getattr-open-world'):
            row = next((item for item in json.loads((Path(__file__).parent / 'cases' / 'observable-findings-and-typed-consumers.json').read_text(encoding='utf-8'))['cases'] if item['case_id'] == case_id))
            yield (row['rule'], row['expected'])

    def equals(self, ground_truth, output):
        """Compare every independently required Finding field, including evidence."""
        code, expected = ground_truth
        observed = [item for item in output.findings if item.code == code]
        if len(observed) != len(expected):
            return False
        for finding, frozen in zip(observed, expected):
            for field, target in frozen.items():
                if field == 'evidence':
                    if any((key not in finding.evidence or finding.evidence[key] != value for key, value in target.items())):
                        return False
                elif getattr(finding, field) != target:
                    return False
        return True

class FieldInitializationCase(TestCase):
    """Check real field-read initialization verdicts from the shared topology."""

    def get_enterpoint(self):
        """Use the normalized topology producer consumed by the production QGs."""
        return normalized_topology

    def get_input_data(self):
        """Turn authored module sources into a single shared Graph snapshot."""
        for case_id in ('b25-flow-constructor_always_assigns_before_later_method', 'b25-flow-partial_ctor_branch_unproven_no_missing_bug', 'b25-flow-unknown_call_delete_and_loop_kill_must', 'b25-flow-unsupported_exception_and_inheritance_do_not_fake_must', 'b25-flow-annotation_without_assignment_is_not_initialized', 'b25-flow-delayed_closure_reads_are_not_must_at_creation', 'b25-flow-constructor_callback_before_assignment_cannot_certify_later_reads', 'b25-flow-local_alias_remains_safe_but_global_publication_kills-1', 'b25-flow-local_alias_remains_safe_but_global_publication_kills-2', 'b25-flow-receiver_passed_to_unknown_callee_cannot_be_repaired_by_local_write', 'b25-flow-other_method_can_delete_constructor_field_before_read', 'b25-flow-local_container_not_global_but_global_container_publication_kills-1', 'b25-flow-local_container_not_global_but_global_container_publication_kills-2', 'b25-flow-name_independence_for_alias_transfer_and_escape-1', 'b25-flow-name_independence_for_alias_transfer_and_escape-2', 'b25-flow-constructor_can_publish_self_before_assigning_field', 'b25-flow-sibling_alias_delete_and_augassign_invalidate_constructor_seed-1', 'b25-flow-sibling_alias_delete_and_augassign_invalidate_constructor_seed-2', 'b25-flow-sibling_callback_receiving_alias_invalidates_constructor_seed'):
            row = next((item for item in json.loads((Path(__file__).parent / 'cases' / 'observable-findings-and-typed-consumers.json').read_text(encoding='utf-8'))['cases'] if item['case_id'] == case_id))
            snapshot = RepositoryAnalysisSnapshot(None, 'DIRECTORY', {path: PythonUnit(path, source, ast.parse(source)) for path, source in row['sources'].items()})
            yield ((RepositoryRelationGraph(snapshot),), {})

    def get_output_data(self):
        """Read the authored initialization verdict and witness-line oracle."""
        for case_id in ('b25-flow-constructor_always_assigns_before_later_method', 'b25-flow-partial_ctor_branch_unproven_no_missing_bug', 'b25-flow-unknown_call_delete_and_loop_kill_must', 'b25-flow-unsupported_exception_and_inheritance_do_not_fake_must', 'b25-flow-annotation_without_assignment_is_not_initialized', 'b25-flow-delayed_closure_reads_are_not_must_at_creation', 'b25-flow-constructor_callback_before_assignment_cannot_certify_later_reads', 'b25-flow-local_alias_remains_safe_but_global_publication_kills-1', 'b25-flow-local_alias_remains_safe_but_global_publication_kills-2', 'b25-flow-receiver_passed_to_unknown_callee_cannot_be_repaired_by_local_write', 'b25-flow-other_method_can_delete_constructor_field_before_read', 'b25-flow-local_container_not_global_but_global_container_publication_kills-1', 'b25-flow-local_container_not_global_but_global_container_publication_kills-2', 'b25-flow-name_independence_for_alias_transfer_and_escape-1', 'b25-flow-name_independence_for_alias_transfer_and_escape-2', 'b25-flow-constructor_can_publish_self_before_assigning_field', 'b25-flow-sibling_alias_delete_and_augassign_invalidate_constructor_seed-1', 'b25-flow-sibling_alias_delete_and_augassign_invalidate_constructor_seed-2', 'b25-flow-sibling_callback_receiving_alias_invalidates_constructor_seed'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'observable-findings-and-typed-consumers.json').read_text(encoding='utf-8'))['cases']
            yield next((item['expected'] for item in rows if item['case_id'] == case_id))

    def equals(self, ground_truth, output):
        """Compare field initialization and failure-witness lines by source identity."""
        observed = [{'verdict': fact.verdict, 'witness_lines': list(fact.witness_lines)} for fact in output.field_initializations]
        return len(observed) == len(ground_truth) and all((all((key in item and item[key] == value for key, value in frozen.items())) for item, frozen in zip(observed, ground_truth, strict=True)))

class TypedConsumerCase(TestCase):
    """Preserve TypedDict producer-consumer observations and UNKNOWN boundaries."""

    def get_enterpoint(self):
        """Call the same source-bound graph generator as the production scanner."""
        return normalized_topology

    def get_input_data(self):
        """Prepare the caller and TypedDict schema as authentic Python units."""
        for case_id in ('b25-typed-relative-import-read', 'b25-typed-presence-guard', 'b25-typed-fake-schema', 'b25-typed-rebound-consumer', 'b25-typed-dynamic-or-unknown-field', 'b25-typed-nested-and-lambda', 'b25-typed-import-rebound', 'b25-typed-competing-import', 'b25-typed-fake-isinstance', 'b25-typed-naming-get_value', 'b25-typed-naming-set_value', 'b25-typed-naming-queue', 'b25-typed-naming-mutable'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'observable-findings-and-typed-consumers.json').read_text(encoding='utf-8'))['cases']
            row = next((item for item in rows if item['case_id'] == case_id))
            snapshot = RepositoryAnalysisSnapshot(None, 'DIRECTORY', {path: PythonUnit(path, source, ast.parse(source)) for path, source in row['sources'].items()})
            yield ((RepositoryRelationGraph(snapshot),), {})

    def get_output_data(self):
        """Return source-independent requiredness, guard and capability witnesses."""
        for case_id in ('b25-typed-relative-import-read', 'b25-typed-presence-guard', 'b25-typed-fake-schema', 'b25-typed-rebound-consumer', 'b25-typed-dynamic-or-unknown-field', 'b25-typed-nested-and-lambda', 'b25-typed-import-rebound', 'b25-typed-competing-import', 'b25-typed-fake-isinstance', 'b25-typed-naming-get_value', 'b25-typed-naming-set_value', 'b25-typed-naming-queue', 'b25-typed-naming-mutable'):
            rows = json.loads((Path(__file__).parent / 'cases' / 'observable-findings-and-typed-consumers.json').read_text(encoding='utf-8'))['cases']
            yield next((item['expected'] for item in rows if item['case_id'] == case_id))

    def equals(self, ground_truth, output):
        """Compare actual typed consumer facts with each authored schema witness."""
        observed = [{'schema_symbol_id': fact.schema_symbol_id, 'field': fact.field, 'required': fact.required, 'consumer_symbol_id': fact.consumer_symbol_id, 'guard_observation': fact.guard_observation, 'guard_line': fact.guard_line, 'capability': fact.capability, 'observation': fact.observation} for fact in output.typed_consumers]
        return len(observed) == len(ground_truth) and all((all((key in item and item[key] == value for key, value in frozen.items())) for item, frozen in zip(observed, ground_truth, strict=True)))
