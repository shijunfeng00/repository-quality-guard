"""Single owner of invocation and comparison. No user-written runner is needed."""
from __future__ import annotations
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
import asyncio
import inspect
from itertools import chain
from .case import ITestCase, TestCase, StatefulTestCase, Outcome
from .result import SampleResult, BatchResult, TestRunFailure
from ..data.loader import DataLoader

_MISSING = object()


class _PartialCaseError(RuntimeError):
    """Retain already observed samples if an input/oracle iterator breaks."""

    def __init__(self, label, source, cause, samples):
        self.samples = tuple(samples)
        super().__init__(f"{label}: {source} iterator {type(cause).__name__}: {cause}")


def _safe_next(iterator, label, source, samples):
    """Differentiate a failed iterator from ordinary end-of-stream."""
    try:
        return next(iterator, _MISSING)
    except Exception as exc:
        raise _PartialCaseError(label, source, exc, samples) from exc



def _await_observation(value, loop=None):
    """Await coroutine, Future or custom awaitable; reuse the case loop when given."""
    if not inspect.isawaitable(value):
        return value

    async def observe():
        return await value

    return asyncio.run(observe()) if loop is None else loop.run_until_complete(observe())


@contextmanager
def _stateful_loop():
    """Preserve background tasks between transitions, then cancel at Case end."""
    loop = asyncio.new_event_loop()
    previous = None
    try:
        try:
            previous = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            pass
        asyncio.set_event_loop(loop)
        yield loop
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        asyncio.set_event_loop(previous)
        loop.close()


def _invocation(value):
    if (not isinstance(value, tuple) or len(value) != 2
            or not isinstance(value[0], tuple) or not isinstance(value[1], dict)):
        raise TypeError("Each invocation must be (tuple(args), dict(kwargs)); wrap a dict argument as ((payload,), {})")
    return value


