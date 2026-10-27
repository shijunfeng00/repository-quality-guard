"""Production code for the standalone rqgtest quickstart."""

def add(a, b):
    return a + b


def divide(a, b):
    return a / b


class Counter:
    def __init__(self, initial=0):
        self.value = initial

    def add(self, amount):
        self.value += amount
        return self.value
