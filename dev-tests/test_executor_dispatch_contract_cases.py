"""Input/output oracle for source-certified executor dispatch links."""

import ast

import pytest

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import UsageKind
from scripts.evidence_graph import _serialize, TraceOptions, render
from scripts.evidence_statistics import graph_statistics


CASES = [
    (
        "field",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Job:\n"
        "    def __init__(self):\n"
        "        self.pool = ThreadPoolExecutor(max_workers=1)\n"
        "    def run(self):\n"
        "        return 1\n"
        "    def schedule(self):\n"
        "        return self.pool.submit(self.run)\n",
        True,
    ),
    (
        "local",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "def task():\n"
        "    return 1\n"
        "def launch():\n"
        "    with ThreadPoolExecutor(max_workers=1) as pool:\n"
        "        return pool.submit(task)\n",
        True,
    ),
    (
        "nested_closure",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Job:\n"
        "    def __init__(self):\n"
        "        self.pool = ThreadPoolExecutor(max_workers=1)\n"
        "    def task(self):\n"
        "        return 1\n"
        "    def configure(self):\n"
        "        def callback():\n"
        "            return self.pool.submit(self.task)\n"
        "        return callback\n",
        True,
    ),
    (
        "nested_self_shadow",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Job:\n"
        "    def __init__(self):\n"
        "        self.pool = ThreadPoolExecutor(max_workers=1)\n"
        "    def task(self):\n"
        "        return 1\n"
        "    def configure(self):\n"
        "        def callback(self):\n"
        "            return self.pool.submit(self.task)\n"
        "        return callback\n",
        False,
    ),
    (
        "fake",
        "class Fake:\n"
        "    def submit(self, cb):\n"
        "        return 'unused'\n"
        "class Job:\n"
        "    def __init__(self):\n"
        "        self.pool = Fake()\n"
        "    def run(self):\n"
        "        return 1\n"
        "    def schedule(self):\n"
        "        return self.pool.submit(self.run)\n",
        False,
    ),
    (
        "rebinding",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Job:\n"
        "    def __init__(self):\n"
        "        self.pool = ThreadPoolExecutor()\n"
        "    def override(self, other):\n"
        "        self.pool = other\n"
        "    def run(self):\n"
        "        return 1\n"
        "    def schedule(self):\n"
        "        return self.pool.submit(self.run)\n",
        False,
    ),
]


@pytest.mark.parametrize("case,source,expected", CASES)
def test_executor_dispatch_is_conditional_source_witness(tmp_path, case, source, expected):
    snapshot = RepositoryAnalysisSnapshot(
        tmp_path, "DIRECTORY",
        {"module.py": PythonUnit("module.py", source, ast.parse(source))},
    )
    graph = _serialize(normalized_topology(RepositoryRelationGraph(snapshot)))
    scheduled = [edge for edge in graph["edges"] if edge["kind"] == UsageKind.MAY_CALLBACK]
    assert bool(scheduled) is expected, case
    assert all(edge["kind"] != "direct_call" for edge in scheduled)
    if expected:
        target = next(symbol for symbol in graph["symbols"] if symbol["id"] == scheduled[0]["target"])
        report = render(snapshot, graph, target, TraceOptions(
            direction="callers", depth=2, budget=20, radius=0,
            variable=None, line=None, cache_info="test",
        ))
        assert "MAY_CALLBACK, executor scheduling; execution OPEN" in report
        assert "调度可能回调：1" in graph_statistics(graph)
