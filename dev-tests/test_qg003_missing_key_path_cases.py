"""Business-output oracles for missing-key cases, not parser implementation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


@pytest.mark.parametrize(
    ("source", "expected_qg003", "observed"),
    [
        (
            """LOOKUP: dict[str, int] = {key: len(key) for key in ('A', 'BB')}
def _index(key: str) -> int | None:
    return LOOKUP.get(key)
def resolve(key: str) -> int:
    entry = _index(key)
    if entry is not None:
        return entry
    return -1
""",
            0,
            ("resolve", ("BB",), 2),
        ),
        (
            """LOOKUP: dict[str, int] = {key: len(key) for key in ('A', 'BB')}
def _index(key: str) -> int | None:
    return LOOKUP.get(key)
def resolve(key: str) -> int:
    entry = _index(key)
    if entry is not None:
        return entry
    return -1
""",
            0,
            ("resolve", ("unknown",), -1),
        ),
        (
            """LOOKUP: dict[str, int] = {'id': 1}
def _index(key: str) -> int | None:
    return LOOKUP.get(key)
def consume(key: str):
    entry = _index(key)
    return entry or 999
""",
            1,
            ("consume", ("missing",), 999),
        ),
        (
            """LOOKUP: dict[str, int] = {'id': 1}
def _index(key: str) -> int | None:
    return LOOKUP.get(key)
def consume(key: str):
    entry = _index(key)
    if entry is not None:
        return entry
    return 0
def second(key: str):
    return _index(key)
""",
            1,
            ("second", ("missing",), None),
        ),
        (
            """from typing import TypedDict
class Payload(TypedDict):
    id: str
def consume(payload: Payload) -> str:
    return payload.get('id')
""",
            1,
            ("consume", ({"id": "K"},), "K"),
        ),
        (
            """class Index:
    def __init__(self, keys):
        self._values: dict[str, int] = {}
        for key in keys:
            self._values[key] = len(key)
    def classify(self, key: str) -> str:
        found = self._values.get(key)
        if found is None:
            return 'absent'
        else:
            return 'present'
""",
            0,
            ("Index.classify", ("B",), "absent"),
        ),
        (
            """class Index:
    def __init__(self, keys):
        self._values: dict[str, int] = {}
        for key in keys:
            self._values[key] = len(key)
    def classify(self, key: str) -> str:
        found = self._values.get(key)
        if found is None:
            pass
        else:
            pass
        return str(found or 'UNKNOWN')
""",
            1,
            ("Index.classify", ("B",), "UNKNOWN"),
        ),
    ],
    ids=[
        "known-key",
        "legitimate-miss",
        "unchecked-default",
        "second-unguarded-caller",
        "required-typed-dict",
        "sparse-case",
        "fake-sentinel",
    ],
)
def test_missing_key_business_cases(
    tmp_path: Path, source: str, expected_qg003: int, observed: tuple
) -> None:
    """Assert actual business results and reported rule findings together."""
    (tmp_path / "subject.py").write_text(source, encoding="utf-8")
    report = RepositoryScanner(tmp_path, GuardConfig()).scan()
    actual = sum(item.code == "QG003" for item in report.findings)
    assert actual == expected_qg003
    namespace: dict[str, object] = {}
    exec(compile(source, "subject.py", "exec"), namespace)
    qualname, args, expected = observed
    if qualname == "Index.classify":
        result = namespace["Index"](["A"]).classify(*args)
    else:
        result = namespace[qualname](*args)
    assert result == expected
