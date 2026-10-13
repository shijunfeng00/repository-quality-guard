"""Exercise real compiler bindings and inspect.Signature on source-local calls."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from tools.spike_core.provider_audit import collect
from tools.spike_core.python_compiler_provider import certified_static_call_mismatches


@pytest.mark.skipif(
    not os.environ.get("RQG_TEST_PYRIGHT_ENTRY")
    or not os.environ.get("RQG_TEST_PYREFLY_EXEC"),
    reason="offline compiler pair not configured",
)
def test_actual_compiler_binding_finds_missing_argument_under_renaming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renaming authored symbols must not alter the certified arity mismatch."""
    monkeypatch.setenv(
        "PATH",
        f"{Path(os.environ['RQG_TEST_PYREFLY_EXEC']).parent}:{os.environ['PATH']}",
    )
    for callee, parameter in (("deliver", "payload"), ("receive", "document")):
        source = (
            f"def {callee}(first, {parameter}):\n"
            f"    return first, {parameter}\n"
            f"output = {callee}(1)\n"
        )
        file = tmp_path / "module.py"
        file.write_text(source, encoding="utf-8")
        snapshot = RepositoryAnalysisSnapshot(
            tmp_path,
            "DIRECTORY",
            {"module.py": PythonUnit("module.py", source, ast.parse(source))},
        )
        report = collect(tmp_path, Path(os.environ["RQG_TEST_PYRIGHT_ENTRY"]))
        proof = certified_static_call_mismatches(snapshot, report["observations"])
        assert len(proof) == 1
        assert proof[0]["verdict"] == "STATIC_ARITY_MISMATCH_REVIEW_REQUIRED"
        assert proof[0]["runtime_dispatch"] == "OPEN_WORLD"
        assert proof[0]["symbol"] == f"module.{callee}"
        assert parameter in str(proof[0]["witness"])


@pytest.mark.skipif(
    not os.environ.get("RQG_TEST_PYRIGHT_ENTRY")
    or not os.environ.get("RQG_TEST_PYREFLY_EXEC"),
    reason="offline compiler pair not configured",
)
def test_runtime_wrappers_and_rebindings_remain_open_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compiler diagnostics do not certify a wrapped or rebound runtime call."""
    monkeypatch.setenv(
        "PATH",
        f"{Path(os.environ['RQG_TEST_PYREFLY_EXEC']).parent}:{os.environ['PATH']}",
    )
    source_cases = (
        "def passthrough(fn):\n    return fn\n"
        "@passthrough\ndef operation(first, second):\n    return first\n"
        "operation(1)\n",
        "def operation(first, second):\n    return first\n"
        "def operation(first, second):\n    return second\n"
        "operation(1)\n",
    )
    for source in source_cases:
        (tmp_path / "module.py").write_text(source, encoding="utf-8")
        snapshot = RepositoryAnalysisSnapshot(
            tmp_path,
            "DIRECTORY",
            {"module.py": PythonUnit("module.py", source, ast.parse(source))},
        )
        report = collect(tmp_path, Path(os.environ["RQG_TEST_PYRIGHT_ENTRY"]))
        assert certified_static_call_mismatches(snapshot, report["observations"]) == []


@pytest.mark.skipif(
    not os.environ.get("RQG_TEST_PYRIGHT_ENTRY")
    or not os.environ.get("RQG_TEST_PYREFLY_EXEC"),
    reason="offline compiler pair not configured",
)
def test_module_imports_recover_cross_file_static_arity_without_name_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A unique compiler target permits import-bound, not arbitrary method, calls."""
    monkeypatch.setenv(
        "PATH",
        f"{Path(os.environ['RQG_TEST_PYREFLY_EXEC']).parent}:{os.environ['PATH']}",
    )
    sources = {
        "producer.py": "def transmit(left, right):\n    return left, right\n",
        "consumer.py": (
            "from producer import transmit as deliver\n"
            "import producer as module\n"
            "one = deliver(1)\n"
            "two = module.transmit(1)\n"
        ),
    }
    for name, source in sources.items():
        (tmp_path / name).write_text(source, encoding="utf-8")
    snapshot = RepositoryAnalysisSnapshot(
        tmp_path,
        "DIRECTORY",
        {
            name: PythonUnit(name, source, ast.parse(source))
            for name, source in sources.items()
        },
    )
    diagnostics = collect(tmp_path, Path(os.environ["RQG_TEST_PYRIGHT_ENTRY"]))
    proofs = certified_static_call_mismatches(snapshot, diagnostics["observations"])
    assert len(proofs) == 2
    assert {row["line"] for row in proofs} == {3, 4}
    assert {row["symbol"] for row in proofs} == {"producer.transmit"}
    assert all(row["runtime_dispatch"] == "OPEN_WORLD" for row in proofs)
    assert all(row["callee_sha256"] != row["caller_sha256"] for row in proofs)


