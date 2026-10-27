"""Real subprocess failures should be observable before the global scan timeout."""

from __future__ import annotations

import subprocess
import sys

from rqgtest import CaseConfig, Outcome, TestCase

from runtime.src.process_lifecycle import run_bounded_command


class FailedProcessDiagnosticCase(TestCase):
    """Verify that a failed command returns its exit code and real stderr."""

    def get_enterpoint(self):
        return run_bounded_command

    def get_input_data(self):
        return (
            (
                [
                    sys.executable,
                    "-c",
                    "import sys; print('failed', file=sys.stderr); sys.exit(17)",
                ],
            ),
            {"timeout": 3},
        )

    def get_output_data(self):
        return (17, "failed")

    def equals(self, expected, actual):
        return (
            actual.returncode == expected[0]
            and expected[1] in actual.stderr
        )


class UnresponsiveProcessCleanupCase(TestCase):
    """A nonresponsive external process must fail within its declared budget."""

    def __init__(self):
        super().__init__(type=Outcome.EXCEPTION, config=CaseConfig(process="main"))

    def get_enterpoint(self):
        return run_bounded_command

    def get_input_data(self):
        return (
            ([sys.executable, "-c", "import time; time.sleep(10)"],),
            {"timeout": 0.2},
        )

    def get_output_data(self):
        return subprocess.TimeoutExpired

    def equals(self, expected, actual):
        return isinstance(actual, expected)


class ActiveCpuWithoutFixedWallLimitCase(TestCase):
    """A CPU-busy tool may outlive the no-progress threshold without being killed."""

    def get_enterpoint(self):
        return run_bounded_command

    def get_input_data(self):
        script = (
            "import time; started=time.monotonic(); count=0; "
            "\nwhile time.monotonic()-started < 0.7: count+=1\n"
            "print(count)"
        )
        return (
            ([sys.executable, "-c", script],),
            {"stall_timeout": 0.2, "heartbeat_interval": 0.05},
        )

    def get_output_data(self):
        return 0

    def equals(self, expected, actual):
        return (
            actual.returncode == expected
            and actual.stdout.strip().isdigit()
        )


class StalledProcessWatchdogCase(TestCase):
    """A child with no CPU or output is aborted instead of waiting forever."""

    def __init__(self):
        super().__init__(type=Outcome.EXCEPTION, config=CaseConfig(process="main"))

    def get_enterpoint(self):
        return run_bounded_command

    def get_input_data(self):
        return (
            ([sys.executable, "-c", "import time; time.sleep(10)"],),
            {"stall_timeout": 0.2, "heartbeat_interval": 0.05},
        )

    def get_output_data(self):
        return RuntimeError

    def equals(self, expected, actual):
        return isinstance(actual, expected) and "无可观测进展" in str(actual)
