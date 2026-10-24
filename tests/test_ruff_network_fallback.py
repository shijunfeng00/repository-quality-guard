"""Verify that a missing network registry is rejected without invoking pip."""

from __future__ import annotations

from pathlib import Path
from rqgtest import TestCase
from runtime.src.ruff_runner import RuffRunner


class UnavailableOnlineRegistryCase(TestCase):
    """An unavailable local registry is rejected without launching pip."""

    def get_enterpoint(self):
        return RuffRunner._online_registry_reachable

    def get_input_data(self):
        runner = RuffRunner(
            Path.cwd(),
            Path.cwd() / ".cache",
            {"PIP_INDEX_URL": "http://127.0.0.1:1/simple/"},
        )
        return ((runner,), {})

    def get_output_data(self):
        return False

    def equals(self, expected, actual):
        return actual is expected
