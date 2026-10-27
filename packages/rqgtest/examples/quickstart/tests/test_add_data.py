"""A frozen dataset example, with separate input and expected streams."""
from rqgtest import TestCase, JSONLDataLoader
from calculator import add


class AddFromFileCase(TestCase):
    def get_enterpoint(self):
        return add

    def get_input_data(self):
        for row in JSONLDataLoader("tests/data/input.jsonl"):
            yield ((row["a"], row["b"]), {})

    def get_output_data(self):
        return JSONLDataLoader("tests/data/expected.jsonl")

    def equals(self, expected, actual):
        return expected == actual
