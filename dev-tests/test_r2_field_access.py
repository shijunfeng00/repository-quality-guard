"""R2-03 tests anchored in real AST operations, not identifier spellings."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import merge_topologies


def _scan(source: str):
    path = "pkg/example.py"
    snapshot = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {path: PythonUnit(path, source, ast.parse(source))}
    )
    return normalized_topology(RepositoryRelationGraph(snapshot))


def test_distinct_instance_receivers_and_true_operations() -> None:
    topology = _scan(
        "class A:\n"
        "    def use(one):\n"
        "        one.item = 1\n"
        "        a = one.item\n"
        "        one.item += 2\n"
        "        del one.item\n"
        "class B:\n"
        "    def use(two):\n"
        "        two.item = 9\n"
        "        return two.item\n"
    )
    rows = [
        (x.owner_symbol_id, x.method_symbol_id, x.operation, x.line)
        for x in topology.instance_field_accesses
    ]
    assert rows == [
        ("pkg.example.A", "pkg.example.A.use", "WRITE", 3),
        ("pkg.example.A", "pkg.example.A.use", "READ", 4),
        ("pkg.example.A", "pkg.example.A.use", "READ_WRITE", 5),
        ("pkg.example.A", "pkg.example.A.use", "DELETE", 6),
        ("pkg.example.B", "pkg.example.B.use", "WRITE", 9),
        ("pkg.example.B", "pkg.example.B.use", "READ", 10),
    ]
    assert len({x.source_sha256 for x in topology.instance_field_accesses}) == 1


def test_spelling_changes_only_identity_not_effect_classification() -> None:
    a = _scan("class A:\n    def use(receiver):\n        return receiver.get_item\n")
    b = _scan("class A:\n    def use(whatever):\n        return whatever.read_only\n")
    assert [x.operation for x in a.instance_field_accesses] == ["READ"]
    assert [x.operation for x in b.instance_field_accesses] == ["READ"]


def test_rebinding_decorator_and_nested_escape_are_conservative() -> None:
    topology = _scan(
        "class A:\n"
        "    def reassigned(receiver):\n"
        "        receiver.item = 1\n"
        "        receiver = object()\n"
        "        receiver.other = 2\n"
        "    @classmethod\n"
        "    def decorated(receiver):\n"
        "        receiver.member = 1\n"
        "    def nested(receiver):\n"
        "        def closure():\n"
        "            receiver.item = 2\n"
        "        return closure\n"
    )
    assert not topology.instance_field_accesses


def test_lexical_binding_respects_first_parameter_not_name() -> None:
    topo = _scan(
        "class A:\n"
        "    def run(other, get):\n"
        "        get.member = 1\n"
        "        other.member = 2\n"
    )
    assert [(x.line, x.operation) for x in topo.instance_field_accesses] == [
        (4, "WRITE")
    ]


def test_merged_topology_deduplicates_source_bound_fields() -> None:
    topo = _scan("class C:\n    def m(self):\n        self.field = 1\n")
    merged = merge_topologies((topo, topo))
    assert merged.instance_field_accesses == topo.instance_field_accesses
    assert merged.instance_field_accesses[0].path == Path("pkg/example.py")
    assert (
        merged.instance_field_accesses[0].source_sha256
        == hashlib.sha256(
            b"class C:\n    def m(self):\n        self.field = 1\n"
        ).hexdigest()
    )


def test_authored_method_reference_is_not_instance_state_field_read() -> None:
    topology = _scan(
        "class Handler:\n"
        "    def action(first):\n"
        "        return first.member\n"
        "    def member(second):\n"
        "        return 1\n"
    )
    assert not topology.instance_field_accesses
