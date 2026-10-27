"""File-based test record loaders with optional, explicit cache preprocessing.

A loader yields complete serialized records, never automatically expands an
iterable stored *inside* a record. Only trusted pickle inputs are permitted.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
import csv
import json
import os
import pickle
import tempfile


class DataLoader(ABC, Iterable):
    """Iterable records with an optional one-time, file-to-file preprocessing hook.

    Override ``_iter`` to consume a resolved filename. Override
    ``preprocessing(source, output)`` only for source formats that require
    conversion. The base implementation is intentionally unsupported.
    """

    _suffix = ""

    def __init__(self, path: str | Path):
        self.source = Path(path)

    def preprocessing(self, source: Path, output: Path) -> None:
        """Optional conversion hook. Write a complete file to ``output``."""
        raise NotImplementedError("This loader does not require preprocessing")

    def filename(self) -> Path:
        """Return source or materialize a reusable file in sibling .cache/."""
        source = self.source
        if not source.is_file():
            raise FileNotFoundError(f"Expected one data source file: {source}")
        if type(self).preprocessing is DataLoader.preprocessing:
            return source
        if not self._suffix:
            raise ValueError("A preprocessing DataLoader must declare _suffix")
        # Class identity differentiates distinct transformations of one source.
        target = (source.parent / ".cache" /
                  f"{source.name}.{type(self).__module__.replace('.', '_')}."
                  f"{type(self).__qualname__}{self._suffix}")
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement prevents a reader from opening a partial cache.
        fd, name = tempfile.mkstemp(prefix=".building-", suffix=self._suffix,
                                    dir=target.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            self.preprocessing(source, temporary)
            if not temporary.is_file():
                raise FileNotFoundError(f"Preprocessing did not write {temporary}")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def __iter__(self) -> Iterator[Any]:
        return self._iter(self.filename())

    @abstractmethod
    def _iter(self, path: Path) -> Iterator[Any]:
        """Deserialize and yield complete records from one prepared file."""


class JSONDataLoader(DataLoader):
    """One JSON document is one record, even when it contains a list."""
    _suffix = ".json"

    def _iter(self, path: Path) -> Iterator[Any]:
        with path.open("r", encoding="utf-8") as stream:
            yield json.load(stream)


class JSONLDataLoader(DataLoader):
    """Yield one complete JSON value per non-empty line."""
    _suffix = ".jsonl"

    def _iter(self, path: Path) -> Iterator[Any]:
        with path.open("r", encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL: {path}:{line_no}: {exc}") from exc


class CSVDataLoader(DataLoader):
    """Yield CSV rows as mappings; no automatic type coercion."""
    _suffix = ".csv"

    def _iter(self, path: Path) -> Iterator[Any]:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError(f"Missing or repeated CSV header: {path}")
            for row in reader:
                if None in row:
                    raise ValueError(f"CSV contains extra columns: {path}")
                yield dict(row)


class PickleDataLoader(DataLoader):
    """Read consecutive trusted pickle records from exactly one file.

    Every pickle.load result is one record; containers inside it stay intact.
    A non-EOF pickle error is propagated instead of being treated as completion.
    """
    _suffix = ".pkl"

    def _iter(self, path: Path) -> Iterator[Any]:
        with path.open("rb") as stream:
            while True:
                # EOF must be distinguished from a truncated/incomplete record.
                if not stream.read(1):
                    break
                stream.seek(-1, os.SEEK_CUR)
                yield pickle.load(stream)
