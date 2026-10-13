"""Source-domain positive and adverse CFG witnesses for R2-04."""

from __future__ import annotations

import ast

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import merge_topologies


def _find(source: str):
    path = "pkg/thing.py"
    snap = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {path: PythonUnit(path, source, ast.parse(source))}
    )
    return normalized_topology(RepositoryRelationGraph(snap)).field_initializations


def test_constructor_always_assigns_before_later_method() -> None:
    rows = _find(
        "class Result:\n"
        "    def __init__(actual, flag):\n"
        "        if flag:\n"
        "            actual.payload = 1\n"
        "        else:\n"
        "            actual.payload = 2\n"
        "    def read(actual):\n"
        "        return actual.payload\n"
    )
    assert [(r.line, r.verdict) for r in rows] == [(8, "MUST_AT_READ")]
    assert rows[0].witness_lines == (4, 6)
    assert rows[0].capability.endswith("OPEN_RUNTIME")


def test_partial_ctor_branch_unproven_no_missing_bug() -> None:
    rows = _find(
        "class Result:\n"
        "    def __init__(actual, flag):\n"
        "        if flag:\n"
        "            actual.payload = 1\n"
        "    def read(actual):\n"
        "        return actual.payload\n"
    )
    assert [(r.verdict, r.witness_lines) for r in rows] == [("UNKNOWN", ())]


def test_return_vs_raise_are_different_normal_paths() -> None:
    source = (
        "class A:\n"
        "    def __init__(ref, flag):\n"
        "        if flag:\n"
        "            raise ValueError\n"
        "        ref.value = 1\n"
        "    def read(ref):\n"
        "        return ref.value\n"
    )
    rows = _find(source)
    assert [(r.line, r.verdict) for r in rows] == [(7, "MUST_AT_READ")]


def test_unknown_call_delete_and_loop_kill_must() -> None:
    rows = _find(
        "class A:\n"
        "    def __init__(ref):\n"
        "        ref.value = 1\n"
        "    def use(ref, fn):\n"
        "        a = ref.value\n"
        "        fn(ref)\n"
        "        b = ref.value\n"
        "        ref.value = 2\n"
        "        del ref.value\n"
        "        c = ref.value\n"
        "        ref.value = 3\n"
        "        for _ in (): pass\n"
        "        return ref.value\n"
    )
    assert [(r.line, r.verdict) for r in rows] == [
        # A sibling method can delete the constructor field before this call.
        (5, "UNKNOWN"),
        (7, "UNKNOWN"),
        (10, "UNKNOWN"),
        (13, "UNKNOWN"),
    ]


def test_unsupported_exception_and_inheritance_do_not_fake_must() -> None:
    rows = _find(
        "class Base:\n"
        "    def __init__(ref):\n"
        "        ref.value = 1\n"
        "class Derived(Base):\n"
        "    def read(ref):\n"
        "        return ref.value\n"
        "class Plain:\n"
        "    def __init__(ref):\n"
        "        try:\n"
        "            ref.value = 1\n"
        "        except Exception:\n"
        "            pass\n"
        "    def read(ref):\n"
        "        return ref.value\n"
    )
    assert [(r.verdict, r.witness_lines) for r in rows] == [
        ("UNKNOWN", ()),
        ("UNKNOWN", ()),
    ]


def test_topology_replay_deduplicates_and_names_do_not_change_verdict() -> None:
    text = "class A:\n    def run(ref):\n        ref.x = 1\n        return ref.x\n"
    first = _find(text)
    second = _find(text.replace("ref", "anything").replace(".x", ".queue_get"))
    assert (
        [row.verdict for row in first]
        == [row.verdict for row in second]
        == ["MUST_AT_READ"]
    )
    path = "pkg/thing.py"
    snap = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {path: PythonUnit(path, text, ast.parse(text))}
    )
    topo = normalized_topology(RepositoryRelationGraph(snap))
    assert (
        merge_topologies((topo, topo)).field_initializations
        == topo.field_initializations
    )


def test_annotation_without_assignment_is_not_initialized() -> None:
    rows = _find(
        "class A:\n"
        "    def __init__(ref):\n"
        "        ref.value: int\n"
        "    def use(ref):\n"
        "        return ref.value\n"
    )
    assert [(r.verdict, r.witness_lines) for r in rows] == [("UNKNOWN", ())]


def test_delayed_closure_reads_are_not_must_at_creation() -> None:
    rows = _find(
        "class A:\n"
        "    def run(ref):\n"
        "        ref.value = 1\n"
        "        return lambda: ref.value\n"
    )
    assert not rows


