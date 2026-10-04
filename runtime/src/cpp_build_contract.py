"""Authoritative C++ translation-unit build contracts from compilation databases."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot


class CompilerFamily(StrEnum):
    """Compiler family declared by an authored translation-unit command."""

    GCC = "gcc"
    CLANG = "clang"
    MSVC = "msvc"
    UNKNOWN = "unknown"


_WRAPPERS = {"ccache", "sccache"}
_GNU_DEPENDENCY_FLAGS = {"-M", "-MM", "-MD", "-MMD", "-MP"}
_GNU_VALUE_FLAGS = {"-o", "-MF", "-MT", "-MQ", "-MJ"}


@dataclass(slots=True, frozen=True)
class TranslationUnitBuildSpec:
    """One authored translation-unit compile command.

    The command is build truth, not a semantic-provider command. Consumers may derive
    no-output validation commands, but must not change the compiler family or invent
    semantic flags.
    """

    path: str
    directory: Path
    compiler: str
    arguments: tuple[str, ...]
    family: CompilerFamily
    database: Path
    repository_root: Path

    @property
    def compiler_name(self) -> str:
        """Return the invoked compiler basename without path components."""
        return Path(self.compiler).name.lower()

    def native_syntax_command(self, source: Path | None = None) -> tuple[str, ...]:
        """Return a no-output syntax validation command for the native compiler.

        This preserves the compiler and semantic flags from the compilation database.
        It removes object/dependency-output switches and replaces the source path only
        when an explicit materialized source is supplied.
        """
        if self.family in {CompilerFamily.GCC, CompilerFamily.CLANG}:
            return _gnu_syntax_command(self, source)
        if self.family is CompilerFamily.MSVC:
            return _msvc_syntax_command(self, source)
        return ()


def _compiler_index(arguments: tuple[str, ...]) -> int | None:
    """Locate the real compiler token behind common cache wrappers."""
    if not arguments:
        return None
    index = 0
    while index < len(arguments) and Path(arguments[index]).name.lower() in _WRAPPERS:
        index += 1
    return index if index < len(arguments) else None


def _compiler_family(compiler: str) -> CompilerFamily:
    """Classify a compiler executable without changing its identity."""
    name = Path(compiler).name.lower()
    if name in {"cl", "cl.exe"}:
        return CompilerFamily.MSVC
    if "clang" in name:
        return CompilerFamily.CLANG
    if name in {"c++", "g++", "gcc"} or name.startswith(("g++-", "gcc-")):
        return CompilerFamily.GCC
    return CompilerFamily.UNKNOWN


def _entry_arguments(entry: dict[str, object]) -> tuple[str, ...]:
    """Decode one compilation-database entry without executing shell syntax."""
    raw = entry.get("arguments")
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return tuple(raw)
    command = entry.get("command")
    if isinstance(command, str):
        return tuple(shlex.split(command, posix=True))
    return ()


def _repository_path(root: Path, directory: Path, value: str) -> str | None:
    """Map an authored source path back into the repository when it belongs there."""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = directory / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _database_candidates(root: Path) -> tuple[Path, ...]:
    """Return stable conventional compilation-database candidates.

    We intentionally do not recursively guess through vendored build trees. An
    existing root/build database is authoritative; otherwise a unique direct child
    database is accepted.
    """
    fixed = [root / "compile_commands.json", root / "build" / "compile_commands.json"]
    present = [path for path in fixed if path.is_file()]
    if present:
        return tuple(present)
    children = sorted(
        path for path in root.glob("*/compile_commands.json") if path.is_file()
    )
    return tuple(children) if len(children) == 1 else ()


def load_compilation_database(
    snapshot: RepositoryAnalysisSnapshot,
) -> dict[str, TranslationUnitBuildSpec]:
    """Load authoritative TU build contracts for a filesystem-backed snapshot.

    Immutable Git-revision snapshots deliberately do not borrow a worktree-generated
    compilation database. If the snapshot has no matching authoritative database, the
    result is empty and callers must treat build truth as unavailable.
    """
    if snapshot.label not in {"WORKTREE", "DIRECTORY"}:
        return {}
    candidates = _database_candidates(snapshot.root)
    if len(candidates) != 1:
        return {}
    database = candidates[0]
    try:
        payload = json.loads(database.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, list):
        return {}

    specs: dict[str, TranslationUnitBuildSpec] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        raw_directory = entry.get("directory")
        raw_file = entry.get("file")
        if not isinstance(raw_directory, str) or not isinstance(raw_file, str):
            continue
        directory = Path(raw_directory).resolve()
        path = _repository_path(snapshot.root, directory, raw_file)
        if path is None or path not in snapshot.language_units:
            continue
        arguments = _entry_arguments(entry)
        compiler_index = _compiler_index(arguments)
        if compiler_index is None:
            continue
        compiler = arguments[compiler_index]
        specs[path] = TranslationUnitBuildSpec(
            path=path,
            directory=directory,
            compiler=compiler,
            arguments=arguments,
            family=_compiler_family(compiler),
            database=database,
            repository_root=snapshot.root.resolve(),
        )
    return dict(sorted(specs.items()))


def _source_argument(spec: TranslationUnitBuildSpec, argument: str) -> bool:
    """Return whether an argument names the authored translation-unit source."""
    path = _repository_path(spec.repository_root, spec.directory, argument)
    return path == spec.path


def _gnu_syntax_command(
    spec: TranslationUnitBuildSpec, source: Path | None
) -> tuple[str, ...]:
    compiler_index = _compiler_index(spec.arguments)
    if compiler_index is None:
        return ()
    output: list[str] = list(spec.arguments[: compiler_index + 1])
    index = compiler_index + 1
    replaced_source = False
    while index < len(spec.arguments):
        argument = spec.arguments[index]
        if argument == "-c" or argument in _GNU_DEPENDENCY_FLAGS:
            index += 1
            continue
        if argument in _GNU_VALUE_FLAGS:
            index += 2
            continue
        if any(argument.startswith(prefix) for prefix in ("-o", "-MF", "-MT", "-MQ", "-MJ")):
            index += 1
            continue
        if _source_argument(spec, argument):
            output.append(str(source) if source is not None else argument)
            replaced_source = True
        else:
            output.append(argument)
        index += 1
    if source is not None and not replaced_source:
        output.append(str(source))
    output.append("-fsyntax-only")
    return tuple(output)


def _msvc_syntax_command(
    spec: TranslationUnitBuildSpec, source: Path | None
) -> tuple[str, ...]:
    compiler_index = _compiler_index(spec.arguments)
    if compiler_index is None:
        return ()
    output: list[str] = list(spec.arguments[: compiler_index + 1])
    replaced_source = False
    for argument in spec.arguments[compiler_index + 1 :]:
        lower = argument.lower()
        if lower == "/c" or lower.startswith(("/fo", "/fd", "/fe")):
            continue
        if _source_argument(spec, argument):
            output.append(str(source) if source is not None else argument)
            replaced_source = True
        else:
            output.append(argument)
    if source is not None and not replaced_source:
        output.append(str(source))
    output.append("/Zs")
    return tuple(output)
