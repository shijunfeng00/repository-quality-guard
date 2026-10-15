"""Behavioral positive/negative cases for real receiver identity and missing results."""

from __future__ import annotations

from pathlib import Path

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


def _findings(tmp_path: Path, source: str) -> list[tuple[str, int]]:
    """Return actual RQG rule findings, never implementation helper internals."""
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")
    scan = RepositoryScanner(tmp_path, GuardConfig()).scan()
    return [
        (item.code, item.line)
        for item in scan.findings
        if item.code in {"QG003", "QG004", "QG026"}
    ]


def test_zero_argument_authored_get_is_not_dictionary_read(tmp_path: Path) -> None:
    source = (
        "class Provider:\n"
        "    def get(self):\n        return 42\n"
        "def issue(provider: Provider):\n    return provider.get()\n"
    )
    assert _findings(tmp_path, source) == []
    module = {}
    exec(source, module)
    assert module["issue"](module["Provider"]()) == 42


def test_custom_method_with_key_and_grandparent_override(tmp_path: Path) -> None:
    source = (
        "class Source:\n    def get(self, key):\n        return key.upper()\n"
        "class Middle(Source):\n    pass\n"
        "class Destination(Middle):\n    pass\n"
        "def convert(value: Destination):\n    return value.get('x')\n"
    )
    assert _findings(tmp_path, source) == []
    module = {}
    exec(source, module)
    assert module["convert"](module["Destination"]()) == "X"


def test_mapping_grandparent_and_self_dict_remain_detected(tmp_path: Path) -> None:
    source = (
        "class Storage(dict):\n    pass\n"
        "class Middle(Storage):\n    pass\n"
        "class Repository(Middle):\n"
        "    def __init__(self):\n        self._data = {}\n"
        "    def read(self, key):\n"
        "        return self._data.get(key, 'UNKNOWN')\n"
        "    def lookup(self, key):\n"
        "        return self.get(key, 'UNKNOWN')\n"
    )
    found = _findings(tmp_path, source)
    assert ("QG004", 9) in found
    assert ("QG004", 11) in found


def test_wrapper_get_does_not_hide_inner_mapping_fallback(tmp_path: Path) -> None:
    source = (
        "class Repository:\n"
        "    def __init__(self):\n        self._data: dict[str,str] = {}\n"
        "    def get(self, key):\n        return self._data.get(key, 'UNKNOWN')\n"
        "def read(value: Repository):\n    return value.get('required')\n"
    )
    found = _findings(tmp_path, source)
    assert ("QG004", 5) in found
    assert ("QG003", 7) not in found
    assert ("QG026", 7) not in found


def test_optional_return_none_is_declared_result_not_forged_identifier(
    tmp_path: Path,
) -> None:
    source = (
        "from typing import Optional\n"
        "class Node: pass\n"
        "def find(index: dict[str, Node], key: str) -> Optional[Node]:\n"
        "    return index.get(key, None)\n"
    )
    assert _findings(tmp_path, source) == []
    module = {}
    exec(source, module)
    assert module["find"]({}, "missing") is None


def test_optional_return_must_not_hide_required_schema_key(tmp_path: Path) -> None:
    source = (
        "from typing import Optional, TypedDict\n"
        "class Payload(TypedDict):\n    id: str\n"
        "def consume(record: Payload) -> Optional[str]:\n"
        "    return record.get('id', None)\n"
    )
    assert ("QG004", 5) in _findings(tmp_path, source)


def test_unknown_sentinel_still_detected_even_for_optional_result(
    tmp_path: Path,
) -> None:
    source = (
        "from typing import Optional\n"
        "def consume(record: dict[str,str], key: str) -> Optional[str]:\n"
        "    return record.get(key, 'UNKNOWN')\n"
    )
    assert ("QG004", 3) in _findings(tmp_path, source)


def test_sparse_producer_with_valid_absent_case_accepts_empty_collection(
    tmp_path: Path,
) -> None:
    source = (
        "class Graph:\n"
        "    def __init__(self, nodes, edges):\n"
        "        self.nodes = set(nodes)\n"
        "        incoming = {}\n"
        "        for source, target in edges:\n"
        "            assert source in nodes and target in nodes\n"
        "            incoming.setdefault(target, []).append(source)\n"
        "        self._incoming = incoming\n"
        "    def parents(self, target: str) -> list[str]:\n"
        "        assert target in self.nodes\n"
        "        return self._incoming.get(target, [])\n"
    )
    assert ("QG004", 11) not in _findings(tmp_path, source)
    module = {}
    exec(source, module)
    graph = module["Graph"]({"A", "B"}, [("B", "A")])
    assert graph.parents("A") == ["B"]
    assert graph.parents("B") == []


def test_empty_without_a_sparse_producer_remains_a_review_candidate(
    tmp_path: Path,
) -> None:
    source = (
        "def read(payload: dict[str,str], key: str) -> str:\n"
        "    return payload.get(key, '')\n"
    )
    assert ("QG004", 2) in _findings(tmp_path, source)


def test_optional_return_does_not_hide_required_self_typed_dict_field(
    tmp_path: Path,
) -> None:
    source = (
        "from typing import Optional, TypedDict\n"
        "class Payload(TypedDict):\n    id: str\n"
        "class Repository:\n"
        "    def __init__(self):\n"
        "        self._data: Payload = {'id': 'A'}\n"
        "    def read(self) -> Optional[str]:\n"
        "        return self._data.get('id', None)\n"
    )
    assert ("QG004", 8) in _findings(tmp_path, source)


def test_multiple_inheritance_with_ambiguous_get_is_not_proven_mapping(
    tmp_path: Path,
) -> None:
    source = (
        "class MethodMixin:\n    def get(self, key):\n        return key\n"
        "class MappingBase(dict):\n    pass\n"
        "class Combination(MethodMixin, MappingBase):\n    pass\n"
        "def execute(value: Combination):\n"
        "    return value.get('id')\n"
    )
    assert ("QG003", 9) not in _findings(tmp_path, source)


def test_builtin_mapping_get_without_required_key_is_not_accepted(
    tmp_path: Path,
) -> None:
    source = "def read(value: dict[str, int]):\n    return value.get()\n"
    assert ("QG003", 2) in _findings(tmp_path, source)
