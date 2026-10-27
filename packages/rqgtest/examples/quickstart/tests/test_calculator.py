"""Complete quickstart for the two Case roots."""
from rqgtest import ITestCase, TestCase, StatefulTestCase, Outcome
from calculator import add, divide, Counter


class AddCase(TestCase):
    def get_enterpoint(self):
        return add

    def get_input_data(self):
        return ((2, 3), {})

    def get_output_data(self):
        return 5

    def equals(self, expected, actual):
        return expected == actual


class DivideByZeroCase(TestCase):
    def __init__(self):
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        return divide

    def get_input_data(self):
        return ((1, 0), {})

    def get_output_data(self):
        return ZeroDivisionError

    def equals(self, expected, actual):
        return isinstance(actual, expected)


class CounterConstructionCase(TestCase):
    def get_enterpoint(self):
        return Counter

    def get_input_data(self):
        return ((7,), {})

    def get_output_data(self):
        return 7

    def equals(self, expected, actual):
        return actual.value == expected


class CounterSequenceCase(StatefulTestCase):
    def get_enterpoint(self):
        return Counter

    def get_input_data(self):
        yield ((10,), {})             # 构造 Counter(10)；此项没有输出 Oracle
        yield ("add", (2,), {})     # 在同一对象上按顺序调用
        yield ("add", (3,), {})

    def get_output_data(self):
        yield 12
        yield 15

    def equals(self, expected, actual):
        return expected == actual