class TestUnit:
    """Final executor: no subclasses, no project-supplied scheduler."""

    def __init_subclass__(cls, **kwargs):
        raise TypeError("TestUnit is final; use python -m rqgtest.run")

    @staticmethod
    def run(case: ITestCase, name: str, max_samples: int = 10000) -> list[SampleResult]:
        if not isinstance(case, (TestCase, StatefulTestCase)):
            raise TypeError("Expected a concrete TestCase or StatefulTestCase")
        if not isinstance(max_samples, int) or max_samples < 1:
            raise ValueError("max_samples must be positive")
        entry = case.get_enterpoint()
        if not callable(entry):
            raise TypeError("get_enterpoint() must return a callable")
        if isinstance(case, StatefulTestCase):
            return TestUnit._stateful(case, entry, name, max_samples)
        inputs = case.get_input_data()
        expected = case.get_output_data()
        if isinstance(inputs, tuple) and len(inputs) == 2 and isinstance(inputs[1], dict):
            args, kwargs = _invocation(inputs)
            if isinstance(expected, (Iterator, DataLoader)):
                raise TypeError("Single input requires one scalar oracle, not an iterator or DataLoader")
            return [TestUnit._invoke(case, entry, args, kwargs, expected, name)]
        if isinstance(inputs, (str, bytes, Mapping)) or not isinstance(inputs, Iterable):
            raise TypeError("Multi input must be iterable of (args,kwargs) tuples")
        if isinstance(expected, (str, bytes, Mapping)) or not isinstance(expected, Iterable):
            raise TypeError("Multi input requires an iterable of independent expected values")
        results = []
        input_iterator, oracle_iterator = iter(inputs), iter(expected)
        i = 0
        while True:
            inputs_row = _safe_next(input_iterator, name, "input", results)
            expected_row = _safe_next(oracle_iterator, name, "oracle", results)
            if inputs_row is _MISSING and expected_row is _MISSING:
                break
            i += 1
            if i > max_samples:
                raise _PartialCaseError(name, "input", ValueError(f"max_samples exceeded (limit={max_samples}); refusing unbounded stream"), results)
            if inputs_row is _MISSING or expected_row is _MISSING:
                raise _PartialCaseError(name, "paired sample", ValueError(f"Input/output stream lengths differ at row {i}"), results)
            args, kwargs = _invocation(inputs_row)
            results.append(TestUnit._invoke(case, entry, args, kwargs, expected_row, f"{name}[{i}]"))
        if not results:
            raise ValueError(f"Empty input/output stream for {name}")
        return results

    @staticmethod
    def _invoke(case, entry, args, kwargs, expected, name, outcome=None, loop=None):
        """Execute once, preserving the difference between target and oracle exceptions."""
        expected_type = case.type if outcome is None else outcome
        if not isinstance(expected_type, Outcome):
            return SampleResult(name, "INVALID", "Unrecognized expected outcome")
        try:
            actual = entry(*args, **kwargs)
            actual = _await_observation(actual, loop)
        except (Exception, asyncio.CancelledError) as exc:
            if expected_type is Outcome.RETURN:
                return SampleResult(name, "FAIL", f"Unexpected {type(exc).__name__}: {exc}")
            actual = exc
        else:
            if expected_type is Outcome.EXCEPTION:
                return SampleResult(name, "FAIL", f"Expected exception, returned {actual!r}")
        try:
            matches = case.equals(expected, actual)
        except Exception as exc:
            return SampleResult(name, "INVALID", f"Oracle {type(exc).__name__}: {exc}")
        if not isinstance(matches, bool):
            return SampleResult(name, "INVALID", "equals must return bool")
        return SampleResult(name, "PASS" if matches else "FAIL",
                            "" if matches else f"expected {expected!r}, got {actual!r}")

    @staticmethod
    def require_pass(report: BatchResult) -> None:
        """Reject an unsuccessful batch without discarding its failure ledger."""
        if not report.succeeded:
            raise TestRunFailure(report)

    @staticmethod
    def _stateful(case, entry, name, max_samples):
        # One loop for the entire ordered case, including an async factory.
        with _stateful_loop() as loop:
            return TestUnit._stateful_sequence(case, entry, name, max_samples, loop)

    @staticmethod
    def _stateful_sequence(case, entry, name, max_samples, loop):
        """Read only get_input_data(), retaining a target across ordered calls.

        Initialization: a leading (args, kwargs) row, if present, constructs
        a class or calls a function factory. It is not a measured operation.
        Operations: (method_or_None, args, kwargs) tuples, paired 1:1 with
        the independent oracle stream from get_output_data().
        """
        inputs = case.get_input_data()
        if isinstance(inputs, (str, bytes, Mapping)) or not isinstance(inputs, Iterable):
            raise TypeError("Stateful get_input_data() must be an ordered iterable")
        if (isinstance(inputs, tuple) and len(inputs) == 2
                and isinstance(inputs[1], dict)):
            raise TypeError("Stateful get_input_data() must yield the initialization and operations, not return a bare invocation")
        iterator = iter(inputs)
        first = _safe_next(iterator, name, "input", [])
        if first is _MISSING:
            raise ValueError(f"Empty stateful input stream for {name}")
        if isinstance(first, tuple) and len(first) == 2:
            args, kwargs = _invocation(first)
            try:
                target = entry(*args, **kwargs)
                target = _await_observation(target, loop)
            except (Exception, asyncio.CancelledError) as exc:
                return [SampleResult(name, "FAIL", f"Initialization {type(exc).__name__}: {exc}")]
            operations = iterator
        else:
            if inspect.isclass(entry):
                raise TypeError("Class entry requires a leading constructor (args, kwargs) row")
            target = entry
            operations = chain((first,), iterator)
        expected = case.get_output_data()
        if isinstance(expected, (str, bytes, Mapping)) or not isinstance(expected, Iterable):
            raise TypeError("Stateful get_output_data() must provide an iterable oracle stream")
        results = []
        operation_iterator, oracle_iterator = iter(operations), iter(expected)
        i = 0
        while True:
            call = _safe_next(operation_iterator, name, "input", results)
            oracle = _safe_next(oracle_iterator, name, "oracle", results)
            if call is _MISSING and oracle is _MISSING:
                break
            i += 1
            if i > max_samples:
                raise _PartialCaseError(name, "input", ValueError("max_samples exceeded"), results)
            if call is _MISSING or oracle is _MISSING:
                raise _PartialCaseError(name, "paired sample", ValueError(f"Stateful input/output lengths differ at row {i}"), results)
            if not isinstance(call, tuple) or len(call) != 3:
                raise TypeError(f"Stateful operation {i} must be (method_or_None, args, kwargs)")
            method, raw_args, raw_kwargs = call
            if method is not None and (not isinstance(method, str) or not method):
                raise TypeError(f"Stateful operation {i} has an invalid method name")
            args, kwargs = _invocation((raw_args, raw_kwargs))
            operation = target if method is None else getattr(target, method)
            if not callable(operation):
                raise TypeError(f"Stateful operation target is not callable: {name}[{i}]")
            label = method if method is not None else "call"
            # An exception class as the independent oracle explicitly states
            # that this one transition is expected to throw. This supports
            # mixed return/exception FSM sequences without Step/type metadata.
            exception_oracle = (isinstance(oracle, type)
                                and issubclass(oracle, Exception))
            transition_type = (Outcome.EXCEPTION if exception_oracle
                               else case.type)
            results.append(TestUnit._invoke(
                case, operation, args, kwargs, oracle,
                f"{name}[{i}].{label}", outcome=transition_type, loop=loop
            ))
        if not results:
            raise ValueError(f"No stateful operations for {name}")
        return results
