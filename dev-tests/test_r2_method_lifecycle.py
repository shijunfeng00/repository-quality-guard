"""R2 method entry contracts: real source-bound dispatch, not name heuristics.

Each asserted property is a positive or negative behavioral contract of the
normalized topology. Class names and variable names convey no semantic role.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import merge_topologies


def _fact(source: str, owner: str):
    path = "pkg/lifecycle.py"
    snapshot = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {path: PythonUnit(path, source, ast.parse(source))}
    )
    topology = normalized_topology(RepositoryRelationGraph(snapshot))
    cls = next(
        fact
        for fact in topology.constructor_lifecycles
        if fact.owner_symbol_id == f"pkg.lifecycle.{owner}"
    )
    assert merge_topologies((topology, topology)).constructor_lifecycles == (
        topology.constructor_lifecycles
    )
    assert cls.path == Path(path)
    return cls


def test_inherited_and_overridden_entries_follow_builtin_c3() -> None:
    src = (
        "class Root:\n"
        "    def __init__(self): self.value = 1\n"
        "    def act(self): return self.value\n"
        "class Left(Root):\n"
        "    def act(self): return 2\n"
        "class Right(Root):\n"
        "    def check(self): return 3\n"
        "class Child(Left, Right):\n"
        "    def fetch(self): return self.act()\n"
    )
    cls = _fact(src, "Child")
    assert cls.constructor_symbol_id == "pkg.lifecycle.Root.__init__"
    assert dict(cls.post_init_method_entries) == {
        "act": "pkg.lifecycle.Left.act",
        "check": "pkg.lifecycle.Right.check",
        "fetch": "pkg.lifecycle.Child.fetch",
    }
    assert cls.unknown_method_entries == ()
    assert cls.capability.endswith("OPEN_RUNTIME")
    # Independent oracle: CPython's actual method resolution on this fixture.
    ns: dict = {}
    exec(compile(src, "<local-test-fixture>", "exec"), ns)
    instance = ns["Child"]()
    assert (instance.act(), instance.check(), instance.fetch()) == (2, 3, 2)
    assert tuple(cls.__name__ for cls in type(instance).__mro__[:-1]) == (
        "Child",
        "Left",
        "Right",
        "Root",
    )


def test_source_rename_does_not_change_resolution_shape() -> None:
    left = _fact("class A:\n def run(self): return 1\nclass B(A): pass\n", "B")
    right = _fact("class Y:\n def run(q): return 1\nclass Z(Y): pass\n", "Z")
    assert left.post_init_method_entries == (("run", "pkg.lifecycle.A.run"),)
    assert right.post_init_method_entries == (("run", "pkg.lifecycle.Y.run"),)
    assert left.capability == right.capability


def test_decorated_method_and_dynamic_dispatch_remain_unknown() -> None:
    decorated = _fact(
        "def patch(fn): return fn\n"
        "class Base:\n"
        " @patch\n def emit(self): return 1\n"
        "class Child(Base):\n def direct(self): return 2\n",
        "Child",
    )
    assert dict(decorated.post_init_method_entries) == {
        "direct": "pkg.lifecycle.Child.direct"
    }
    assert decorated.unknown_method_entries == ("emit",)
    dynamic = _fact(
        "class A:\n"
        " def __getattribute__(self, name): return 0\n"
        " def run(self): return 1\n",
        "A",
    )
    assert not dynamic.post_init_method_entries
    assert "run" in dynamic.unknown_method_entries


def test_dynamic_hierarchy_cannot_claim_post_init_entry() -> None:
    f = _fact("class Child(unknown()):\n def foo(self): return 1\n", "Child")
    assert f.capability.startswith("UNKNOWN")
    assert not f.post_init_method_entries
    assert not f.unknown_method_entries


def test_super_observation_is_not_constructor_guarantee() -> None:
    src = (
        "class Parent:\n"
        " def __init__(self): self.payload = 1\n"
        " def read(self): return self.payload\n"
        "class Child(Parent):\n"
        " def __init__(self, flag):\n"
        "  if flag: super().__init__()\n"
    )
    cls = _fact(src, "Child")
    assert cls.constructor_symbol_id == "pkg.lifecycle.Child.__init__"
    assert cls.super_init_calls == (("pkg.lifecycle.Child.__init__", 6),)
    assert cls.post_init_method_entries == (("read", "pkg.lifecycle.Parent.read"),)
    # A syntactic super call does not establish all-normal-exit initialization.
    ns: dict = {}
    exec(compile(src, "<local-test-fixture>", "exec"), ns)
    assert ns["Child"](True).read() == 1
    try:
        ns["Child"](False).read()
    except AttributeError:
        pass
    else:
        raise AssertionError("conditional super must not prove initialized field")


def test_class_namespace_shadow_is_not_a_method_entry() -> None:
    fact = _fact(
        "class Base:\n def routine(self): return 1\n"
        "class Child(Base):\n routine = 123\n def stable(self): return 2\n",
        "Child",
    )
    assert (
        "routine",
        "pkg.lifecycle.Base.routine",
    ) not in fact.post_init_method_entries
    assert "routine" in fact.unknown_method_entries
    assert fact.post_init_method_entries == (("stable", "pkg.lifecycle.Child.stable"),)


def test_dynamic_class_body_does_not_certify_method_entry() -> None:
    fact = _fact(
        "condition = True\n"
        "class Child:\n"
        " def routine(self): return 1\n"
        " if condition: routine = lambda self: 2\n",
        "Child",
    )
    assert not fact.post_init_method_entries
    assert "routine" in fact.unknown_method_entries
