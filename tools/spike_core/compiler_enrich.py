"""Pyrefly declaration-binding observation over the exact disk source snapshot.

Reports actual compiler-resolved source identities, not runtime dispatch proof.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import shutil
import subprocess

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from tools.spike_core.python_compiler_provider import project_compiler_targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=pathlib.Path)
    parser.add_argument("--max-calls", type=int, default=120)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve(strict=True)
    compiler = shutil.which("pyrefly")
    if compiler is None:
        parser.error("MISSING_SEMANTIC_PROVIDER: pyrefly executable not in PATH")
    units = {}
    calls = []
    authored = set()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if any(
            part
            in {
                ".git",
                ".agents",
                ".venv",
                "__pycache__",
                "node_modules",
                "history",
                "tests",
                "test",
                "dev-tests",
            }
            for part in path.relative_to(root).parts
        ):
            continue
        source = path.read_text(encoding="utf-8-sig")
        try:
            parsed = ast.parse(source)
        except SyntaxError:
            continue
        units[relative] = PythonUnit(relative, source, parsed)
        mod = relative[:-3].replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        for node in ast.walk(parsed):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                authored.add(f"{mod}.{node.name}")
            if isinstance(node, ast.Call) and isinstance(
                node.func, (ast.Name, ast.Attribute)
            ):
                calls.append((relative, node.func))
    snapshot = RepositoryAnalysisSnapshot(root, "DIRECTORY", units)
    # The entire frozen Snapshot is read before any compiler operation.
    # We only count a source-located, compiler-returned declaration as matched.
    selected = [(rel, func) for rel, func in calls if rel in units][: args.max_calls]
    names = {path: path for path in units}
    # Nested declaration identities are created by the same provider token-index routine.
    from tools.spike_core.python_compiler_provider import declaration_positions

    authored = frozenset(declaration_positions(snapshot).values())
    answers = project_compiler_targets(snapshot, selected, names, authored)
    version = subprocess.run(
        [compiler, "--version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()
    report = {
        "provider": "pyrefly",
        "version": version,
        "binary": compiler,
        "binary_sha256": hashlib.sha256(
            pathlib.Path(compiler).read_bytes()
        ).hexdigest(),
        "root": str(root),
        "snapshot_files": len(units),
        "requested_calls": len(selected),
        "matched_calls": len(answers),
        "status": "OPEN_WORLD_OBSERVED_MAY",
        "target_bindings": [
            {"path": path, "line": line, "column": column, "symbol_ids": target}
            for (path, line, column), target in sorted(answers.items())
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ("binary", "root", "target_bindings")
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
