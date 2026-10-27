"""Twelve observable executor contracts migrated from the RQG TestUnit scenarios.

This module tests rqgtest itself; business tests are auto-discovered separately.
Every Case entrypoint is the actual production callable, not a test wrapper.
"""
import json
import unittest
from rqgtest import TestCase, StatefulTestCase, Outcome, TestUnit
from rqgtest.core.result import BatchResult, TestRunFailure


def add(a, b):
    return a + b


def reciprocal(x):
    return 1 / x


def target_error_then_return(value):
    if value == 0:
        raise ValueError("rejected target input")
    return value


class Counter:
    def __init__(self, x=0):
        self.x = x
    def add(self, x):
        self.x += x
        return self.x
    def reject(self):
        raise ValueError("rejected")


class AddCase(TestCase):
    def get_enterpoint(self): return add
    def get_input_data(self): return ((2, 3), {})
    def get_output_data(self): return 5
    def equals(self, expected, actual): return expected == actual


class WrongCase(TestCase):
    def get_enterpoint(self): return add
    def get_input_data(self): return ((2, 3), {})
    def get_output_data(self): return 99
    def equals(self, expected, actual): return expected == actual


class ExceptionCase(TestCase):
    def __init__(self): super().__init__(type=Outcome.EXCEPTION)
    def get_enterpoint(self): return reciprocal
    def get_input_data(self): return ((0,), {})
    def get_output_data(self): return ZeroDivisionError
    def equals(self, expected, actual): return isinstance(actual, expected)


class WrongExceptionCase(TestCase):
    def __init__(self): super().__init__(type=Outcome.EXCEPTION)
    def get_enterpoint(self): return reciprocal
    def get_input_data(self): return ((0,), {})
    def get_output_data(self): return ValueError
    def equals(self, expected, actual): return isinstance(actual, expected)


class UnexpectedExceptionCase(TestCase):
    def get_enterpoint(self): return reciprocal
    def get_input_data(self): return ((0,), {})
    def get_output_data(self): return 0
    def equals(self, expected, actual): return expected == actual


class ReturnModeTargetErrorLedgerCase(TestCase):
    def get_enterpoint(self): return target_error_then_return
    def get_input_data(self):
        yield ((0,), {})
        yield ((2,), {})
    def get_output_data(self):
        yield 0
        yield 2
    def equals(self, expected, actual): return expected == actual


class StatefulTargetErrorCase(StatefulTestCase):
    def get_enterpoint(self): return Counter
    def get_input_data(self):
        yield ((10,), {})
        yield ('add', (2,), {})
        yield ('reject', (), {})
        yield ('add', (3,), {})
    def get_output_data(self):
        yield 12
        yield 0
        yield 15
    def equals(self, expected, actual): return expected == actual


class ConstructCase(TestCase):
    def get_enterpoint(self): return Counter
    def get_input_data(self): return ((9,), {})
    def get_output_data(self): return 9
    def equals(self, expected, actual): return expected == actual.x


class OrderedCase(StatefulTestCase):
    def get_enterpoint(self): return Counter
    def get_input_data(self):
        yield ((10,), {})
        yield ('add', (2,), {})
        yield ('add', (3,), {})
    def get_output_data(self):
        yield 12
        yield 15
    def equals(self, expected, actual): return expected == actual


class MixedExceptionCase(StatefulTestCase):
    def get_enterpoint(self): return Counter
    def get_input_data(self):
        yield ((10,), {})
        yield ('add', (2,), {})
        yield ('reject', (), {})
        yield ('add', (3,), {})
    def get_output_data(self):
        yield 12
        yield ValueError
        yield 15
    def equals(self, expected, actual):
        return isinstance(actual, expected) if isinstance(expected, type) else actual == expected


