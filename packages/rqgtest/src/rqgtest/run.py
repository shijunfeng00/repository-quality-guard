"""Command line entry point for discovery, selection and execution."""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import subprocess
import shutil
import sys

from .core.unit import TestUnit, _PartialCaseError
from .discovery.collect import discover
from .core.result import SampleResult, BatchResult

_CASES = ()


def _cpu_slots() -> int:
    """Count CPUs available to the current process, respecting affinity."""
    available = (
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else os.cpu_count() or 1
    )
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            available = min(available, max(1, math.ceil(int(quota) / int(period))))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    return max(1, available)


def _worker_init(
    root: str, case_names: tuple[str, ...], selected_files: tuple[str, ...]
) -> None:
    """Load Case definitions once per worker on spawn-only platforms."""
    global _CASES
    if not _CASES:
        collected, _ = discover(
            Path(root),
            selected_files=tuple(Path(path) for path in selected_files)
            if selected_files
            else None,
        )
        selected = dict(collected)
        _CASES = tuple((name, selected[name]) for name in case_names)


def _execute(
    index: int, max_samples: int
) -> tuple[int, list[SampleResult], str | None]:
    name, case = _CASES[index]
    try:
        return index, TestUnit.run(case, name, max_samples), None
    except _PartialCaseError as exc:
        # Preserve preceding observations as well as a visible INVALID marker.
        return index, [*exc.samples, SampleResult(name, "INVALID", str(exc))], str(exc)
    except (Exception, asyncio.CancelledError) as exc:
        detail = f"{name}: {type(exc).__name__}: {exc}"
        return index, [SampleResult(name, "INVALID", detail)], detail


def _source_snapshot(root: Path) -> dict[str, str]:
    """Hash tracked and non-ignored project sources, not generated artifacts."""
    project = root.parent
    git = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=project,
        capture_output=True,
        check=False,
    )
    candidates: list[Path]
    if git.returncode == 0:
        candidates = [project / os.fsdecode(p) for p in git.stdout.split(b"\0") if p]
    else:
        candidates = [
            path
            for path in project.rglob("*.py")
            if not {".git", ".venv", "__pycache__", "build", "dist"}.intersection(
                path.relative_to(project).parts
            )
        ]
    extensions = {
        ".py",
        ".pyx",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".java",
        ".js",
        ".ts",
        ".tsx",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
    }
    hashes = {}
    for path in candidates:
        if path.is_file() and path.suffix in extensions:
            relative = path.relative_to(project).as_posix()
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _resolved_file(root: Path, requested: Path) -> Path:
    """Resolve a requested test file inside the configured test root."""
    current = (Path.cwd() / requested).resolve()
    path = current if current.is_relative_to(root) else (root / requested).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Test file outside root or missing: {requested}")
    if path.suffix != ".py" or not path.name.startswith("test_"):
        raise ValueError(f"Selected module must be test_*.py: {requested}")
    return path


def _resolve_selected_files(
    root: Path, file: Path | None, files: list[Path] | None, name: str | None
) -> tuple[Path, ...] | None:
    """Explicit file and Case selections restrict discovery before importing."""
    requested = [file] if file is not None else list(files or ())
    if name is not None:
        normalized = name.replace("\\", "/")
        if normalized.count("::") != 1:
            raise ValueError("--case must be path/to/test_file.py::CaseName")
        case_path, class_name = normalized.split("::")
        if not case_path or not class_name.isidentifier():
            raise ValueError("--case must be path/to/test_file.py::CaseName")
        case_file = _resolved_file(root, Path(case_path))
        if requested and case_file not in [
            _resolved_file(root, path) for path in requested
        ]:
            raise ValueError("--case and --file(s) must select the same test module")
        if not requested:
            requested.append(Path(case_path))
    if not requested:
        return None
    resolved = tuple(_resolved_file(root, path) for path in requested)
    if len(set(resolved)) != len(resolved):
        raise ValueError("Duplicate selected test files")
    return resolved


