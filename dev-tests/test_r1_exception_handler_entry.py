"""Executed CPython negative/positive oracle for source-bound R1 handler entry."""

from __future__ import annotations

import ast
import asyncio
import hashlib

import pytest

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.config import GuardConfig
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.scanner import RepositoryScanner
from runtime.src.topology_facts import merge_topologies


def _scan(tmp_path, source):
    file = tmp_path / "module.py"
    file.write_text(source, encoding="utf-8")
    snapshot = RepositoryAnalysisSnapshot(
        tmp_path,
        "DIRECTORY",
        {"module.py": PythonUnit("module.py", source, ast.parse(source))},
    )
    topology = normalized_topology(RepositoryRelationGraph(snapshot))
    report = RepositoryScanner(tmp_path, GuardConfig(), snapshot, topology).scan()
    findings = [finding for finding in report.findings if finding.code == "QG211"]
    return topology, findings


@pytest.mark.parametrize(
    "process,callee", [("proc", "start"), ("other_variable", "make")]
)
def test_runtime_exception_before_assignment_certifies_real_handler_bug(
    tmp_path, process, callee
):
    source = (
        f"async def operation({callee}):\n"
        "    try:\n"
        f"        {process} = await {callee}()\n"
        "    except TimeoutError:\n"
        f"        if {process}.returncode is None:\n"
        "            return 1\n"
        "    return 2\n"
    )
    topo, findings = _scan(tmp_path, source)
    assert len(topo.exception_handler_reads) == len(findings) == 1
    finding = findings[0]
    assert finding.line == 5
    assert (
        finding.evidence["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    )
    assert finding.evidence["capability"] == "SOURCE_BOUND_EXCEPTION_PATH_OPEN_PROVIDER"
    assert (
        merge_topologies((topo, topo)).exception_handler_reads
        == topo.exception_handler_reads
    )

    async def external_timeout():
        raise TimeoutError("provider fails before returning a value")

    namespace = {}
    exec(compile(source, "module.py", "exec"), namespace)
    with pytest.raises(UnboundLocalError):
        asyncio.run(namespace["operation"](external_timeout))


def test_prebound_value_is_not_a_local_unbound_risk(tmp_path):
    source = (
        "async def operation(start):\n"
        "    proc = None\n"
        "    try:\n"
        "        proc = await start()\n"
        "    except TimeoutError:\n"
        "        if proc is None: return 3\n"
    )
    topo, findings = _scan(tmp_path, source)
    assert not findings and not topo.exception_handler_reads

    async def fail():
        raise TimeoutError("failure")

    namespace = {}
    exec(compile(source, "module.py", "exec"), namespace)
    assert asyncio.run(namespace["operation"](fail)) == 3


def test_exception_after_assignment_is_not_early_store_failure(tmp_path):
    source = (
        "async def operation(start):\n"
        "    try:\n"
        "        proc = 3\n"
        "        await start()\n"
        "    except TimeoutError:\n"
        "        return proc\n"
    )
    topo, findings = _scan(tmp_path, source)
    assert not findings and not topo.exception_handler_reads


def test_statically_unreachable_nested_assignment_cannot_generate_warning(tmp_path):
    source = (
        "async def operation(start):\n"
        "    try:\n"
        "        if False:\n"
        "            proc = await start()\n"
        "    except TimeoutError:\n"
        "        return proc\n"
    )
    topo, findings = _scan(tmp_path, source)
    assert not findings and not topo.exception_handler_reads


def test_exception_handler_assignment_before_read_is_not_reported(tmp_path):
    source = (
        "async def operation(start):\n"
        "    try:\n"
        "        proc = await start()\n"
        "    except TimeoutError:\n"
        "        proc = 0\n"
        "        return proc\n"
    )
    topo, findings = _scan(tmp_path, source)
    assert not findings and not topo.exception_handler_reads


def test_synchronous_assignment_before_async_failure_is_not_certified(tmp_path):
    source = (
        "async def operation(start):\n"
        "    try:\n"
        "        queued_at = monotonic()\n"
        "        await start()\n"
        "    except TimeoutError:\n"
        "        return queued_at\n"
    )
    topo, findings = _scan(tmp_path, source)
    assert not findings and not topo.exception_handler_reads
