"""Case-oracle regression for writable vs read-only input ownership."""

from pathlib import Path

import pytest

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def update(cache: dict):\n    cache['key'] = 1\n", 0),
        ("def step(instance):\n    instance.waiting = None\n", 0),
        ("def update(cache):\n    cache['key'] = 1\n", 0),
        ("from typing import MutableMapping\ndef update(cache: MutableMapping[str, int]):\n    cache['key'] = 1\n", 0),
        ("from typing import Mapping\ndef update(cache: Mapping[str, int]):\n    cache['key'] = 1\n", 1),
        ("from collections.abc import Sequence\ndef update(items: Sequence[int]):\n    items[0] = 1\n", 1),
        ("def update(items: tuple[int, ...]):\n    items[0] = 1\n", 1),
        ("from typing import Mapping\ndef update(cache: Mapping[str, int]):\n    cache = {}\n", 0),
    ],
)
def test_qg050_requires_readonly_contract(tmp_path: Path, source: str, expected: int) -> None:
    """A write is a quality defect only against a declared read-only contract."""
    (tmp_path / "input.py").write_text(source, encoding="utf-8")
    result = RepositoryScanner(tmp_path, GuardConfig()).scan()
    actual = [f for f in result.findings if f.code == "QG050"]
    assert len(actual) == expected
    for finding in actual:
        assert finding.evidence['readonly_annotation']
        assert finding.severity == 'warning'
