"""Independent observable behaviors used by rqgtest's own public-API tests."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def add(a: int, b: int) -> int:
    return a + b


async def async_add(a: int, b: int) -> int:
    await asyncio.sleep(0)
    return a + b


def return_coroutine(a: int, b: int):
    return async_add(a, b)


async def throw_value_error():
    await asyncio.sleep(0)
    raise ValueError("intentional")


class Counter:
    def __init__(self, start: int):
        self.current = start

    def add(self, increment: int) -> int:
        self.current += increment
        return self.current

    async def async_add(self, increment: int) -> int:
        await asyncio.sleep(0)
        self.current += increment
        return self.current


class TaskWorker:
    async def start(self):
        self.task = asyncio.create_task(self._work())
        return "started"

    async def _work(self):
        await asyncio.sleep(0.002)
        return "ready"

    async def wait(self):
        return await self.task


async def async_factory():
    await asyncio.sleep(0)
    return TaskWorker()


def run_invalid_case_fixture() -> tuple[int, str]:
    """Exercise the actual rqgtest discovery CLI with an invalid Case module."""
    with tempfile.TemporaryDirectory(prefix="rqgtest-negative-") as work:
        root = Path(work)
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_invalid.py").write_text(
            "from rqgtest import TestCase\n"
            "class BadCase(TestCase):\n"
            "    def get_enterpoint(self): return len\n"
            "    def get_input_data(self): return (([],), {})\n"
            "    def get_output_data(self): return 0\n"
            "    def equals(self, expected, actual): return expected == actual\n"
            "    def helper(self): return 1\n",
            encoding="utf-8",
        )
        report = root / "report.json"
        env = dict(os.environ)
        # The CLI is invoked as production behavior, never by the Case itself.
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "rqgtest.run",
                "--tests-root",
                str(tests),
                "--report",
                str(report),
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        ledger = json.loads(report.read_text(encoding="utf-8"))
        return (
            completed.returncode,
            "Nonstandard Case member"
            if "Nonstandard Case member" in str(ledger)
            else "unexpected",
        )
