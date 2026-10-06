from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import threading
import time
import io
import contextlib
import signal
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime.src import multilang, scan_snapshot, scan_worker


class TestWorkerLifecycle(unittest.TestCase):
    def test_worker_failure_publishes_reason_to_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            status = Path(temp) / "status.json"
            with (
                patch.object(scan_worker.faulthandler, "dump_traceback_later"),
                patch.object(
                    scan_worker.cli,
                    "scan_target",
                    side_effect=RuntimeError("缺少 Node parser"),
                ),
            ):
                code = scan_worker.main(
                    [
                        "--path",
                        temp,
                        "--output",
                        str(Path(temp) / "result.pkl"),
                        "--status",
                        str(status),
                        "--parent-pid",
                        str(os.getppid()),
                    ]
                )
            payload = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(2, code)
            self.assertEqual("FAILED", payload["state"])
            self.assertIn("缺少 Node parser", payload["message"])

    def _run_sleeping_worker(
        self, *, kill_after: float | None, deadline: str
    ) -> tuple[float, subprocess.Popen[bytes]]:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = SimpleNamespace(root=root, focus_roots=(root,), focus_files=set())
            args = SimpleNamespace(files="", profile="", diff_base="", staged=False)
            original_popen = subprocess.Popen
            processes: list[subprocess.Popen[bytes]] = []

            def spawn(_command: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
                if _command[0] != sys.executable:
                    return original_popen(_command, **kwargs)
                process = original_popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"], **kwargs
                )
                processes.append(process)
                if kill_after is not None:
                    threading.Timer(kill_after, process.kill).start()
                return process

            selection = SimpleNamespace(source="test", name="test", reference="")
            started = time.monotonic()
            with (
                patch.object(
                    scan_snapshot.cli,
                    "resolve_profile_reference",
                    return_value=selection,
                ),
                patch.object(scan_snapshot.subprocess, "Popen", side_effect=spawn),
                patch.dict(
                    os.environ,
                    {
                        "RQG_SCAN_TIMEOUT_SECONDS": deadline,
                        "REPO_QUALITY_GUARD_CACHE_DIR": temp,
                    },
                ),
                self.assertRaisesRegex(RuntimeError, "worker 失败|扫描超时"),
            ):
                scan_snapshot._run_worker(args, target)
            elapsed = time.monotonic() - started
            self.assertEqual(1, len(processes))
            self.assertIsNotNone(processes[0].poll())
            self.assertEqual([], list((root / "scan-worker").iterdir()))
            return elapsed, processes[0]

    def test_parent_detects_killed_worker_and_cleans_ipc(self) -> None:
        elapsed, _ = self._run_sleeping_worker(kill_after=0.5, deadline="10")
        self.assertLess(elapsed, 5)

    def test_cleanup_preserves_recent_worker_directory_without_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            worker_dir = Path(temp) / "scan-worker"
            candidate = worker_dir / "scan-starting"
            candidate.mkdir(parents=True)

            scan_snapshot._cleanup_stale_worker_dirs(worker_dir)

            self.assertTrue(candidate.is_dir())

    def test_cleanup_removes_old_worker_directory_without_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            worker_dir = Path(temp) / "scan-worker"
            candidate = worker_dir / "scan-abandoned"
            candidate.mkdir(parents=True)
            old = time.time() - 120
            os.utime(candidate, (old, old))

            scan_snapshot._cleanup_stale_worker_dirs(worker_dir)

            self.assertFalse(candidate.exists())

    def test_timeout_kills_worker_and_cleans_ipc(self) -> None:
        elapsed, _ = self._run_sleeping_worker(kill_after=None, deadline="0.6")
        self.assertLess(elapsed, 5)

    def test_single_flight_waits_and_reuses_completed_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            key = scan_snapshot._SnapshotKey(
                "profile", "base", False, (), "seal", "fingerprint"
            )
            available = threading.Event()
            result: list[tuple[Path, bool, object]] = []
            errors: list[BaseException] = []

            def fake_load(_root: Path, _key: object) -> object:
                return ("report", "revision") if available.is_set() else None

            with (
                patch.dict(os.environ, {"REPO_QUALITY_GUARD_CACHE_DIR": temp}),
                patch.object(scan_snapshot, "_load_snapshot", side_effect=fake_load),
            ):
                first_lock, first_owned, first_cached = (
                    scan_snapshot._acquire_single_flight(root, key)
                )
                self.assertTrue(first_owned)
                self.assertIsNone(first_cached)
                stream = io.StringIO()

                def wait_for_scan() -> None:
                    try:
                        with contextlib.redirect_stderr(stream):
                            result.append(
                                scan_snapshot._acquire_single_flight(root, key)
                            )
                    except BaseException as error:  # pragma: no cover - test transport
                        errors.append(error)

                thread = threading.Thread(target=wait_for_scan)
                thread.start()
                time.sleep(0.7)
                self.assertTrue(thread.is_alive())
                available.set()
                thread.join(timeout=3)
                if first_owned:
                    shutil.rmtree(first_lock)

            self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)
            self.assertEqual(1, len(result))
            _lock, owned, cached = result[0]
            self.assertFalse(owned)
            self.assertEqual(("report", "revision"), cached)
            self.assertIn("正在排队等待复用结果", stream.getvalue())

    def test_node_parser_timeout_fails_loud(self) -> None:
        unit = SimpleNamespace(language="javascript", source="const x = 1;")
        snapshot = SimpleNamespace(
            language_paths=("src/app.js",),
            language_units={"src/app.js": unit},
        )
        with (
            patch.object(multilang.shutil, "which", return_value="/usr/bin/node"),
            patch.dict(os.environ, {"RQG_NODE_MODULES": "/tmp/node_modules"}),
            patch.object(
                multilang.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["node"], 60),
            ),
            self.assertRaisesRegex(RuntimeError, "超过 60 秒"),
        ):
            multilang.node_facts(snapshot)

    @unittest.skipUnless(
        sys.platform.startswith("linux"), "Linux parent-death contract"
    )
    def test_top_level_parent_death_guard_terminates_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pid_file = root / "child.pid"
            ready_file = root / "ready"
            source_root = Path(__file__).resolve().parents[1]
            child_code = (
                "import time; "
                "from pathlib import Path; "
                "from runtime.src.process_lifecycle import install_parent_death_guard; "
                "install_parent_death_guard(); "
                f"Path({str(ready_file)!r}).write_text('ready'); "
                "time.sleep(30)"
            )
            parent_code = (
                "import subprocess,sys,time; from pathlib import Path; "
                f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
                f"Path({str(pid_file)!r}).write_text(str(p.pid)); "
                "time.sleep(30)"
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(source_root)
            parent = subprocess.Popen(
                [sys.executable, "-c", parent_code], env=environment
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (
                pid_file.is_file() and ready_file.is_file()
            ):
                time.sleep(0.05)
            self.assertTrue(pid_file.is_file())
            self.assertTrue(ready_file.is_file())
            child_pid = int(pid_file.read_text())
            os.kill(parent.pid, signal.SIGKILL)
            parent.wait(timeout=3)

            def child_exited() -> bool:
                stat = Path(f"/proc/{child_pid}/stat")
                if not stat.exists():
                    return True
                fields = stat.read_text().split()
                return len(fields) > 2 and fields[2] == "Z"

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not child_exited():
                time.sleep(0.05)
            self.assertTrue(child_exited(), f"orphan child still alive: {child_pid}")


if __name__ == "__main__":
    unittest.main()
