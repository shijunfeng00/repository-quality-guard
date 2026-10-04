"""Authoritative C++ translation-unit build contracts from compilation databases."""

from __future__ import annotations

import json
import shutil
import subprocess
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot


class CompilerFamily(StrEnum):
    """Compiler family declared by an authored translation-unit command.

    Values preserve the native build-tool identity used by one translation unit; they
    never select or imply a semantic-analysis frontend.
    """

    GCC = "gcc"
    CLANG = "clang"
    MSVC = "msvc"
    UNKNOWN = "unknown"

    @classmethod
    def from_executable(cls, executable: str) -> "CompilerFamily":
        """Classify one authored compiler executable without changing build semantics.

        Args:
            executable: Compiler executable token from the authored build command.

        Returns:
            The recognized native compiler family, or ``UNKNOWN`` when the executable
            does not identify a supported family.
        """
        name = Path(executable).name.lower()
        if name in {"cl", "cl.exe"}:
            return cls.MSVC
        if "clang" in name:
            return cls.CLANG
        if name in {"c++", "g++", "gcc"} or name.startswith(("g++-", "gcc-")):
            return cls.GCC
        return cls.UNKNOWN


class NativeSyntaxState(StrEnum):
    """Outcome of a no-output check executed by the authored native compiler.

    ``UNAVAILABLE`` means the authored compiler cannot be executed in the current
    environment; it is deliberately distinct from a native syntax failure.
    """

    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


@dataclass(slots=True, frozen=True)
class NativeSyntaxResult:
    """Native compiler validation evidence kept separate from semantic-provider facts.

    The result records the exact no-output command and native compiler outcome without
    making any claim about Clang or another optional semantic provider.
    """

    state: NativeSyntaxState
    command: tuple[str, ...]
    returncode: int | None = None
    stderr: str = ""


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

        Args:
            source: Optional replacement source path used for an immutable materialized TU.

        Returns:
            Native compiler arguments with output/dependency generation disabled. Unknown
            compiler families return an empty tuple rather than being translated.
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


def repository_path(root: Path, directory: Path, value: str) -> str | None:
    """Map an authored compile-command path into repository-relative identity.

    Args:
        root: Repository root owning the compilation database.
        directory: Authored working directory for the translation-unit command.
        value: Source or path-bearing argument from the authored command.

    Returns:
        Repository-relative POSIX path when the argument belongs to the repository;
        otherwise ``None``.
    """
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = directory / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _database_candidates(root: Path) -> tuple[Path, ...]:
    """Return stable repository-owned compilation databases.

    A root database represents the whole repository and takes precedence. Otherwise
    independent subprojects may each own one database. Vendored/tool-managed trees are
    excluded; conflicting commands for the same TU are handled by the loader rather
    than resolved by path-order guessing.
    """
    primary = root / "compile_commands.json"
    if primary.is_file():
        return (primary,)
    excluded_parts = {
        ".git",
        ".agents",
        ".venv",
        "venv",
        "node_modules",
        "3rd_party",
        "third_party",
        "vendor",
    }
    candidates = []
    for path in root.rglob("compile_commands.json"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in excluded_parts for part in relative.parts[:-1]):
            continue
        candidates.append(path)
    return tuple(sorted(candidates))


def _translation_unit_spec(
    snapshot: RepositoryAnalysisSnapshot, database: Path, entry: dict[str, object]
) -> TranslationUnitBuildSpec | None:
    """Adapt one external compilation-database entry to the fixed internal contract."""
    if "directory" not in entry or "file" not in entry:
        return None
    raw_directory = entry["directory"]
    raw_file = entry["file"]
    if not isinstance(raw_directory, str) or not isinstance(raw_file, str):
        return None

    if "arguments" in entry:
        raw_arguments = entry["arguments"]
        if not isinstance(raw_arguments, list) or not all(
            isinstance(item, str) for item in raw_arguments
        ):
            return None
        arguments = tuple(raw_arguments)
    else:
        if "command" not in entry:
            return None
        command = entry["command"]
        if not isinstance(command, str):
            return None
        arguments = tuple(shlex.split(command, posix=True))

    compiler_index = _compiler_index(arguments)
    if compiler_index is None:
        return None
    compiler = arguments[compiler_index]
    family = CompilerFamily.from_executable(compiler)
    directory = Path(raw_directory).resolve()
    path = repository_path(snapshot.root, directory, raw_file)
    if path is None or path not in snapshot.language_units:
        return None
    return TranslationUnitBuildSpec(
        path=path,
        directory=directory,
        compiler=compiler,
        arguments=arguments,
        family=family,
        database=database,
        repository_root=snapshot.root.resolve(),
    )


