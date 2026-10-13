"""Source-bound class hierarchy uses native CPython C3, not spelling heuristics."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import merge_topologies


def _topology(source: str):
    path = "pkg/classes.py"
    snapshot = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {path: PythonUnit(path, source, ast.parse(source))}
    )
    return normalized_topology(RepositoryRelationGraph(snapshot))


def test_constructor_and_super_bound_to_native_mro() -> None:
    source = (
        "class Root:\n"
        "    def __init__(self): self.item = 1\n"
        "class Left(Root):\n"
        "    def __init__(arg): super().__init__()\n"
        "class Right(Root):\n"
        "    def __init__(what): super().__init__()\n"
        "class Child(Left, Right):\n"
        "    def __init__(different): super().__init__()\n"
    )
    topology = _topology(source)
    child = next(
        x
        for x in topology.constructor_lifecycles
        if x.owner_symbol_id.endswith(".Child")
    )
    assert child.mro_symbol_ids == (
        "pkg.classes.Child",
        "pkg.classes.Left",
        "pkg.classes.Right",
        "pkg.classes.Root",
    )
    assert child.constructor_symbol_id == "pkg.classes.Child.__init__"
    assert child.super_init_calls == (
        ("pkg.classes.Child.__init__", 8),
        ("pkg.classes.Left.__init__", 4),
        ("pkg.classes.Right.__init__", 6),
    )
    assert child.capability == "SOURCE_BOUND_CPYTHON_C3_OPEN_RUNTIME"
    assert child.source_sha256 == hashlib.sha256(source.encode()).hexdigest()
    assert (
        merge_topologies((topology, topology)).constructor_lifecycles
        == topology.constructor_lifecycles
    )


def test_constructor_inheritance_and_parameter_renaming() -> None:
    a = _topology(
        "class Base:\n    def __init__(anyone): pass\nclass Deriv(Base): pass\n"
    )
    b = _topology(
        "class Base:\n    def __init__(renamed): pass\nclass Deriv(Base): pass\n"
    )
    child = next(
        x for x in a.constructor_lifecycles if x.owner_symbol_id.endswith(".Deriv")
    )
    assert child.constructor_symbol_id == "pkg.classes.Base.__init__"
    assert [
        (x.constructor_symbol_id, x.mro_symbol_ids) for x in a.constructor_lifecycles
    ] == [(x.constructor_symbol_id, x.mro_symbol_ids) for x in b.constructor_lifecycles]


def test_unbound_external_decorator_dynamic_base_and_new_are_unknown() -> None:
    topology = _topology(
        "class Good:\n    pass\n"
        "class External(UnknownClass):\n    pass\n"
        "@change\nclass Decorated(Good):\n    pass\n"
        "class Dynamic(Good, f()):\n    pass\n"
        "class NewOverride(Good):\n    def __new__(cls): return object.__new__(cls)\n"
    )
    assert all(
        x.capability == "UNKNOWN_DYNAMIC_OR_UNBOUND_HIERARCHY"
        for x in topology.constructor_lifecycles
        if not x.owner_symbol_id.endswith(".Good")
    )
    assert all(
        not x.constructor_symbol_id and not x.mro_symbol_ids
        for x in topology.constructor_lifecycles
        if x.capability.startswith("UNKNOWN")
    )


def test_c3_inconsistency_becomes_unknown_not_fallback_mro() -> None:
    topology = _topology(
        "class X: pass\nclass Y: pass\n"
        "class A(X,Y): pass\nclass B(Y,X): pass\n"
        "class C(A,B): pass\n"
    )
    c = next(
        x for x in topology.constructor_lifecycles if x.owner_symbol_id.endswith(".C")
    )
    assert c.capability == "UNKNOWN_DYNAMIC_OR_UNBOUND_HIERARCHY"
    assert c.mro_symbol_ids == ()
    assert c.path == Path("pkg/classes.py")


def test_nested_super_and_non_constructor_are_not_counted() -> None:
    topo = _topology(
        "class Root:\n    def __init__(self): pass\n"
        "class Child(Root):\n"
        "    def __init__(person):\n"
        "        def later(): super().__init__()\n"
        "        action = lambda: super().__init__()\n"
        "        return None\n"
    )
    child = next(
        x for x in topo.constructor_lifecycles if x.owner_symbol_id.endswith(".Child")
    )
    assert child.super_init_calls == ()


def test_shadowed_super_is_not_reported_as_builtin() -> None:
    local = _topology(
        "class Parent: pass\n"
        "class Child(Parent):\n"
        "    def __init__(self):\n"
        "        super = lambda: self\n"
        "        super().__init__()\n"
    )
    child = next(
        x for x in local.constructor_lifecycles if x.owner_symbol_id.endswith(".Child")
    )
    assert child.super_init_calls == ()
    module = _topology(
        "super = lambda: None\n"
        "class Parent: pass\n"
        "class Child(Parent):\n"
        "    def __init__(self): super().__init__()\n"
    )
    child = next(
        x for x in module.constructor_lifecycles if x.owner_symbol_id.endswith(".Child")
    )
    assert child.super_init_calls == ()
