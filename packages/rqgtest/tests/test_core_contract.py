"""Executor-level regression verification, separate from business Case discovery."""

import unittest
from rqgtest import ITestCase, TestCase, StatefulTestCase, Outcome, TestUnit


async def async_product(a, b):
    return a * b


class AsyncProductCase(TestCase):
    def get_enterpoint(self):
        return async_product

    def get_input_data(self):
        return ((4, 5), {})

    def get_output_data(self):
        return 20

    def equals(self, expected, actual):
        return expected == actual


class Counter:
    def __init__(self, initial=0):
        self.value = initial

    def add(self, amount):
        self.value += amount
        return self.value

    def reject(self):
        raise ValueError("not allowed")


def create_adder(initial):
    state = [initial]

    def adder(amount):
        state[0] += amount
        return state[0]

    return adder


def counter_function(amount):
    counter_function.current += amount
    return counter_function.current


counter_function.current = 0


class CounterSequenceCase(StatefulTestCase):
    def get_enterpoint(self):
        return Counter

    def get_input_data(self):
        yield ((10,), {})
        yield ("add", (2,), {})
        yield ("add", (3,), {})

    def get_output_data(self):
        yield 12
        yield 15

    def equals(self, expected, actual):
        return expected == actual


class FactoryCase(StatefulTestCase):
    def get_enterpoint(self):
        return create_adder

    def get_input_data(self):
        yield ((10,), {})
        yield (None, (2,), {})
        yield (None, (3,), {})

    def get_output_data(self):
        yield 12
        yield 15

    def equals(self, expected, actual):
        return expected == actual


class StatefulFunctionCase(StatefulTestCase):
    def get_enterpoint(self):
        return counter_function

    def get_input_data(self):
        yield (None, (2,), {})
        yield (None, (3,), {})

    def get_output_data(self):
        yield 2
        yield 5

    def equals(self, expected, actual):
        return expected == actual


class FSM:
    def __init__(self):
        self.state = "A"

    def transition(self, event):
        transitions = {("A", "B"): "C", ("C", "D"): "E"}
        self.state = transitions[(self.state, event)]
        return self.state


class AsyncFSM:
    def __init__(self):
        self.state = "A"

    async def transition(self, event):
        self.state = {("A", "B"): "C", ("C", "D"): "E"}[(self.state, event)]
        return self.state


class AsyncFSMCase(StatefulTestCase):
    def get_enterpoint(self):
        return AsyncFSM

    def get_input_data(self):
        yield ((), {})
        yield ("transition", ("B",), {})
        yield ("transition", ("D",), {})

    def get_output_data(self):
        yield "C"
        yield "E"

    def equals(self, expected, actual):
        return expected == actual


class FSMCase(StatefulTestCase):
    def get_enterpoint(self):
        return FSM

    def get_input_data(self):
        yield ((), {})
        yield ("transition", ("B",), {})
        yield ("transition", ("D",), {})

    def get_output_data(self):
        yield "C"
        yield "E"

    def equals(self, expected, actual):
        return expected == actual


class MixedOutcomeCase(StatefulTestCase):
    def get_enterpoint(self):
        return Counter

    def get_input_data(self):
        yield ((10,), {})
        yield ("add", (2,), {})
        yield ("reject", (), {})
        yield ("add", (3,), {})

    def get_output_data(self):
        yield 12
        yield ValueError
        yield 15

    def equals(self, expected, actual):
        return (
            isinstance(actual, expected)
            if isinstance(expected, type)
            else actual == expected
        )


class CallableBox:
    def __init__(self):
        self.total = 0

    def __call__(self, amount):
        self.total += amount
        return self.total


class CallableBoxCase(StatefulTestCase):
    def get_enterpoint(self):
        return CallableBox

    def get_input_data(self):
        yield ((), {})
        yield (None, (2,), {})
        yield (None, (3,), {})

    def get_output_data(self):
        yield 2
        yield 5

    def equals(self, expected, actual):
        return expected == actual


class StatefulExceptionCase(StatefulTestCase):
    def __init__(self):
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        return unsafe_divide

    def get_input_data(self):
        yield (None, (0,), {})
        yield (None, (0,), {})

    def get_output_data(self):
        yield ZeroDivisionError
        yield ZeroDivisionError

    def equals(self, expected, actual):
        return isinstance(actual, expected)


def unsafe_divide(value):
    return 1 / value


class ErrorCase(TestCase):
    def __init__(self):
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        return unsafe_divide

    def get_input_data(self):
        return ((0,), {})

    def get_output_data(self):
        return ZeroDivisionError

    def equals(self, expected, actual):
        return isinstance(actual, expected)


class ConstructCase(TestCase):
    def get_enterpoint(self):
        return Counter

    def get_input_data(self):
        return ((10,), {})

    def get_output_data(self):
        return 10

    def equals(self, expected, actual):
        return expected == actual.value


class MismatchCase(StatefulTestCase):
    def get_enterpoint(self):
        return Counter

    def get_input_data(self):
        yield ((10,), {})
        yield ("add", (2,), {})

    def get_output_data(self):
        yield 12
        yield 15

    def equals(self, expected, actual):
        return expected == actual


