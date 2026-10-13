"""R3-01 structural contract/producer facts; no developer-name heuristics."""

import ast
import hashlib
from pathlib import Path

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import merge_topologies


def _facts(source: str):
    path = "pkg/contracts.py"
    snapshot = RepositoryAnalysisSnapshot(
        None, "DIRECTORY", {path: PythonUnit(path, source, ast.parse(source))}
    )
    return normalized_topology(RepositoryRelationGraph(snapshot))


def test_schema_total_required_notrequired_alias_and_builtins() -> None:
    source = (
        "from typing import TypedDict as S, Required as R, NotRequired as N\n"
        "class Item(S, total=False):\n"
        "    x: R[int]\n"
        "    y: N[str]\n"
        "    z: float\n"
        "\n"
        "def create() -> Item:\n"
        "    return {'x': 1}\n"
        "record: Item = {'x': 1}\n"
    )
    topo = _facts(source)
    assert [
        (x.field, x.required, x.annotation, x.type_binding)
        for x in topo.typed_schema_fields
    ] == [
        ("x", True, "int", "BUILTIN"),
        ("y", False, "str", "BUILTIN"),
        ("z", False, "float", "BUILTIN"),
    ]
    assert [(x.kind, x.observed_literal_keys) for x in topo.typed_producers] == [
        ("DECLARED_RETURN", ()),
        ("OBSERVED_RETURN_LITERAL", ("x",)),
        ("ANNOTATED_ASSIGNMENT", ("x",)),
    ]
    assert all(
        x.source_sha256 == hashlib.sha256(source.encode()).hexdigest()
        for x in topo.typed_schema_fields
    )
    assert all(x.schema_symbol_id == "pkg.contracts.Item" for x in topo.typed_producers)
    assert merge_topologies((topo, topo)).typed_producers == topo.typed_producers


def test_module_qualified_typing_extensions_and_required_default() -> None:
    topo = _facts(
        "import typing_extensions as lib\n"
        "class Payload(lib.TypedDict):\n"
        "    required: str\n"
        "    optional: lib.NotRequired[int]\n"
    )
    assert [(x.field, x.required) for x in topo.typed_schema_fields] == [
        ("required", True),
        ("optional", False),
    ]


def test_fake_typeddict_and_rebound_import_cannot_certify() -> None:
    for source in (
        "class TypedDict: pass\nclass Contract(TypedDict):\n    key: str\n",
        "from typing import TypedDict\nTypedDict = factory\nclass Contract(TypedDict):\n    key: str\n",
        "from typing import TypedDict\nclass Contract(TypedDict, External):\n    key: str\n",
        "from typing import TypedDict\nclass Contract(TypedDict):\n    key: str = 'bad'\n",
    ):
        assert not _facts(source).typed_schema_fields


def test_rebound_optional_wrapper_is_not_silently_trusted() -> None:
    topo = _facts(
        "from typing import TypedDict, NotRequired\n"
        "NotRequired = external\n"
        "class Payload(TypedDict):\n"
        "    optional: NotRequired[str]\n"
    )
    assert topo.typed_schema_fields == ()


def test_schema_member_spelling_does_not_change_contract_semantics() -> None:
    for word in ("get_item", "mutable", "set_value", "queue", "safe_read"):
        topo = _facts(
            f"from typing import TypedDict\nclass X(TypedDict):\n    {word}: int\n"
        )
        assert len(topo.typed_schema_fields) == 1
        assert topo.typed_schema_fields[0].required
        assert topo.typed_schema_fields[0].type_binding == "BUILTIN"


def test_open_annotation_preserves_source_but_never_claims_type_binding() -> None:
    topo = _facts(
        "from typing import TypedDict\n"
        "class Custom: pass\n"
        "class X(TypedDict):\n"
        "    value: Custom\n"
    )
    assert len(topo.typed_schema_fields) == 1
    assert topo.typed_schema_fields[0].type_binding == "OPEN_REFERENCE"
    assert topo.typed_schema_fields[0].path == Path("pkg/contracts.py")


def test_declared_container_method_producer_is_not_a_runtime_proof() -> None:
    topo = _facts(
        "from typing import TypedDict\n"
        "class Result(TypedDict):\n    value: int\n"
        "class Service:\n"
        "    async def load(self) -> list[Result]:\n"
        "        return [{'value': 3}]\n"
    )
    assert [(x.kind, x.producer_symbol_id) for x in topo.typed_producers] == [
        ("DECLARED_CONTAINER_RETURN", "pkg.contracts.Service.load")
    ]
    assert topo.typed_producers[0].capability == "SOURCE_DECLARED_PRODUCER_OPEN_RUNTIME"


def test_branch_rebinding_import_and_builtin_invalidation() -> None:
    invalid = (
        "from typing import TypedDict\n"
        "if condition:\n    TypedDict = external\n"
        "class Result(TypedDict):\n    value: int\n"
    )
    assert _facts(invalid).typed_schema_fields == ()
    builtin = _facts(
        "from typing import TypedDict\n"
        "if condition:\n    int = external\n"
        "class Result(TypedDict):\n    value: int\n"
    )
    assert builtin.typed_schema_fields[0].type_binding == "OPEN_REFERENCE"
    import_alias = _facts(
        "import typing as t\nimport mystery as t\n"
        "class Result(t.TypedDict):\n    value: str\n"
    )
    assert import_alias.typed_schema_fields == ()


def test_ambiguous_duplicate_schema_field_rejected() -> None:
    topo = _facts(
        "from typing import TypedDict\n"
        "class Payload(TypedDict):\n"
        "    value: int\n"
        "    value: str\n"
    )
    assert not topo.typed_schema_fields
