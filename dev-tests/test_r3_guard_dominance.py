"""R3 structured-flow guard evidence: soundness on positive/negative paths."""

import ast

from runtime.src.typed_guard_flow import presence_witnesses


def evidence(body: str):
    function = ast.parse(
        "def check(state):\n" + "".join("    " + ln + "\n" for ln in body.splitlines())
    ).body[0]
    return presence_witnesses(function, {"state"})


def test_positive_membership_dominates_direct_read():
    rows = evidence('if "value" in state:\n    return state["value"]\nreturn 0')
    assert {(loc[0], loc[3], line) for loc, line in rows.items()} == {(3, "value", 2)}


def test_negative_membership_early_return_or_raise():
    for stop in ("return 0", "raise ValueError()"):
        rows = evidence(f'if "value" not in state:\n    {stop}\nreturn state["value"]')
        assert {(loc[0], loc[3], line) for loc, line in rows.items()} == {
            (4, "value", 2)
        }


def test_unprotected_read_fails_even_when_schema_suggests_optional():
    assert evidence('return state["value"]') == {}
    assert evidence('if "value" in state:\n    pass\nreturn state["value"]') == {}


def test_both_branches_must_agree_on_normal_path():
    assert evidence(
        'if "value" in state:\n    pass\nelse:\n    return 0\nreturn state["value"]'
    )
    assert not evidence(
        'if "value" in state:\n    pass\nelse:\n    pass\nreturn state["value"]'
    )


def test_unknown_effect_and_mutation_kill_presence():
    for effect in (
        "unknown(state)",
        'del state["value"]',
        "state = external()",
        "awaitable(state)",
    ):
        assert not evidence(
            f'if "value" not in state:\n    return 0\n{effect}\nreturn state["value"]'
        )


def test_unrelated_conditional_preserves_known_presence():
    assert evidence(
        'if "value" not in state:\n    return 0\nif flag:\n    pass\nreturn state["value"]'
    )


def test_nested_if_uses_correct_branch():
    assert not evidence('if "value" not in state:\n    return state["value"]\nreturn 0')
    assert evidence(
        'if not ("value" not in state):\n    return state["value"]\nreturn 0'
    )


def test_unsupported_exception_path_does_not_prove_presence():
    assert not evidence(
        'if "value" not in state:\n    return 0\ntry:\n    may_fail()\nexcept Exception:\n    pass\nreturn state["value"]'
    )


def test_name_invariance_and_symbol_not_effect_guess():
    for field in ("get", "queue", "read_only", "set"):
        assert evidence(
            f'if "{field}" not in state:\n    return 0\nreturn state["{field}"]'
        )


def test_witness_arrives_in_production_normalized_topology():
    from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
    from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology

    source = (
        "from typing import TypedDict\n"
        "class Record(TypedDict, total=False):\n    value: int\n"
        "def process(state: Record):\n"
        '    if "value" not in state:\n        raise ValueError()\n'
        '    return state["value"]\n'
    )
    snapshot = RepositoryAnalysisSnapshot(
        None,
        "DIRECTORY",
        {"sample.py": PythonUnit("sample.py", source, ast.parse(source))},
    )
    topo = normalized_topology(RepositoryRelationGraph(snapshot))
    assert len(topo.typed_consumers) == 1
    consumer = topo.typed_consumers[0]
    assert consumer.presence_witness_line == 5
    assert consumer.field == "value" and consumer.required is False


def test_receiver_alias_escape_kills_must_evidence():
    assert not evidence(
        'if "value" not in state:\n    return 0\nalias = state\ndel alias["value"]\nreturn state["value"]'
    )
    assert not evidence(
        'if "value" not in state:\n    return 0\nalias = [state]\ndel alias[0]["value"]\nreturn state["value"]'
    )