def load_compilation_database(
    snapshot: RepositoryAnalysisSnapshot,
) -> dict[str, TranslationUnitBuildSpec]:
    """Load authoritative TU build contracts for a filesystem-backed snapshot.

    Args:
        snapshot: Repository snapshot whose authored build contracts should be loaded.

    Returns:
        Stable TU-path mapping of unambiguous build contracts. Immutable Git revisions
        deliberately return an empty mapping rather than borrowing worktree metadata.
    """
    if snapshot.label not in {"WORKTREE", "DIRECTORY"}:
        return {}
    specs: dict[str, TranslationUnitBuildSpec] = {}
    ambiguous: set[str] = set()
    for database in _database_candidates(snapshot.root):
        try:
            payload = json.loads(database.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if type(payload) is not list:
            continue
        for entry in payload:
            if type(entry) is not dict:
                continue
            candidate = _translation_unit_spec(snapshot, database, entry)
            if candidate is None or candidate.path in ambiguous:
                continue
            if candidate.path not in specs:
                specs[candidate.path] = candidate
                continue
            existing = specs[candidate.path]
            if (
                existing.arguments != candidate.arguments
                or existing.directory != candidate.directory
            ):
                del specs[candidate.path]
                ambiguous.add(candidate.path)
    return dict(sorted(specs.items()))


def _source_argument(spec: TranslationUnitBuildSpec, argument: str) -> bool:
    """Return whether an argument names the authored translation-unit source."""
    path = repository_path(spec.repository_root, spec.directory, argument)
    return path == spec.path


def _gnu_syntax_command(
    spec: TranslationUnitBuildSpec, source: Path | None
) -> tuple[str, ...]:
    """Build a no-output GCC/Clang command while preserving authored semantic flags."""
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
        if any(
            argument.startswith(prefix) for prefix in ("-o", "-MF", "-MT", "-MQ", "-MJ")
        ):
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
    """Build a no-output MSVC command while preserving authored semantic flags."""
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


def check_native_syntax(
    spec: TranslationUnitBuildSpec,
    timeout: float = 30.0,
) -> NativeSyntaxResult:
    """Execute the authored compiler in no-output syntax mode.

    Args:
        spec: Authoritative translation-unit build contract from the compilation database.
        timeout: Maximum native compiler execution time in seconds.

    Returns:
        Native syntax evidence. Missing compilers and timeouts remain ``UNAVAILABLE``;
        semantic providers do not reinterpret this result.
    """
    command = spec.native_syntax_command()
    if not command:
        return NativeSyntaxResult(NativeSyntaxState.UNAVAILABLE, ())
    executable = command[0]
    resolved = (
        shutil.which(executable) if not Path(executable).is_absolute() else executable
    )
    if resolved is None or not Path(resolved).exists():
        return NativeSyntaxResult(NativeSyntaxState.UNAVAILABLE, command)
    try:
        completed = subprocess.run(
            command,
            cwd=spec.directory,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return NativeSyntaxResult(
            NativeSyntaxState.UNAVAILABLE, command, stderr=str(error)
        )
    return NativeSyntaxResult(
        NativeSyntaxState.PASS if completed.returncode == 0 else NativeSyntaxState.FAIL,
        command,
        completed.returncode,
        completed.stderr.strip(),
    )