@pytest.mark.skipif(
    not os.environ.get("RQG_TEST_PYRIGHT_ENTRY")
    or not os.environ.get("RQG_TEST_PYREFLY_EXEC"),
    reason="offline compiler pair not configured",
)
def test_import_target_rebinding_and_dynamic_splats_prevent_arity_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never treat a syntactic importer as a closed-world runtime declaration."""
    monkeypatch.setenv(
        "PATH",
        f"{Path(os.environ['RQG_TEST_PYREFLY_EXEC']).parent}:{os.environ['PATH']}",
    )
    (tmp_path / "producer.py").write_text(
        "def transmit(left, right):\n    return left, right\n", encoding="utf-8"
    )
    sources = (
        "from producer import transmit as call\ncall = lambda *a: 1\ncall(1)\n",
        "import producer as module\nmodule = object()\nmodule.transmit(1)\n",
        "from producer import transmit\ndef launch(transmit):\n    return transmit(1)\n",
        "from producer import transmit\ndef launch(args):\n    return transmit(*args)\n",
    )
    for source in sources:
        (tmp_path / "consumer.py").write_text(source, encoding="utf-8")
        units = {
            name: PythonUnit(name, file.read_text(), ast.parse(file.read_text()))
            for name in ("producer.py", "consumer.py")
            for file in [tmp_path / name]
        }
        snapshot = RepositoryAnalysisSnapshot(tmp_path, "DIRECTORY", units)
        diagnostics = collect(tmp_path, Path(os.environ["RQG_TEST_PYRIGHT_ENTRY"]))
        assert (
            certified_static_call_mismatches(snapshot, diagnostics["observations"])
            == []
        )


@pytest.mark.skipif(
    not os.environ.get("RQG_TEST_PYRIGHT_ENTRY")
    or not os.environ.get("RQG_TEST_PYREFLY_EXEC"),
    reason="offline compiler pair not configured",
)
def test_module_getattr_is_not_an_imported_call_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python module dynamic exports must not become __getattr__ arity errors."""
    monkeypatch.setenv(
        "PATH",
        f"{Path(os.environ['RQG_TEST_PYREFLY_EXEC']).parent}:{os.environ['PATH']}",
    )
    sources = {
        "producer.py": (
            "from typing import Callable\n"
            "def __getattr__(name: str) -> Callable[[int, int], int]:\n"
            "    return lambda x, y: x + y\n"
        ),
        "consumer.py": (
            "from producer import dynamic\n"
            "import producer as module\n"
            "first = dynamic(1)\n"
            "second = module.dynamic(1)\n"
        ),
    }
    for name, source in sources.items():
        (tmp_path / name).write_text(source, encoding="utf-8")
    snapshot = RepositoryAnalysisSnapshot(
        tmp_path,
        "DIRECTORY",
        {
            name: PythonUnit(name, source, ast.parse(source))
            for name, source in sources.items()
        },
    )
    report = collect(tmp_path, Path(os.environ["RQG_TEST_PYRIGHT_ENTRY"]))
    assert (
        sum(row["native_rule"] == "reportCallIssue" for row in report["observations"])
        == 2
    )
    assert certified_static_call_mismatches(snapshot, report["observations"]) == []
