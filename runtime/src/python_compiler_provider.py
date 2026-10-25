"""Project Pyrefly's verified declaration locations into existing open-world calls.

Only existing Snapshot declarations may become targets. The language server is a
source of symbol binding facts, not a proof of dynamic dispatch or caller closure.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import queue
import shutil
import subprocess
import threading
import time
import tokenize
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .python_import_resolution import python_module_name



def declaration_positions(
    snapshot: RepositoryAnalysisSnapshot,
) -> dict[tuple[str, int, int], str]:
    """Index Snapshot-authored declaration identities by precise LSP positions.

    Args:
        snapshot: Frozen parsed source and module units used to reconstruct lexical obligations.

    Returns:
        Source-position index of discovered Python function declarations."""
    positions: dict[tuple[str, int, int], str] = {}
    for path in snapshot.paths:
        unit = snapshot.units[path]
        if unit.tree is None:
            continue
        lines = unit.source.splitlines()
        module = python_module_name(path)
        # Token locations are authoritative; substring search can match text
        # within a Python keyword instead of the declaration identifier.
        tokens = list(tokenize.generate_tokens(io.StringIO(unit.source).readline))
        declaration_names: dict[tuple[int, str, str], tuple[int, int]] = {}
        for index, keyword in enumerate(tokens[:-1]):
            if keyword.type != tokenize.NAME or keyword.string not in {"def", "class"}:
                continue
            declared = tokens[index + 1]
            if declared.type != tokenize.NAME:
                continue
            start_line = keyword.start[0]
            if (
                keyword.string == "def"
                and index
                and tokens[index - 1].string == "async"
            ):
                start_line = tokens[index - 1].start[0]
            column = (
                len(
                    lines[declared.start[0] - 1][: declared.start[1]].encode(
                        "utf-16-le"
                    )
                )
                // 2
            )
            declaration_names[(start_line, keyword.string, declared.string)] = (
                declared.start[0] - 1,
                column,
            )

        class Declarations(ast.NodeVisitor):
            """Build lexical declaration identities from tokenizer-authenticated source locations.

            This visitor only records authored source observations and never invokes analyzed code."""

            def __init__(self) -> None:
                """Initialize the nesting path that qualifies compiler declaration identities.

                Returns:
                    None; results are stored on the visitor, emitted to the requested file, or resources are released."""
                self.owners: list[str] = []

            def _visit(
                self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ) -> None:
                """Index the currently authored declaration and recurse into its nested definitions."""
                kind = "class" if isinstance(node, ast.ClassDef) else "def"
                point = declaration_names.get((node.lineno, kind, node.name))
                if point is not None:
                    symbol = ".".join(filter(None, (module, *self.owners, node.name)))
                    positions[(path, *point)] = symbol
                self.owners.append(node.name)
                self.generic_visit(node)
                self.owners.pop()

            visit_FunctionDef = _visit
            visit_AsyncFunctionDef = _visit
            visit_ClassDef = _visit

        Declarations().visit(unit.tree)
    return positions


class _PyreflyLsp:
    """One bounded LSP session for a repository snapshot, no separate call graph."""

    def __init__(self, binary: str, root: Path) -> None:
        """Create one bounded Pyrefly LSP process, initialize it, and retain its request queue.

        Args:
            binary: str passed to the source-bound __init__ operation.
            root: Path passed to the source-bound __init__ operation.

        Returns:
            None; results are stored on the visitor, emitted to the requested file, or resources are released."""
        self.process = subprocess.Popen(
            [binary, "lsp", "--indexing-mode", "none", "-j", "1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=root,
        )
        self.messages: queue.Queue[dict | None] = queue.Queue()
        threading.Thread(target=self._read_messages, daemon=True).start()
        self.sequence = 0
        try:
            self._request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": root.as_uri(),
                    "workspaceFolders": [{"uri": root.as_uri(), "name": root.name}],
                    "capabilities": {},
                },
                timeout=15,
            )
            self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        except (OSError, RuntimeError, TimeoutError, ValueError):
            self.close()
            raise
        self.opened: set[str] = set()

    def _read_messages(self) -> None:
        """Read length-prefixed JSON-RPC replies and signal EOF explicitly to request waiters."""
        stream = self.process.stdout
        if stream is None:
            raise RuntimeError("Python compiler LSP stdout is unavailable")
        try:
            while True:
                header = stream.readline()
                if not header:
                    break
                if not header.lower().startswith(b"content-length:"):
                    continue
                size = int(header.partition(b":")[2].strip())
                while stream.readline().strip():
                    pass
                payload = stream.read(size)
                if len(payload) != size:
                    break
                self.messages.put(json.loads(payload))
        finally:
            self.messages.put(None)

    def _send(self, message: dict) -> None:
        """Write a single framed JSON-RPC message to the compiler subprocess."""
        payload = json.dumps(message, separators=(",", ":")).encode()
        if self.process.stdin is None:
            raise RuntimeError("Python compiler LSP stdin is unavailable")
        self.process.stdin.write(
            f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload
        )
        self.process.stdin.flush()

    def _request(self, method: str, params: dict, timeout: float = 5) -> object:
        """Wait for a matching JSON-RPC reply or raise a bounded transport error."""
        self.sequence += 1
        requested = self.sequence
        self._send(
            {"jsonrpc": "2.0", "id": requested, "method": method, "params": params}
        )
        deadline = time.monotonic() + timeout
        remaining = deadline - time.monotonic()
        while remaining > 0:
            try:
                response = self.messages.get(timeout=remaining)
            except queue.Empty as error:
                raise TimeoutError("Python compiler request timed out") from error
            if response is None:
                raise RuntimeError("Python compiler LSP terminated unexpectedly")
            if response["id"] == requested:
                if "error" in response:
                    raise RuntimeError("Python compiler rejected definition request")
                return response["result"]
            remaining = deadline - time.monotonic()
        raise TimeoutError("Python compiler request exceeded its deadline")

    def definition(self, path: Path, source: str, line: int, col: int) -> object:
        """Request a source declaration location, opening the source document once per session.

        Args:
            path: Path passed to the source-bound definition operation.
            source: str passed to the source-bound definition operation.
            line: int passed to the source-bound definition operation.
            col: int passed to the source-bound definition operation.

        Returns:
            Compiler-resolved definition response for the requested original source position."""
        uri = path.as_uri()
        if uri not in self.opened:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "python",
                            "version": 1,
                            "text": source,
                        }
                    },
                }
            )
            self.opened.add(uri)
        return self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": col},
            },
        )

    def close(self) -> None:
        """Close the compiler session and terminate its subprocess without retaining a worker.

        Returns:
            None; results are stored on the visitor, emitted to the requested file, or resources are released."""
        self.process.kill()
        self.process.wait(timeout=3)


def project_compiler_targets(
    snapshot: RepositoryAnalysisSnapshot,
    unresolved: Iterable[tuple[str, ast.expr]],
    source_paths: dict[str, str],
    authored_symbols: frozenset[str],
) -> dict[tuple[str, int, int], tuple[str, ...]]:
    """Resolve authored unknown callsites without changing entry/dispatch closure.

    The external compiler may only read a matching physical WORKTREE/DIRECTORY
    snapshot. Revision/synthetic sources are never mixed with filesystem state.

    Args:
        snapshot: Frozen parsed source and module units used to reconstruct lexical obligations.
        unresolved: Iterable[tuple[str, ast.expr]] supplied to the source-bound project_compiler_targets analysis.
        source_paths: dict[str, str] supplied to the source-bound project_compiler_targets analysis.
        authored_symbols: frozenset[str] supplied to the source-bound project_compiler_targets analysis.

    Returns:
        Bound calls and resolvable declarations eligible for compiler-assisted checking."""
    candidates = tuple(unresolved)
    if not candidates:
        return {}
    binary = shutil.which("pyrefly")
    if binary is None or snapshot.label not in {"WORKTREE", "DIRECTORY"}:
        return {}
    root = snapshot.root.resolve()
    # An LSP resolves imports from disk. Refuse hybrid revision/live-source facts.
    for path in snapshot.paths:
        unit = snapshot.units[path]
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return {}
        if (
            hashlib.sha256(candidate.read_bytes()).digest()
            != hashlib.sha256(unit.source.encode()).digest()
        ):
            return {}
    positions = declaration_positions(snapshot)
    results: dict[tuple[str, int, int], tuple[str, ...]] = {}
    session: _PyreflyLsp | None = None
    try:
        session = _PyreflyLsp(binary, root)
        lines_by_path = {
            path: snapshot.units[path].source.splitlines() for path in snapshot.paths
        }
        for caller, expr in candidates:
            if not isinstance(expr, (ast.Name, ast.Attribute)):
                continue
            source_path = source_paths[caller]
            unit = snapshot.units[source_path]
            start = expr.col_offset
            if isinstance(expr, ast.Attribute):
                start = expr.end_col_offset - len(expr.attr.encode("utf-8"))
            line = expr.lineno - 1
            prefix = lines_by_path[source_path][line].encode("utf-8")[:start].decode("utf-8")
            char = len(prefix.encode("utf-16-le")) // 2
            declarations = session.definition(
                root / source_path, unit.source, line, char
            )
            if not declarations:
                continue
            locations = (
                declarations if isinstance(declarations, list) else [declarations]
            )
            targets: set[str] = set()
            for location in locations:
                uri = location.get("uri", location.get("targetUri", ""))
                split = urlsplit(uri)
                if split.scheme != "file":
                    continue
                destination = Path(unquote(split.path)).resolve()
                if not destination.is_relative_to(root):
                    continue
                relative = destination.relative_to(root).as_posix()
                point = location.get(
                    "range", location.get("targetSelectionRange", {})
                ).get("start", {})
                target = positions.get(
                    (relative, point.get("line"), point.get("character"))
                )
                if target in authored_symbols:
                    targets.add(target)
            if targets:
                results[(source_path, expr.lineno, expr.col_offset)] = tuple(
                    sorted(targets)
                )
    except (OSError, RuntimeError, TimeoutError, ValueError):
        # No compiler proof is preferable to a stale or partial proof.
        return {}
    finally:
        if session is not None:
            session.close()
    return results


def certified_static_call_mismatches(
    snapshot: RepositoryAnalysisSnapshot,
    diagnostics: Iterable[dict],
) -> list[dict[str, object]]:
    """Independently check compiler-bound, source-authored function arity.

    Accept direct module-level declarations, explicit imported functions, and
    module-qualified imports when the compiler resolves a unique declaration.
    Everything remains OPEN_WORLD: neither type checking nor source signature
    inspection certifies runtime rebinding or all possible entry points.

    Args:
        snapshot: Frozen parsed source and module units used to reconstruct lexical obligations.
        diagnostics: Iterable[dict] supplied to the source-bound certified_static_call_mismatches analysis.

    Returns:
        Provable call-signature mismatch findings with the originating source and compiler witness."""
    import inspect

    if snapshot.label not in {"WORKTREE", "DIRECTORY"}:
        return []
    observations = [
        row
        for row in diagnostics
        if row.get("native_rule") == "reportCallIssue"
        and row.get("native_severity") == "error"
        and row.get("source_path") in snapshot.units
    ]
    if not observations:
        return []

    declarations: dict[str, tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    duplicates: set[str] = set()
    for path in snapshot.paths:
        tree = snapshot.units[path].tree
        if tree is None:
            continue
        for declaration in tree.body:
            if not isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            identity = f"{python_module_name(path)}.{declaration.name}"
            if identity in declarations:
                duplicates.add(identity)
            declarations[identity] = (path, declaration)

    candidates: dict[tuple[str, int, int], tuple[str, ast.Call]] = {}
    for row in observations:
        path = row["source_path"]
        unit = snapshot.units[path]
        if hashlib.sha256(unit.source.encode()).hexdigest() != row.get("source_sha256"):
            raise ValueError("stale source identity in compiler diagnostic")
        if unit.tree is None:
            continue
        line, column = row.get("line"), row.get("column")
        if not isinstance(line, int) or not isinstance(column, int):
            continue
        # Compiler diagnostics sometimes point to an argument rather than the
        # callable. Only accept a unique source-level call at that location.
        possible = [
            node
            for node in ast.walk(unit.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
            and node.lineno == line
            and node.col_offset
            <= column - 1
            <= (node.end_col_offset or node.col_offset)
        ]
        if len(possible) != 1:
            continue
        call = possible[0]
        if any(isinstance(value, ast.Starred) for value in call.args) or any(
            keyword.arg is None for keyword in call.keywords
        ):
            continue
        candidates[(path, call.func.lineno, call.func.col_offset)] = (path, call)
    if not candidates:
        return []

    targets = project_compiler_targets(
        snapshot,
        [(path, call.func) for path, call in candidates.values()],
        {path: path for path in snapshot.paths},
        frozenset(declaration_positions(snapshot).values()),
    )
    results: list[dict[str, object]] = []
    for key, (path, call) in sorted(candidates.items()):
        found = targets.get(key, ())
        if len(found) != 1 or found[0] in duplicates:
            continue
        definition = declarations.get(found[0])
        if definition is None:
            continue
        target_path, declaration = definition
        if declaration.decorator_list:
            continue
        tree = snapshot.units[path].tree
        assert tree is not None
        function = call.func
        name: str
        if isinstance(function, ast.Name):
            name = function.id
            direct = target_path == path and declaration.name == name
            imported = any(
                isinstance(node, ast.ImportFrom)
                and any(
                    (alias.asname or alias.name) == name
                    and alias.name == declaration.name
                    for alias in node.names
                )
                for node in tree.body
            )
            if not (direct or imported):
                continue
        elif isinstance(function, ast.Attribute) and isinstance(
            function.value, ast.Name
        ):
            if function.attr != declaration.name:
                # A module-level __getattr__() can be reported as the
                # definition of a dynamically exported attribute. That
                # declaration is *not* the callable's runtime signature.
                continue
            name = function.value.id
            # The receiver must be a bound module import, not an object method
            # whose dispatch/binding signature may differ at runtime.
            if not any(
                isinstance(node, ast.Import)
                and any(
                    (alias.asname or alias.name.split(".")[0]) == name
                    for alias in node.names
                )
                for node in tree.body
            ):
                continue
        else:
            continue
        # Avoid every observed local/global reassignment, deletion, parameter
        # shadow, and alternate import of this binding. An unresolved boundary
        # makes a witness unavailable; it is never evidence of correctness.
        if any(
            (
                isinstance(node, ast.Name)
                and node.id == name
                and isinstance(node.ctx, (ast.Store, ast.Del))
            )
            or (isinstance(node, ast.arg) and node.arg == name)
            for node in ast.walk(tree)
        ):
            continue
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and any(
                (
                    alias.asname
                    or (
                        alias.name.split(".")[0]
                        if isinstance(node, ast.Import)
                        else alias.name
                    )
                )
                == name
                for alias in node.names
            )
        ]
        if len(imports) > (
            0 if target_path == path and isinstance(function, ast.Name) else 1
        ):
            continue
        args = declaration.args
        positional = [*args.posonlyargs, *args.args]
        default_at = len(positional) - len(args.defaults)
        parameters = [
            inspect.Parameter(
                arg.arg,
                inspect.Parameter.POSITIONAL_ONLY
                if index < len(args.posonlyargs)
                else inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=object() if index >= default_at else inspect.Parameter.empty,
            )
            for index, arg in enumerate(positional)
        ]
        if args.vararg:
            parameters.append(
                inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL)
            )
        parameters.extend(
            inspect.Parameter(
                arg.arg,
                inspect.Parameter.KEYWORD_ONLY,
                default=object() if default is not None else inspect.Parameter.empty,
            )
            for arg, default in zip(args.kwonlyargs, args.kw_defaults)
        )
        if args.kwarg:
            parameters.append(
                inspect.Parameter(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD)
            )
        try:
            inspect.Signature(parameters).bind(
                *[object() for _ in call.args],
                **{keyword.arg: object() for keyword in call.keywords},
            )
        except TypeError as error:
            results.append(
                {
                    "path": path,
                    "line": call.lineno,
                    "column": call.col_offset + 1,
                    "symbol": found[0],
                    "caller_sha256": hashlib.sha256(
                        snapshot.units[path].source.encode()
                    ).hexdigest(),
                    "callee_path": target_path,
                    "callee_sha256": hashlib.sha256(
                        snapshot.units[target_path].source.encode()
                    ).hexdigest(),
                    "witness": str(error),
                    "capability": "COMPILER_UNIQUE_SOURCE_SIGNATURE",
                    "runtime_dispatch": "OPEN_WORLD",
                    "rule_family": "R1_ARGUMENT_BINDING",
                    "verdict": "STATIC_ARITY_MISMATCH_REVIEW_REQUIRED",
                }
            )
    return results
