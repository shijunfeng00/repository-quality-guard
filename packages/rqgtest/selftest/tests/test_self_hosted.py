"""rqgtest self-hosted regression Cases; every oracle is independent of the target."""

from rqgtest import CaseConfig, Outcome, StatefulTestCase, TestCase
from targets import (
    add,
    async_add,
    return_coroutine,
    throw_value_error,
    Counter,
    TaskWorker,
    async_factory,
    run_invalid_case_fixture,
)


class SyncValuesCase(TestCase):
    def get_enterpoint(self):
        return add

    def get_input_data(self):
        yield ((2, 3), {})
        yield ((-8, 5), {})
        yield ((0, 0), {})

    def get_output_data(self):
        yield 5
        yield -3
        yield 0

    def equals(self, expected, actual):
        return expected == actual


class AsyncValuesCase(TestCase):
    def get_enterpoint(self):
        return async_add

    def get_input_data(self):
        yield ((3, 4), {})
        yield ((-2, -9), {})
        yield ((0, 9), {})

    def get_output_data(self):
        yield 7
        yield -11
        yield 9

    def equals(self, expected, actual):
        return expected == actual


class SyncReturningCoroutineCase(TestCase):
    def get_enterpoint(self):
        return return_coroutine

    def get_input_data(self):
        return ((5, 6), {})

    def get_output_data(self):
        return 11

    def equals(self, expected, actual):
        return expected == actual


class AsyncExpectedExceptionCase(TestCase):
    def __init__(self):
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        return throw_value_error

    def get_input_data(self):
        return ((), {})

    def get_output_data(self):
        return ValueError

    def equals(self, expected, actual):
        return isinstance(actual, expected) and str(actual) == "intentional"


class MixedStateSequenceCase(StatefulTestCase):
    def get_enterpoint(self):
        return Counter

    def get_input_data(self):
        yield ((10,), {})
        yield ("add", (2,), {})
        yield ("async_add", (3,), {})
        yield ("add", (-1,), {})

    def get_output_data(self):
        yield 12
        yield 15
        yield 14

    def equals(self, expected, actual):
        return expected == actual


class AsyncTaskLifespanCase(StatefulTestCase):
    def get_enterpoint(self):
        return TaskWorker

    def get_input_data(self):
        yield ((), {})
        yield ("start", (), {})
        yield ("wait", (), {})

    def get_output_data(self):
        yield "started"
        yield "ready"

    def equals(self, expected, actual):
        return expected == actual


class AsyncFactoryTaskLifespanCase(StatefulTestCase):
    def get_enterpoint(self):
        return async_factory

    def get_input_data(self):
        yield ((), {})
        yield ("start", (), {})
        yield ("wait", (), {})

    def get_output_data(self):
        yield "started"
        yield "ready"

    def equals(self, expected, actual):
        return expected == actual


class InvalidDiscoveryIsRejectedCase(TestCase):
    def __init__(self):
        super().__init__(config=CaseConfig(process="main"))

    def get_enterpoint(self):
        return run_invalid_case_fixture

    def get_input_data(self):
        return ((), {})

    def get_output_data(self):
        return (2, "Nonstandard Case member")

    def equals(self, expected, actual):
        return expected == actual
