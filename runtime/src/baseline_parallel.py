"""Bounded isolated Git-baseline work overlapped with current-source analysis."""

from __future__ import annotations

import os
import pickle
import subprocess
import sys
import tempfile
from types import TracebackType
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .audit_target import AuditTarget
from .config import GuardConfig
from .model import QualityBaseline
from .process_lifecycle import (
    terminate_process_tree,
    wait_for_active_process,
)


class GitBaselineTask:
    """Manage the lifecycle of one isolated Git-baseline scan.

    A cached baseline bypasses process creation; otherwise a child Python
    interpreter computes it while the caller can scan the current worktree.
    Cleanup owns both process termination and temporary request files.
    """

    def __init__(self) -> None:
        """Prepare one baseline task without allocating external resources.

        A new task has neither cached results nor worker identity; start() must
        bind the revision before result() can return either execution path.

        Returns:
            None.
        """
        self._revision: str | None = None
        self._cached: tuple[QualityBaseline, QualityBaseline] | None = None
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> GitBaselineTask:
        """Expose this task to a context manager without starting a scan.

        Returns:
            The same task; callers still invoke start() with a revision.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the baseline worker and discard its temporary result workspace.

        Args:
            exc_type: Exception class received from Python's context manager protocol.
            exc_value: Active exception instance, if the context body raised.
            traceback: Context-body traceback, if an exception was raised.
                None of these values changes cleanup or suppresses the exception.

        Returns:
            None; always delegates cleanup to close(), including abnormal exits.
        """
        self.close()

    def start(
        self,
        target: AuditTarget,
        config: GuardConfig,
        revision: str,
        profile_reference: str,
        snapshot: RepositoryAnalysisSnapshot,
        cached: tuple[QualityBaseline, QualityBaseline] | None,
    ) -> None:
        """Start exactly one baseline read for the requested frozen revision.

        A compatible disk cache is used immediately. A cache miss serializes
        the snapshot and launches an isolated worker; start never awaits it.

        Args:
            target: Audit target including its Git repository root.
            config: Effective scan policy shared with the current scan.
            revision: Commit identifier matching snapshot.label.
            profile_reference: Profile identifier resolved again by the worker.
            snapshot: Parsed baseline source for precisely this revision.
            cached: Already resolved immutable disk-cache result, when available.

        Returns:
            None; call result() to retrieve or await the baseline pair.

        Raises:
            RuntimeError: The task was started already or snapshots disagree.
        """
        if self._revision is not None:
            raise RuntimeError("Git baseline task already started")
        if snapshot.label != revision:
            raise RuntimeError("Git baseline snapshot revision mismatch")
        self._revision = revision
        self._cached = cached
        if self._cached is not None:
            return
        self._directory = tempfile.TemporaryDirectory(prefix="quality-guard-parallel-")
        root = Path(self._directory.name)
        with (root / "request.pkl").open("wb") as destination:
            pickle.dump(
                (target, config, revision, profile_reference, snapshot),
                destination,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        release_root = Path(__file__).resolve().parents[2]
        with (root / "worker.log").open("wb") as diagnostic:
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "runtime.src.baseline_worker",
                    str(root / "request.pkl"),
                    str(root / "result.pkl"),
                ],
                cwd=release_root,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=diagnostic,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )

    def result(self, revision: str) -> tuple[QualityBaseline, QualityBaseline]:
        """Obtain the pair of source and policy baselines for this revision.

        Cached results are returned directly; otherwise wait for the child,
        deserialize its output, and clean resources even when it fails.

        Args:
            revision: Required revision identity, checked against start().

        Returns:
            The two QualityBaseline records generated by the baseline scan.

        Raises:
            RuntimeError: Identity mismatch, absent worker, or failed child.
        """
        if revision != self._revision:
            raise RuntimeError("Git baseline task revision mismatch")
        if self._cached is not None:
            return self._cached
        process = self._process
        directory = self._directory
        if process is None or directory is None:
            raise RuntimeError("Git baseline task is not running")
        try:
            exit_code = wait_for_active_process(
                process,
                stall_timeout=60,
                heartbeat_interval=5,
                progress_file=Path(directory.name) / "worker.log",
            )
            root = Path(directory.name)
            if exit_code != 0:
                detail = (root / "worker.log").read_text(
                    encoding="utf-8", errors="replace"
                )
                raise RuntimeError(
                    f"Git baseline worker exit={exit_code}: {detail[-4000:]}"
                )
            with (root / "result.pkl").open("rb") as source:
                return pickle.load(source)
        finally:
            self.close()

    def close(self) -> None:
        """Terminate any active baseline worker and discard its temporary files.

        Called explicitly or by result()/__exit__(), including failure paths;
        repeated cleanup does not leave a live child or temporary directory.

        Returns:
            None.
        """
        if self._process is not None:
            process = self._process
            terminate_process_tree(process)
            self._process = None
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None
