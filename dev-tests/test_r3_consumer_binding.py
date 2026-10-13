"""R3 source-bound consumer/guard facts; tests invariant under names."""

import ast
import hashlib

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import merge_topologies


def topology(files):
    snapshot = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {p: PythonUnit(p, s, ast.parse(s)) for p, s in files.items()}
    )
    return normalized_topology(RepositoryRelationGraph(snapshot))


def test_relative_import_and_key_read_are_source_bound():
    schema = "from typing import TypedDict\nclass Payload(TypedDict, total=False):\n    value: int\n"
    user = (
        "from .schema import Payload as Alias\n"
        'def consumer(x: Alias):\n    return x["value"]\n'
    )
    result = topology({"pkg/schema.py": schema, "pkg/user.py": user})
    assert [
        (x.schema_symbol_id, x.field, x.required, x.consumer_symbol_id)
        for x in result.typed_consumers
    ] == [("pkg.schema.Payload", "value", False, "pkg.user.consumer")]
    assert (
        result.typed_consumers[0].source_sha256
        == hashlib.sha256(user.encode()).hexdigest()
    )
    assert result.typed_consumers[0].guard_observation == "NONE"
    assert merge_topologies((result, result)).typed_consumers == result.typed_consumers


def test_if_membership_guard_observation_is_not_must():
    result = topology(
        {
            "schema.py": (
                "from typing import TypedDict\nclass P(TypedDict, total=False):\n    v: int\n"
                'def f(x: P):\n    if "v" in x:\n        return x["v"]\n    return 0\n'
            )
        }
    )
    assert [
        (f.guard_observation, f.guard_line, f.capability)
        for f in result.typed_consumers
    ] == [("KEY_PRESENT_BRANCH", 5, "SOURCE_BOUND_CONSUMER_OPEN_CFG_AND_RUNTIME")]


def test_fake_schema_and_rebinding_rejected():
    fake = 'class TypedDict: pass\nclass P(TypedDict):\n    v: int\ndef f(x: P):\n    return x["v"]\n'
    assert not topology({"fake.py": fake}).typed_consumers
    source = (
        "from typing import TypedDict\nclass P(TypedDict):\n    v: int\n"
        'def f(x: P):\n    x = external()\n    return x["v"]\n'
    )
    assert not topology({"t.py": source}).typed_consumers


def test_method_name_and_field_name_do_not_set_semantics():
    for word in ("get_value", "set_value", "queue", "mutable"):
        src = (
            f"from typing import TypedDict\nclass P(TypedDict):\n    {word}: str\n"
            f'def check(x: P):\n    return x["{word}"]\n'
        )
        facts = topology({"test.py": src}).typed_consumers
        assert len(facts) == 1 and facts[0].observation == "DIRECT_SUBSCRIPT_READ"
        assert facts[0].required is True


def test_dynamic_subscript_or_unknown_field_not_invented():
    source = (
        "from typing import TypedDict\nclass P(TypedDict):\n    v: int\n"
        'def f(x: P, key):\n    return x[key], x["missing"]\n'
    )
    assert topology({"test.py": source}).typed_consumers == ()


def test_nested_function_and_lambda_dont_steal_receiver():
    source = (
        "from typing import TypedDict\nclass P(TypedDict):\n    v: int\n"
        'def f(x: P):\n    def inner():\n        return x["v"]\n'
        '    return lambda: x["v"]\n'
    )
    assert topology({"test.py": source}).typed_consumers == ()


def test_imported_schema_rebind_rejected():
    schema = "from typing import TypedDict\nclass P(TypedDict):\n    v: int\n"
    user = 'from .schema import P\nP = arbitrary\ndef f(x: P):\n    return x["v"]\n'
    assert topology({"pkg/schema.py": schema, "pkg/use.py": user}).typed_consumers == ()


def test_competing_import_cannot_certify_schema() -> None:
    schema = "from typing import TypedDict\nclass P(TypedDict):\n    v: int\n"
    user = (
        'from .schema import P\nimport external as P\ndef f(x: P):\n    return x["v"]\n'
    )
    assert topology({"pkg/schema.py": schema, "pkg/use.py": user}).typed_consumers == ()


def test_fake_builtin_named_isinstance_cannot_certify_type_validation() -> None:
    source = (
        "from typing import TypedDict\nclass P(TypedDict):\n    v: int\n"
        "def isinstance(*a):\n    return True\n"
        'def f(x: P):\n    if isinstance(x["v"], int):\n'
        '        return x["v"]\n'
    )
    rows = topology({"test.py": source}).typed_consumers
    assert len(rows) == 2
    assert all(r.guard_observation == "NONE" for r in rows)
