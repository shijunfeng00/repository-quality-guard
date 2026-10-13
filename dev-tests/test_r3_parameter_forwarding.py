"""Contract-derived, executable positive and negative forwarding oracles."""

from __future__ import annotations

import ast
import hashlib

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.parameter_forwarding import trace_parameter_forwarding
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import merge_topologies


def _observe(files: dict[str, str]):
    snapshot = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {p: PythonUnit(p, s, ast.parse(s)) for p, s in files.items()}
    )
    return normalized_topology(RepositoryRelationGraph(snapshot))


def test_34_hop_transitive_flow_and_runtime_oracle():
    # Expected chain is authored independently, then CPython executes it.
    functions = [
        f"def route_{i}(token):\n    return route_{i + 1}(token)\n" for i in range(34)
    ]
    functions.append("def route_34(token):\n    return token\n")
    source = "\n".join(functions)
    topo = _observe({"chain.py": source})
    rows = trace_parameter_forwarding(topo, "chain.route_0", 0)
    assert len(rows) == 35
    assert ("chain.route_34", 0) in rows
    env = {}
    exec(compile(source, "chain.py", "exec"), env)
    target = object()
    assert env["route_0"](target) is target
    assert len(topo.parameter_forwardings) == 34


def test_cyclic_static_references_terminate_without_execution():
    source = "def first(token):\n    return second(token)\ndef second(arg):\n    return first(arg)\n"
    topo = _observe({"cycle.py": source})
    assert trace_parameter_forwarding(topo, "cycle.first", 0) == (
        ("cycle.first", 0),
        ("cycle.second", 0),
    )


def test_alias_and_renaming_do_not_change_forwarding_semantics():
    for formal, alias, callee in [
        ("token", "alias", "move"),
        ("random_field", "another_name", "route"),
    ]:
        text = f"def {callee}(value):\n    return value\ndef send({formal}):\n    {alias} = {formal}\n    return {callee}({alias})\n"
        topo = _observe({"pkg/entry.py": text})
        assert [
            (x.source_parameter_index, x.target_parameter_index)
            for x in topo.parameter_forwardings
        ] == [(0, 0)]
        env = {}
        exec(compile(text, "pkg/entry.py", "exec"), env)
        expected = object()
        assert env["send"](expected) is expected
        first = topo.parameter_forwardings[0]
        assert first.caller_sha256 == hashlib.sha256(text.encode()).hexdigest()
        assert first.capability.endswith("OPEN_RUNTIME")
        assert (
            merge_topologies((topo, topo)).parameter_forwardings
            == topo.parameter_forwardings
        )


def test_unknown_calls_rebinding_shadowed_param_and_dynamic_splats_are_not_proof():
    cases = (
        "def receive(value):\n    return value\ndef send(token):\n    token = object()\n    return receive(token)\n",
        "def receive(value):\n    return value\ndef send(token):\n    mutate(token)\n    return receive(token)\n",
        "def receive(value):\n    return value\ndef send(receive, token):\n    return receive(token)\n",
        "def receive(value):\n    return value\ndef send(token):\n    return receive(*token)\n",
        "def receive(value):\n    return value\nreceive=object()\ndef send(token):\n    return receive(token)\n",
    )
    for code in cases:
        assert _observe({"module.py": code}).parameter_forwardings == ()


def test_exact_cross_module_import_identity_without_module_name_heuristics():
    files = {
        "pkg/producer.py": "def receive(value):\n    return value\n",
        "pkg/consumer.py": "from pkg.producer import receive as transfer\ndef send(payload):\n    return transfer(payload)\n",
    }
    topo = _observe(files)
    rows = topo.parameter_forwardings
    assert len(rows) == 1
    assert rows[0].target_symbol_id == "pkg.producer.receive"
    assert rows[0].source_symbol_id == "pkg.consumer.send"
    assert (
        rows[0].callee_sha256
        == hashlib.sha256(files["pkg/producer.py"].encode()).hexdigest()
    )


def test_two_calls_same_line_cannot_inherit_one_ambiguous_legacy_edge():
    text = "def receive(value):\n    return value\ndef send(token):\n    return receive(token) if token else receive(token)\n"
    topo = _observe({"ambiguous.py": text})
    assert topo.parameter_forwardings == ()


def test_deferred_generator_module_rebinding_and_import_shadow_not_certified():
    cases = (
        "def receive(value):\n    return value\ndef send(token):\n    return (receive(token) for _ in range(1))\n",
        "def receive(value):\n    return value\nfrom other import receive\ndef send(token):\n    return receive(token)\n",
    )
    for source in cases:
        assert _observe({"pkg/something.py": source}).parameter_forwardings == ()
    imported = {
        "pkg/callee.py": "def receive(value):\n    return value\n",
        "pkg/caller.py": "import pkg.callee as helper\nhelper.receive = other\ndef send(token):\n    return helper.receive(token)\n",
    }
    assert _observe(imported).parameter_forwardings == ()
