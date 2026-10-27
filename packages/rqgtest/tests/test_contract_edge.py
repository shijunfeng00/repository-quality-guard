"""Public runtime outcomes that may not be hidden behind green smoke tests."""

import unittest
from rqgtest import TestCase, StatefulTestCase, Outcome, TestUnit
from rqgtest.core.result import BatchResult, TestRunFailure


def divide(a, b):
    return a / b


def increment(x):
    return x + 1


class WrongValueCase(TestCase):
    def get_enterpoint(self):
        return increment

    def get_input_data(self):
        return ((10,), {})

    def get_output_data(self):
        return 999

    def equals(self, expected, actual):
        return expected == actual


class WrongExceptionCase(TestCase):
    def __init__(self):
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        return divide

    def get_input_data(self):
        return ((2, 0), {})

    def get_output_data(self):
        return KeyError

    def equals(self, expected, actual):
        return isinstance(actual, expected)


class UnexpectedExceptionCase(TestCase):
    def get_enterpoint(self):
        return divide

    def get_input_data(self):
        return ((2, 0), {})

    def get_output_data(self):
        return 2

    def equals(self, expected, actual):
        return expected == actual


class MultiAfterFailureCase(TestCase):
    def get_enterpoint(self):
        return increment

    def get_input_data(self):
        yield ((4,), {})
        yield ((5,), {})
        yield ((6,), {})

    def get_output_data(self):
        yield 5
        yield 999
        yield 7

    def equals(self, expected, actual):
        return expected == actual


class Calculator:
    def __init__(self):
        self.value = 0

    def add(self, delta):
        self.value += delta
        return self.value


class StatefulDivergenceCase(StatefulTestCase):
    def get_enterpoint(self):
        return Calculator

    def get_input_data(self):
        yield ((), {})
        yield ("add", (2,), {})
        yield ("add", (3,), {})
        yield ("add", (4,), {})

    def get_output_data(self):
        yield 2
        yield -1
        yield 9

    def equals(self, expected, actual):
        return expected == actual


class TestFailureLedger(unittest.TestCase):
    def test_incorrect_output_is_failure(self):
        self.assertEqual(
            [x.status for x in TestUnit.run(WrongValueCase(), "wrong")], ["FAIL"]
        )

    def test_wrong_expected_exception_is_failure(self):
        x = TestUnit.run(WrongExceptionCase(), "wrong-exception")[0]
        self.assertEqual(x.status, "FAIL")
        self.assertIn("expected", x.detail)

    def test_return_mode_error_is_failure(self):
        x = TestUnit.run(UnexpectedExceptionCase(), "unexpected")[0]
        self.assertEqual(x.status, "FAIL")
        self.assertIn("ZeroDivisionError", x.detail)

    def test_later_samples_survive_ordinary_failure(self):
        rows = TestUnit.run(MultiAfterFailureCase(), "independent")
        self.assertEqual([x.status for x in rows], ["PASS", "FAIL", "PASS"])

    def test_stateful_sequence_ledger_reports_all_observations(self):
        rows = TestUnit.run(StatefulDivergenceCase(), "fsm")
        self.assertEqual([x.status for x in rows], ["PASS", "FAIL", "PASS"])

    def test_report_exception_preserves_samples(self):
        samples = tuple(TestUnit.run(MultiAfterFailureCase(), "case"))
        report = BatchResult(samples)
        with self.assertRaises(TestRunFailure) as caught:
            TestUnit.require_pass(report)
        self.assertIs(caught.exception.report, report)
        self.assertEqual((report.passed, len(report.samples)), (2, 3))


if __name__ == "__main__":
    unittest.main()
