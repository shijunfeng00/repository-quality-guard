"""R3 source-authoritative producer contract regression."""

from __future__ import annotations

from pathlib import Path

from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.scanner import RepositoryScanner


def _findings(tmp_path: Path, source: str):
    (tmp_path / "contract.py").write_text(source, encoding="utf8")
    config = GuardConfig(include_tests=True, require_docstrings=False)
    snapshot = directory_analysis_snapshot(tmp_path, config)
    topology = normalized_topology(RepositoryRelationGraph(snapshot))
    return [
        finding
        for finding in RepositoryScanner(
            tmp_path, config, analysis_snapshot=snapshot, topology=topology
        ).scan().findings
        if finding.code == "QG216"
    ]


def test_missing_required_field_in_literal_return(tmp_path: Path):
    source = (
        "from typing import TypedDict, NotRequired\n"
        "class Item(TypedDict):\n"
        "  identifier: str\n"
        "  label: NotRequired[str]\n"
        "def provide() -> Item:\n"
        "  return {'label': 'ok'}\n"
    )
    findings = _findings(tmp_path, source)
    assert len(findings) == 1
    assert findings[0].evidence["missing_required_fields"] == ["identifier"]
    assert findings[0].evidence["schema_field_lines"] == {"identifier": 3}
    assert findings[0].line == 6


def test_optional_field_or_complete_literal_is_not_a_violation(tmp_path: Path):
    source = (
        "from typing import TypedDict\n"
        "class Item(TypedDict, total=False):\n"
        "  identifier: str\n"
        "def provide() -> Item:\n"
        "  return {}\n"
    )
    assert not _findings(tmp_path, source)
    source = (
        "from typing import TypedDict\n"
        "class Item(TypedDict):\n"
        "  identifier: str\n"
        "def provide() -> Item:\n"
        "  return {'identifier': 'ok'}\n"
    )
    assert not _findings(tmp_path, source)


def test_dynamic_spread_and_open_key_cannot_certify_omission(tmp_path: Path):
    prefix = (
        "from typing import TypedDict\n"
        "class Item(TypedDict):\n"
        "  identifier: str\n"
        "def provide(extra: dict) -> Item:\n"
    )
    assert not _findings(tmp_path, prefix + "  return {**extra}\n")
    assert not _findings(tmp_path, prefix + "  return {next(iter(extra)): 1}\n")


def test_typed_assignment_literal_uses_same_schema_contract(tmp_path: Path):
    source = (
        "from typing import TypedDict\n"
        "class Item(TypedDict):\n"
        "  identifier: str\n"
        "  title: str\n"
        "record: Item = {'identifier': 'ok'}\n"
    )
    findings = _findings(tmp_path, source)
    assert len(findings) == 1
    assert findings[0].evidence["missing_required_fields"] == ["title"]


def test_container_return_does_not_masquerade_as_mapping_producer(tmp_path: Path):
    source = (
        "from typing import TypedDict\n"
        "class Item(TypedDict):\n"
        "  identifier: str\n"
        "def provide() -> list[Item]:\n"
        "  return {}\n"
    )
    assert not _findings(tmp_path, source)


def test_nested_branch_and_caught_failure_return_use_same_schema(tmp_path: Path):
    source = (
        "from typing import TypedDict\n"
        "class Response(TypedDict):\n"
        "  status: str\n"
        "  payload: str\n"
        "def create(ok: bool) -> Response:\n"
        "  if ok:\n"
        "    return {'status': 'ok', 'payload': 'ready'}\n"
        "  try:\n"
        "    raise ValueError()\n"
        "  except ValueError:\n"
        "    return {'status': 'failed'}\n"
    )
    findings = _findings(tmp_path, source)
    assert len(findings) == 1
    assert findings[0].line == 11
    assert findings[0].evidence["missing_required_fields"] == ["payload"]


def test_nested_function_literal_does_not_belong_to_outer_schema(tmp_path: Path):
    source = (
        "from typing import TypedDict\n"
        "class Response(TypedDict):\n"
        "  status: str\n"
        "def create() -> Response:\n"
        "  def nested():\n"
        "    return {}\n"
        "  return {'status': 'ok'}\n"
    )
    assert not _findings(tmp_path, source)


def test_nested_open_dict_remains_unknown(tmp_path: Path):
    source = (
        "from typing import TypedDict\n"
        "class Response(TypedDict):\n"
        "  status: str\n"
        "def create(extra: dict) -> Response:\n"
        "  if extra:\n"
        "    return {**extra}\n"
        "  return {'status': 'ok'}\n"
    )
    assert not _findings(tmp_path, source)