class DivergenceCase(StatefulTestCase):
    def get_enterpoint(self): return Counter
    def get_input_data(self):
        yield ((10,), {})
        yield ('add', (2,), {})
        yield ('add', (3,), {})
        yield ('add', (4,), {})
    def get_output_data(self):
        yield 12
        yield 999
        yield 19
    def equals(self, expected, actual): return expected == actual


class IteratorErrorCase(StatefulTestCase):
    def get_enterpoint(self): return Counter
    def get_input_data(self):
        yield ((10,), {})
        yield ('add', (2,), {})
        raise RuntimeError('broken input stream')
    def get_output_data(self):
        yield 12
        yield 15
    def equals(self, expected, actual): return expected == actual


class TestTwelveLegacyBehaviors(unittest.TestCase):
    def _statuses(self, case):
        return [row.status for row in TestUnit.run(case, type(case).__name__)]

    def test_01_return_value(self):
        self.assertEqual(self._statuses(AddCase()), ['PASS'])

    def test_02_wrong_output_is_failure(self):
        self.assertEqual(self._statuses(WrongCase()), ['FAIL'])

    def test_03_expected_exception(self):
        self.assertEqual(self._statuses(ExceptionCase()), ['PASS'])

    def test_04_return_mode_target_error_preserves_ordered_ledger(self):
        samples = TestUnit.run(ReturnModeTargetErrorLedgerCase(), 'target-error-ledger')
        self.assertEqual([sample.status for sample in samples], ['FAIL', 'PASS'])
        self.assertEqual([sample.name for sample in samples],
                         ['target-error-ledger[1]', 'target-error-ledger[2]'])
        self.assertIn('Unexpected ValueError: rejected target input', samples[0].detail)

    def test_05_stateful_return_mode_target_error_does_not_pass(self):
        samples = TestUnit.run(StatefulTargetErrorCase(), 'stateful-target-error')
        self.assertEqual([sample.status for sample in samples], ['PASS', 'FAIL', 'PASS'])
        self.assertEqual([sample.name for sample in samples], [
            'stateful-target-error[1].add',
            'stateful-target-error[2].reject',
            'stateful-target-error[3].add',
        ])
        self.assertIn('Unexpected ValueError: rejected', samples[1].detail)

    def test_06_class_construction(self):
        self.assertEqual(self._statuses(ConstructCase()), ['PASS'])

    def test_07_ordered_stateful(self):
        self.assertEqual(self._statuses(OrderedCase()), ['PASS', 'PASS'])

    def test_08_wrong_exception(self):
        self.assertEqual(self._statuses(WrongExceptionCase()), ['FAIL'])

    def test_09_stateful_expected_exception_and_continuation(self):
        self.assertEqual(self._statuses(MixedExceptionCase()), ['PASS', 'PASS', 'PASS'])

    def test_10_first_divergence_preserves_later_observations(self):
        self.assertEqual(self._statuses(DivergenceCase()), ['PASS', 'FAIL', 'PASS'])

    def test_11_input_iterator_error_retains_previous_results(self):
        from rqgtest.core.unit import _PartialCaseError
        with self.assertRaises(_PartialCaseError) as caught:
            TestUnit.run(IteratorErrorCase(), 'IteratorErrorCase')
        self.assertEqual([x.status for x in caught.exception.samples], ['PASS'])
        self.assertIn('broken input stream', str(caught.exception))

    def test_12_ordered_failure_report_serializes_complete_ledger(self):
        samples = tuple(
            TestUnit.run(WrongCase(), 'wrong')
            + TestUnit.run(OrderedCase(), 'fsm')
            + TestUnit.run(AddCase(), 'last')
        )
        ledger = BatchResult(samples)
        self.assertEqual([x.status for x in samples], ['FAIL', 'PASS', 'PASS', 'PASS'])
        self.assertEqual(json.loads(json.dumps(ledger.to_dict())), ledger.to_dict())
        with self.assertRaises(TestRunFailure) as caught:
            TestUnit.require_pass(ledger)
        self.assertIs(caught.exception.report, ledger)