def test_constructor_callback_before_assignment_cannot_certify_later_reads() -> None:
    rows = _find(
        "class A:\n"
        "    def __init__(ref, callback):\n"
        "        callback(ref)\n"
        "        ref.value = 1\n"
        "    def use(ref):\n"
        "        return ref.value\n"
    )
    assert [(r.verdict, r.witness_lines) for r in rows] == [("UNKNOWN", ())]


def test_local_alias_remains_safe_but_global_publication_kills() -> None:
    safe = _find(
        "class A:\n"
        "    def __init__(self):\n"
        "        self.field = 1\n"
        "    def inspect(self):\n"
        "        local = self\n"
        "        again = local\n"
        "        return self.field\n"
    )
    escaped = _find(
        "class A:\n"
        "    def __init__(self):\n"
        "        self.field = 1\n"
        "    def inspect(self):\n"
        "        global exported\n"
        "        local = self\n"
        "        again = local\n"
        "        exported = again\n"
        "        return self.field\n"
    )
    assert [(r.verdict, r.witness_lines) for r in safe] == [("MUST_AT_READ", (3,))]
    assert [(r.verdict, r.witness_lines) for r in escaped] == [("UNKNOWN", ())]


def test_receiver_passed_to_unknown_callee_cannot_be_repaired_by_local_write() -> None:
    rows = _find(
        "class A:\n"
        "    def invoke(obj, callback):\n"
        "        obj.state = 1\n"
        "        callback(obj)\n"
        "        obj.state = 2\n"
        "        return obj.state\n"
    )
    assert [(r.verdict, r.witness_lines) for r in rows] == [("UNKNOWN", ())]


def test_other_method_can_delete_constructor_field_before_read() -> None:
    rows = _find(
        "class A:\n"
        "    def __init__(ref):\n"
        "        ref.field = 1\n"
        "    def forget(ref):\n"
        "        del ref.field\n"
        "    def observe(ref):\n"
        "        return ref.field\n"
    )
    assert [(r.verdict, r.witness_lines) for r in rows] == [("UNKNOWN", ())]


def test_local_container_not_global_but_global_container_publication_kills() -> None:
    local = _find(
        "class A:\n"
        "    def run(this):\n"
        "        this.value = 1\n"
        "        container = [this]\n"
        "        return this.value\n"
    )
    globalized = _find(
        "class A:\n"
        "    def run(this):\n"
        "        global registry\n"
        "        this.value = 1\n"
        "        container = [this]\n"
        "        registry = container\n"
        "        return this.value\n"
    )
    assert [r.verdict for r in local] == ["MUST_AT_READ"]
    assert [r.verdict for r in globalized] == ["UNKNOWN"]


def test_name_independence_for_alias_transfer_and_escape() -> None:
    first = _find(
        "class A:\n"
        "    def run(ref):\n"
        "        ref.value = 1\n"
        "        alias = ref\n"
        "        callback(alias)\n"
        "        return ref.value\n"
    )
    renamed = _find(
        "class A:\n"
        "    def run(entry):\n"
        "        entry.value = 1\n"
        "        different = entry\n"
        "        callback(different)\n"
        "        return entry.value\n"
    )
    assert [r.verdict for r in first] == [r.verdict for r in renamed] == ["UNKNOWN"]


def test_constructor_can_publish_self_before_assigning_field() -> None:
    rows = _find(
        "class A:\n"
        "    def __init__(obj):\n"
        "        global registry\n"
        "        registry = obj\n"
        "        obj.field = 1\n"
        "    def read(obj):\n"
        "        return obj.field\n"
    )
    assert [(r.verdict, r.witness_lines) for r in rows] == [("UNKNOWN", ())]


def test_sibling_alias_delete_and_augassign_invalidate_constructor_seed() -> None:
    deleted = _find(
        "class A:\n"
        "    def __init__(ref):\n"
        "        ref.field = 1\n"
        "    def mutate(ref):\n"
        "        a = ref\n"
        "        del a.field\n"
        "    def read(ref):\n"
        "        return ref.field\n"
    )
    augmented = _find(
        "class A:\n"
        "    def __init__(ref):\n"
        "        ref.field = 1\n"
        "    def mutate(ref):\n"
        "        a = ref\n"
        "        a.field += 1\n"
        "    def read(ref):\n"
        "        return ref.field\n"
    )
    assert [row.verdict for row in deleted] == ["UNKNOWN"]
    assert [row.verdict for row in augmented] == ["UNKNOWN"]


def test_sibling_callback_receiving_alias_invalidates_constructor_seed() -> None:
    rows = _find(
        "class A:\n"
        "    def __init__(ref):\n"
        "        ref.field = 1\n"
        "    def publish(ref, fn):\n"
        "        transfer = ref\n"
        "        fn(transfer)\n"
        "    def read(ref):\n"
        "        return ref.field\n"
    )
    assert [r.verdict for r in rows] == ["UNKNOWN"]