class InvalidOperationCase(StatefulTestCase):
    def get_enterpoint(self):
        return Counter

    def get_input_data(self):
        yield ((10,), {})
        yield ("add", 2, {})

    def get_output_data(self):
        yield 12

    def equals(self, expected, actual):
        return expected == actual


class Regression(unittest.TestCase):
    def assertPassed(self, case, count):
        samples = TestUnit.run(case, type(case).__name__)
        self.assertEqual([x.status for x in samples], ["PASS"] * count)

    def test_async_function_without_new_case_root(self):
        self.assertPassed(AsyncProductCase(), 1)

    def test_async_fsm_without_new_case_root(self):
        self.assertPassed(AsyncFSMCase(), 2)

    def test_fsm_ordered_transition(self):
        self.assertPassed(FSMCase(), 2)

    def test_mixed_exception_and_return(self):
        self.assertPassed(MixedOutcomeCase(), 3)

    def test_root_is_abstract(self):
        import inspect

        self.assertTrue(inspect.isabstract(ITestCase))
        self.assertEqual(
            set(ITestCase.__abstractmethods__),
            {"get_enterpoint", "get_input_data", "get_output_data", "equals"},
        )
        self.assertTrue(inspect.isabstract(TestCase))
        self.assertTrue(inspect.isabstract(StatefulTestCase))

    def test_direct_interface_implementation_forbidden(self):
        with self.assertRaises(TypeError):

            class InvalidDirect(ITestCase):
                def get_enterpoint(self):
                    return int

                def get_input_data(self):
                    return ((1,), {})

                def get_output_data(self):
                    return 1

                def equals(self, expected, actual):
                    return expected == actual

    def test_intermediate_subclass_forbidden(self):
        with self.assertRaises(TypeError):

            class InvalidIntermediate(CounterSequenceCase):
                pass

    def test_counter_state(self):
        self.assertPassed(CounterSequenceCase(), 2)

    def test_function_factory(self):
        self.assertPassed(FactoryCase(), 2)

    def test_callable_object(self):
        self.assertPassed(CallableBoxCase(), 2)

    def test_stateful_exception(self):
        self.assertPassed(StatefulExceptionCase(), 2)

    def test_stateful_function(self):
        counter_function.current = 0
        self.assertPassed(StatefulFunctionCase(), 2)

    def test_exception(self):
        self.assertPassed(ErrorCase(), 1)

    def test_instance(self):
        self.assertPassed(ConstructCase(), 1)

    def test_discard_extra_oracle_is_invalid(self):
        with self.assertRaisesRegex(RuntimeError, "lengths differ"):
            TestUnit.run(MismatchCase(), "MismatchCase")

    def test_reject_invalid_invocation(self):
        with self.assertRaisesRegex(TypeError, "Each invocation"):
            TestUnit.run(InvalidOperationCase(), "InvalidOperationCase")

    def test_no_extra_public_interface(self):
        self.assertEqual(
            set(ITestCase.__abstractmethods__),
            {"get_enterpoint", "get_input_data", "get_output_data", "equals"},
        )


if __name__ == "__main__":
    unittest.main()


class PositionalOnlyCalculatorCase(TestCase):
    def get_enterpoint(self):
        return Counter.add

    def get_input_data(self):
        return ((Counter(10), 2),)

    def get_output_data(self):
        return 12

    def equals(self, expected, actual):
        return expected == actual


class StatefulReceiverCase(StatefulTestCase):
    def get_enterpoint(self):
        return Counter.add

    def get_input_data(self):
        counter = Counter(10)
        yield counter, (2,)
        yield counter, (3,)

    def get_output_data(self):
        yield 12
        yield 15

    def equals(self, expected, actual):
        return expected == actual


class StatefulReceiverKeywordCase(StatefulTestCase):
    def get_enterpoint(self):
        return Counter.add

    def get_input_data(self):
        counter = Counter(10)
        yield counter, (), {"amount": 2}
        yield counter, (), {"amount": 3}

    def get_output_data(self):
        yield 12
        yield 15

    def equals(self, expected, actual):
        return expected == actual


class StatefulReceiverLengthMismatchCase(StatefulTestCase):
    def get_enterpoint(self):
        return Counter.add

    def get_input_data(self):
        counter = Counter(10)
        yield counter, (2,)
        yield counter, (3,)

    def get_output_data(self):
        yield 12

    def equals(self, expected, actual):
        return expected == actual


class PositionalOnlyInvocationTests(unittest.TestCase):
    def test_single_unbound_method(self):
        results = TestUnit.run(PositionalOnlyCalculatorCase(), "Calculator")
        self.assertEqual([r.status for r in results], ["PASS"])

    def test_stateful_receiver_matches_oracles(self):
        results = TestUnit.run(StatefulReceiverCase(), "Counter")
        self.assertEqual([r.status for r in results], ["PASS", "PASS"])

    def test_stateful_receiver_kwargs(self):
        results = TestUnit.run(StatefulReceiverKeywordCase(), "Counter")
        self.assertEqual([r.status for r in results], ["PASS", "PASS"])

    def test_stateful_receiver_requires_one_oracle_per_call(self):
        with self.assertRaisesRegex(RuntimeError, "lengths differ"):
            TestUnit.run(StatefulReceiverLengthMismatchCase(), "Counter")
