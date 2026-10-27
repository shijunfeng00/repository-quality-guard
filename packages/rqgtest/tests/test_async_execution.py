"""Independent async lifetime and error-ledger regression tests."""
import asyncio
import unittest
from rqgtest import TestCase, StatefulTestCase, TestUnit


async def _fetch():
    await asyncio.sleep(0)
    return 7


class _FetchCase(TestCase):
    def get_enterpoint(self): return _fetch
    def get_input_data(self): return ((), {})
    def get_output_data(self): return 7
    def equals(self, expected, actual): return expected == actual


class _Worker:
    def __init__(self):
        self.task = None
        self.events = []

    async def start(self):
        async def worker():
            await asyncio.sleep(0)
            self.events.append("ready")
        self.task = asyncio.create_task(worker())
        return "started"

    async def wait(self):
        await self.task
        return list(self.events)

    def count(self):
        return len(self.events)


class _StatefulCase(StatefulTestCase):
    def get_enterpoint(self): return _Worker
    def get_input_data(self):
        yield ((), {})
        yield ("start", (), {})
        yield ("wait", (), {})
        yield ("count", (), {})
    def get_output_data(self):
        yield "started"
        yield ["ready"]
        yield 1
    def equals(self, expected, actual): return expected == actual


async def _new_worker():
    return _Worker()

class _AsyncFactoryCase(StatefulTestCase):
    def get_enterpoint(self): return _new_worker
    def get_input_data(self):
        yield ((), {})
        yield ("start", (), {})
        yield ("wait", (), {})
    def get_output_data(self):
        yield "started"
        yield ["ready"]
    def equals(self, expected, actual): return expected == actual


async def _cancelled():
    raise asyncio.CancelledError()


class _CancelledCase(TestCase):
    def get_enterpoint(self): return _cancelled
    def get_input_data(self): return ((), {})
    def get_output_data(self): return 1
    def equals(self, expected, actual): return expected == actual


class _UnfinishedWorker:
    async def start(self):
        self.task = asyncio.create_task(asyncio.sleep(10))
        return "started"


class _UnfinishedCase(StatefulTestCase):
    def get_enterpoint(self): return _UnfinishedWorker
    def get_input_data(self):
        yield ((), {})
        yield ("start", (), {})
    def get_output_data(self): yield "started"
    def equals(self, expected, actual): return expected == actual


class AsyncExecutionRegression(unittest.TestCase):
    def test_async_entry(self):
        self.assertEqual([r.status for r in TestUnit.run(_FetchCase(), "async")], ["PASS"])

    def test_stateful_preserves_task_and_order_with_sync_method(self):
        self.assertEqual([r.status for r in TestUnit.run(_StatefulCase(), "stateful")], ["PASS"] * 3)

    def test_async_factory_preserves_loop(self):
        self.assertEqual([r.status for r in TestUnit.run(_AsyncFactoryCase(), "factory")], ["PASS"] * 2)

    def test_cancelled_coroutine_is_sample_failure_not_runner_crash(self):
        self.assertEqual(TestUnit.run(_CancelledCase(), "cancelled")[0].status, "FAIL")

    def test_unfinished_tasks_are_cancelled_at_case_end(self):
        self.assertEqual(TestUnit.run(_UnfinishedCase(), "cleanup")[0].status, "PASS")