def _select(
    cases, root: Path, selected_files: tuple[Path, ...] | None, name: str | None
):
    """Select exact Case identities; a missing match is an error."""
    selected = cases
    if selected_files is not None:
        wanted = {path.relative_to(root).as_posix() for path in selected_files}
        selected = [
            (key, case)
            for key, case in selected
            if key.split("::", 1)[0].replace("\\", "/") in wanted
        ]
    if name is not None:
        normalized = name.replace("\\", "/")
        prefix = root.name + "/"
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
        selected = [
            (key, case)
            for key, case in selected
            if key.replace("\\", "/") == normalized
        ]
    if not selected:
        raise ValueError("No matching TestCase; use --list to inspect available cases")
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rqgtest.run",
        description="Discover and execute standard TestCase modules.",
    )
    parser.add_argument(
        "--tests-root",
        type=Path,
        default=Path("tests"),
        help="测试根目录（默认 tests）",
    )
    parser.add_argument("--list", action="store_true", help="列出所有可运行 Case")
    files_group = parser.add_mutually_exclusive_group()
    files_group.add_argument("--file", type=Path, help="仅运行指定 test_*.py 文件")
    files_group.add_argument(
        "--files", type=Path, nargs="+", help="仅运行指定的多个 test_*.py 文件"
    )
    parser.add_argument("--case", help="仅运行指定 文件路径::Case类名")
    parser.add_argument(
        "--max-samples", type=int, default=10000, help="单个 Case 最大迭代样本数"
    )
    parser.add_argument(
        "--rebuild-cache", action="store_true", help="删除测试缓存并重新预处理"
    )
    parser.add_argument("--report", type=Path, help="写出机器可读执行结果 JSON")
    args = parser.parse_args(argv)
    results: list[SampleResult] = []
    hashes = {}
    invalid = None
    root = args.tests_root.resolve()
    try:
        selected_files = _resolve_selected_files(root, args.file, args.files, args.case)
        if args.rebuild_cache and not args.list:
            for directory in sorted(root.rglob(".cache")):
                if directory.is_dir() and directory.is_relative_to(root):
                    shutil.rmtree(directory)
        cases, hashes = discover(root, selected_files=selected_files)
        selected = _select(cases, root, selected_files, args.case)
        if args.list:
            for name, _ in selected:
                print(name)
            return 0
        global _CASES
        _CASES = tuple(selected)
        before = _source_snapshot(root)
        pool_indices = [
            i for i, (_, case) in enumerate(selected) if case.config.process == "pool"
        ]
        main_indices = [
            i for i, (_, case) in enumerate(selected) if case.config.process == "main"
        ]
        # Explicit coordinator cases finish before parallel work, so they never
        # race against pool cases over process-local fixtures or native state.
        completed = [_execute(index, args.max_samples) for index in main_indices]
        if pool_indices:
            context = multiprocessing.get_context(
                "fork" if sys.platform.startswith("linux") else "spawn"
            )
            # One shared pool per test invocation, bounded by available CPUs.
            with ProcessPoolExecutor(
                max_workers=min(_cpu_slots(), len(pool_indices)),
                mp_context=context,
                initializer=_worker_init,
                initargs=(
                    str(root),
                    tuple(name for name, _ in selected),
                    tuple(str(path) for path in selected_files)
                    if selected_files
                    else (),
                ),
            ) as pool:
                futures = [
                    pool.submit(_execute, i, args.max_samples) for i in pool_indices
                ]
                for index, future in zip(pool_indices, futures):
                    try:
                        completed.append(future.result())
                    except Exception as exc:
                        name = selected[index][0]
                        detail = f"{name}: worker {type(exc).__name__}: {exc}"
                        completed.append(
                            (index, [SampleResult(name, "INVALID", detail)], detail)
                        )
        for _, samples, error in sorted(completed, key=lambda item: item[0]):
            results.extend(samples)
            if error:
                invalid = error if invalid is None else invalid + "; " + error
        after = _source_snapshot(root)
        if before != after:
            invalid = "Project source changed while TestUnit was running"
        if not invalid and not results:
            invalid = "No samples executed"
    except Exception as exc:
        invalid = f"{type(exc).__name__}: {exc}"
    ledger = BatchResult(tuple(results), invalid)
    success = ledger.passed
    data = {"executor": "rqgtest", **ledger.to_dict(), "case_sha256": hashes}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    for item in results:
        if item.status != "PASS":
            print(f"{item.status}: {item.name} {item.detail}", file=sys.stderr)
    if invalid:
        print(f"INVALID: {invalid}", file=sys.stderr)
    print(f"TestUnit: {success}/{len(results)} PASS")
    return 2 if invalid else 0 if success == len(results) and results else 1


if __name__ == "__main__":
    raise SystemExit(main())
