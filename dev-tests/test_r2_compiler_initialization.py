"""Compiler R2 instance-initialization candidates keep source-bound identity."""

from __future__ import annotations

import ast
import hashlib

import pytest

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.compiler_contracts import r2_initialization_observations
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import merge_topologies


SOURCE = (
    "class Holder:\n"
    "    def __init__(instance):\n"
    "        pass\n"
    "    def populate(instance, value):\n"
    "        instance.token = value\n"
)


def _input(source: str = SOURCE, location: int = 18):
    path = "package/example.py"
    snapshot = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {path: PythonUnit(path, source, ast.parse(source))}
    )
    graph = normalized_topology(RepositoryRelationGraph(snapshot))
    item = {
        "native_rule": "reportUninitializedInstanceVariable",
        "source_path": path,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "line": 5,
        "column": location,
    }
    return snapshot, graph, item


PROVIDER = {"pyright_sha256": "a" * 64, "typeshed_sha256": "b" * 64}


def test_bound_field_is_observation_not_false_must_proof() -> None:
    snapshot, topology, evidence = _input()
    facts = r2_initialization_observations(
        snapshot, [evidence], topology, provider_identity=PROVIDER
    )
    assert len(facts) == 1
    assert facts[0].owner_symbol_id == "package.example.Holder"
    assert facts[0].member == "token"
    assert facts[0].binding == "BOUND_CLASS_ATTRIBUTE_STORE"
    assert facts[0].capability == "TYPECHECKER_OBSERVATION_ONLY"
    # One normalized graph; no secondary symbol identity or inferred MUST result.
    assert (
        merge_topologies(
            (topology, type(topology)((), (), (), compiler_diagnostics=facts))
        ).compiler_diagnostics
        == facts
    )


def test_receiver_and_field_renaming_is_semantically_invariant() -> None:
    source = SOURCE.replace("instance", "receiver").replace("token", "value_field")
    snapshot, topology, item = _input(source, location=18)
    # Actual source coordinates come from the compiler; move the diagnostic
    # anchor to the renamed attribute token, without inferring effects from text.
    item["column"] = 18
    facts = r2_initialization_observations(
        snapshot, [item], topology, provider_identity=PROVIDER
    )
    assert len(facts) == 1
    assert facts[0].member == "value_field"
    assert facts[0].binding == "BOUND_CLASS_ATTRIBUTE_STORE"


def test_wrong_column_is_explicit_unknown_not_guessed_by_name() -> None:
    snapshot, topology, item = _input(location=9)
    facts = r2_initialization_observations(
        snapshot, [item], topology, provider_identity=PROVIDER
    )
    assert facts[0].binding == "UNKNOWN_SOURCE_SHAPE"
    assert not facts[0].owner_symbol_id


@pytest.mark.parametrize("tamper", ["source_sha256", "source_path", "line"])
def test_forged_or_stale_source_rejected(tamper: str) -> None:
    snapshot, topology, item = _input()
    item[tamper] = {
        "source_sha256": "wrong",
        "source_path": "elsewhere.py",
        "line": 999,
    }[tamper]
    with pytest.raises(ValueError):
        r2_initialization_observations(
            snapshot, [item], topology, provider_identity=PROVIDER
        )


def test_unsupported_diagnostic_not_misclassified() -> None:
    snapshot, topology, item = _input()
    item["native_rule"] = "reportArgumentType"
    assert not r2_initialization_observations(
        snapshot, [item], topology, provider_identity=PROVIDER
    )


def test_missing_provider_identity_rejected() -> None:
    snapshot, topology, item = _input()
    with pytest.raises(ValueError):
        r2_initialization_observations(snapshot, [item], topology, provider_identity={})
