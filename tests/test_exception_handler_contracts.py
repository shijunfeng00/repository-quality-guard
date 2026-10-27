"""Standard four-interface Cases for batch 11."""
from __future__ import annotations
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.exception_handler_rule_objects import HandlerRuleContext, SilentExceptionFallbackRule, SilentHandlerFallthroughRule
from runtime.src.handler_read_rule import ExceptionHandlerReadRule, ExceptionReadRuleContext
from runtime.src.topology_facts import RepositoryTopology, ExceptionHandlerReadFact, TypedProducerFact, TypedSchemaFieldFact
from runtime.src.typed_producer_contract import TypedLiteralRuleContext, TypedRequiredLiteralRule

class Batch11QG214Case(TestCase):
    """Verify authored QG214 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG214 Core rule without an adapter."""
        return SilentExceptionFallbackRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/exception-handler-input-oracle.pkl'):
            if record['case'] == 'Batch11QG214Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/exception-handler-input-oracle.pkl'):
            if record['case'] == 'Batch11QG214Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Verify source-bound evidence and authored ground truth."""
        _case_arg_1_expected = expected
        _case_arg_1_actual = actual
        _case_arg_1_code = 'QG214'
        while True:
            ground_code, truth = _case_arg_1_expected
            if ground_code != _case_arg_1_code:
                _case_value_1 = False
                break
            status_key = {'QG211': 'capability', 'QG214': 'semantic_status', 'QG215': 'semantic_status', 'QG216': 'status'}[_case_arg_1_code]
            found = tuple(((item.code, item.severity, item.confidence, item.path, item.line, item.column, item.symbol, item.source, item.evidence[status_key]) for item in _case_arg_1_actual))
            if found != truth:
                _case_value_1 = False
                break
            if _case_arg_1_code == 'QG211':
                _case_value_1 = all((item.evidence['local_name'] == 'value' for item in _case_arg_1_actual))
                break
            if _case_arg_1_code == 'QG216':
                _case_value_1 = all((item.evidence['missing_required_fields'] == ['age'] for item in _case_arg_1_actual))
                break
            _case_value_1 = all((bool(item.evidence['source_sha256']) for item in _case_arg_1_actual))
            break
            break
        return _case_value_1

class Batch11QG215Case(TestCase):
    """Verify authored QG215 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG215 Core rule without an adapter."""
        return SilentHandlerFallthroughRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/exception-handler-input-oracle.pkl'):
            if record['case'] == 'Batch11QG215Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/exception-handler-input-oracle.pkl'):
            if record['case'] == 'Batch11QG215Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Verify source-bound evidence and authored ground truth."""
        _case_arg_2_expected = expected
        _case_arg_2_actual = actual
        _case_arg_2_code = 'QG215'
        while True:
            ground_code, truth = _case_arg_2_expected
            if ground_code != _case_arg_2_code:
                _case_value_2 = False
                break
            status_key = {'QG211': 'capability', 'QG214': 'semantic_status', 'QG215': 'semantic_status', 'QG216': 'status'}[_case_arg_2_code]
            found = tuple(((item.code, item.severity, item.confidence, item.path, item.line, item.column, item.symbol, item.source, item.evidence[status_key]) for item in _case_arg_2_actual))
            if found != truth:
                _case_value_2 = False
                break
            if _case_arg_2_code == 'QG211':
                _case_value_2 = all((item.evidence['local_name'] == 'value' for item in _case_arg_2_actual))
                break
            if _case_arg_2_code == 'QG216':
                _case_value_2 = all((item.evidence['missing_required_fields'] == ['age'] for item in _case_arg_2_actual))
                break
            _case_value_2 = all((bool(item.evidence['source_sha256']) for item in _case_arg_2_actual))
            break
            break
        return _case_value_2

class Batch11QG211Case(TestCase):
    """Verify authored QG211 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG211 Core rule without an adapter."""
        return ExceptionHandlerReadRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/exception-handler-input-oracle.pkl'):
            if record['case'] == 'Batch11QG211Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/exception-handler-input-oracle.pkl'):
            if record['case'] == 'Batch11QG211Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Verify source-bound evidence and authored ground truth."""
        _case_arg_3_expected = expected
        _case_arg_3_actual = actual
        _case_arg_3_code = 'QG211'
        while True:
            ground_code, truth = _case_arg_3_expected
            if ground_code != _case_arg_3_code:
                _case_value_3 = False
                break
            status_key = {'QG211': 'capability', 'QG214': 'semantic_status', 'QG215': 'semantic_status', 'QG216': 'status'}[_case_arg_3_code]
            found = tuple(((item.code, item.severity, item.confidence, item.path, item.line, item.column, item.symbol, item.source, item.evidence[status_key]) for item in _case_arg_3_actual))
            if found != truth:
                _case_value_3 = False
                break
            if _case_arg_3_code == 'QG211':
                _case_value_3 = all((item.evidence['local_name'] == 'value' for item in _case_arg_3_actual))
                break
            if _case_arg_3_code == 'QG216':
                _case_value_3 = all((item.evidence['missing_required_fields'] == ['age'] for item in _case_arg_3_actual))
                break
            _case_value_3 = all((bool(item.evidence['source_sha256']) for item in _case_arg_3_actual))
            break
            break
        return _case_value_3

class Batch11QG216Case(TestCase):
    """Verify authored QG216 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG216 Core rule without an adapter."""
        return TypedRequiredLiteralRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/exception-handler-input-oracle.pkl'):
            if record['case'] == 'Batch11QG216Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/exception-handler-input-oracle.pkl'):
            if record['case'] == 'Batch11QG216Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Verify source-bound evidence and authored ground truth."""
        _case_arg_4_expected = expected
        _case_arg_4_actual = actual
        _case_arg_4_code = 'QG216'
        while True:
            ground_code, truth = _case_arg_4_expected
            if ground_code != _case_arg_4_code:
                _case_value_4 = False
                break
            status_key = {'QG211': 'capability', 'QG214': 'semantic_status', 'QG215': 'semantic_status', 'QG216': 'status'}[_case_arg_4_code]
            found = tuple(((item.code, item.severity, item.confidence, item.path, item.line, item.column, item.symbol, item.source, item.evidence[status_key]) for item in _case_arg_4_actual))
            if found != truth:
                _case_value_4 = False
                break
            if _case_arg_4_code == 'QG211':
                _case_value_4 = all((item.evidence['local_name'] == 'value' for item in _case_arg_4_actual))
                break
            if _case_arg_4_code == 'QG216':
                _case_value_4 = all((item.evidence['missing_required_fields'] == ['age'] for item in _case_arg_4_actual))
                break
            _case_value_4 = all((bool(item.evidence['source_sha256']) for item in _case_arg_4_actual))
            break
            break
        return _case_value_4
