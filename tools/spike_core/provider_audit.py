"""Import source-located Pyright diagnostics without inventing semantic proof.

The provider owns symbol resolution/type checking; RQG owns the verdict.  No
user-chosen function/field/variable name is used to infer program behavior.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import subprocess
from dataclasses import asdict, dataclass

# These are Pyright's publicly specified diagnostic rule identifiers, not
# developer-defined source identifiers or substring/name-based heuristics.
RULE_FAMILIES: dict[str, str] = {
    "reportCallIssue": "R1_ARGUMENT_BINDING",
    "reportArgumentType": "R1_ARGUMENT_TYPE",
    "reportIncompatibleMethodOverride": "R1_OVERRIDE_CONTRACT",
    "reportUninitializedInstanceVariable": "R2_INSTANCE_LIFECYCLE",
    "reportTypedDictNotRequiredAccess": "R3_SCHEMA_ACCESS",
    "reportOptionalMemberAccess": "R3_OPTIONAL_CONSUMER",
    "reportOptionalSubscript": "R3_OPTIONAL_CONSUMER",
    "reportOptionalCall": "R3_OPTIONAL_CONSUMER",
    "reportPossiblyUnboundVariable": "CFG_POSSIBLY_UNBOUND",
    "reportUnboundVariable": "CFG_UNBOUND",
    "reportMissingImports": "PROVIDER_MISSING_IMPORT",
    "reportMissingModuleSource": "PROVIDER_MISSING_IMPORT",
    "reportReturnType": "TYPE_RETURN_CONTRACT",
    "reportAssignmentType": "TYPE_ASSIGNMENT_CONTRACT",
}

# Pyright owns each native diagnostic. Priority guides human review only; no
# unsupported compiler/runtime inference is promoted to a formal RQG finding.
DIRECT_CONTRACT_RULES = frozenset(
    {
        "reportCallIssue",
        "reportUnboundVariable",
        "reportPossiblyUnboundVariable",
        "reportTypedDictNotRequiredAccess",
        "reportUninitializedInstanceVariable",
    }
)


@dataclass(frozen=True, slots=True)
class CompilerObservation:
    """A location-bound checker observation; not a proven RQG violation.

    Store a native compiler diagnostic with source digest, line and provider identity.
    """

    source_path: str
    source_sha256: str
    line: int
    column: int
    native_rule: str
    native_severity: str
    evidence_family: str
    message: str
    rqg_verdict: str = "REVIEW_REQUIRED"
    proof_capability: str = "TYPECHECKER_DIAGNOSTIC_ONLY"


def _sha256(path: pathlib.Path) -> str:
    """Compute the complete source-file digest used to bind compiler evidence to a version."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(
    source_root: pathlib.Path,
    pyright_entry: pathlib.Path,
    *,
    timeout: int = 90,
) -> dict:
    """Evaluate a frozen disk tree and check each compiler diagnostic's origin.

    Args:
        source_root: pathlib.Path supplied to the source-bound collect analysis.
        pyright_entry: pathlib.Path supplied to the source-bound collect analysis.
        timeout: int supplied to the source-bound collect analysis.

    Returns:
        Source-backed compiler observations, without asserting successful execution of target code."""
    root = source_root.resolve(strict=True)
    entry = pyright_entry.resolve(strict=True)
    if not root.is_dir() or not entry.is_file():
        raise ValueError("provider entry and source root must exist")
    version_result = subprocess.run(
        ["node", str(entry), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    before = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*.py"))
        if path.is_file()
    }
    typeshed = entry.parent / "typeshed-fallback"
    if not (typeshed / "stdlib" / "builtins.pyi").is_file():
        raise RuntimeError(
            "INCOMPLETE_PYRIGHT_PROVIDER: offline typeshed/builtins.pyi missing"
        )
    command = [
        "node",
        str(entry),
        "--outputjson",
        "--typeshedpath",
        str(typeshed),
        str(root),
    ]
    run = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=root,
    )
    if run.returncode not in (0, 1):
        raise RuntimeError(
            f"Pyright execution failed rc={run.returncode}: {run.stderr[:300]}"
        )
    try:
        raw = json.loads(run.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Pyright returned invalid JSON") from error
    if not isinstance(raw.get("generalDiagnostics"), list) or not isinstance(
        raw.get("summary"), dict
    ):
        raise ValueError("unsupported Pyright diagnostics schema")
    observed: list[CompilerObservation] = []
    for item in raw["generalDiagnostics"]:
        absolute = pathlib.Path(item["file"]).resolve(strict=True)
        if not absolute.is_relative_to(root):
            # External dependencies have no source snapshot in this invocation.
            continue
        relative = absolute.relative_to(root).as_posix()
        if relative not in before:
            raise RuntimeError(f"provider refers to untracked Python input: {relative}")
        position = item["range"]["start"]
        if not isinstance(position["line"], int) or not isinstance(
            position["character"], int
        ):
            raise ValueError("invalid diagnostic source location")
        observed.append(
            CompilerObservation(
                source_path=relative,
                source_sha256=before[relative],
                line=position["line"] + 1,
                column=position["character"] + 1,
                native_rule=item.get("rule", "UNCLASSIFIED"),
                native_severity=item["severity"],
                evidence_family=RULE_FAMILIES.get(item.get("rule", ""), "OTHER"),
                message=item["message"],
            )
        )
    after = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*.py"))
        if path.is_file()
    }
    if before != after:
        raise RuntimeError("provider source files changed during analysis")
    observed.sort(
        key=lambda row: (row.source_path, row.line, row.column, row.native_rule)
    )
    counts = collections.Counter(row.evidence_family for row in observed)
    return {
        "schema": "rqg.compiler_observation.v1",
        "provider": "pyright",
        "version": version_result.stdout.strip(),
        "provider_entry_sha256": _sha256(entry),
        "stdlib_builtins_sha256": _sha256(typeshed / "stdlib" / "builtins.pyi"),
        "source_root": str(root),
        "source_inventory_sha256": hashlib.sha256(
            json.dumps(before, sort_keys=True).encode()
        ).hexdigest(),
        "source_files": len(before),
        "provider_summary": raw["summary"],
        "family_counts": dict(sorted(counts.items())),
        "rqg_formal_proofs": 0,
        "rqg_blockers": 0,
        "limitations": [
            "TypeChecker errors are not automatically verified runtime defects",
            "R1 lacks closed entry/callsite dominance and default execution proof",
            "R2 lacks object identity, constructor and inheritance MUST paths",
            "R3 lacks authenticated producer-to-consumer-to-effect-sink proof",
        ],
        "observations": [asdict(row) for row in observed],
    }


def triage(report: dict, source_root: pathlib.Path) -> dict:
    """Review every checker diagnostic against the unchanged source snapshot.

    Classify dependency-environment uncertainty separately from source-local
    contract issues. This does not silently discard any diagnostic, nor infer
    program behavior from developer-assigned identifier spellings.

    Args:
        report: dict supplied to the source-bound triage analysis.
        source_root: pathlib.Path supplied to the source-bound triage analysis.

    Returns:
        Evidence-based classification of compiler observations by provider capabilities."""
    if report["schema"] != "rqg.compiler_observation.v1":
        raise ValueError("unsupported compiler report schema")
    root = source_root.resolve(strict=True)
    sources: dict[str, list[str]] = {}
    missing_import_files = {
        item["source_path"]
        for item in report["observations"]
        if item["native_rule"] in {"reportMissingImports", "reportMissingModuleSource"}
    }
    rows = []
    for item in report["observations"]:
        relative = item["source_path"]
        source_path = (root / relative).resolve(strict=True)
        if not source_path.is_relative_to(root):
            raise ValueError("compiler finding escapes its attested source root")
        if _sha256(source_path) != item["source_sha256"]:
            raise ValueError(f"stale compiler diagnostic source: {relative}")
        if relative not in sources:
            sources[relative] = source_path.read_text(encoding="utf-8-sig").splitlines()
        lines = sources[relative]
        line = item["line"]
        if not isinstance(line, int) or not (1 <= line <= len(lines)):
            raise ValueError(f"invalid compiler source position: {relative}:{line}")
        native = item["native_rule"]
        dependency_incomplete = relative in missing_import_files
        if native in {"reportMissingImports", "reportMissingModuleSource"}:
            review_group = "ENVIRONMENT_INCOMPLETE"
        elif dependency_incomplete:
            review_group = "COMPILER_CONTRACT_WITH_MISSING_IMPORTS"
        elif native in DIRECT_CONTRACT_RULES:
            review_group = "SOURCE_LOCAL_PRIORITY_REVIEW"
        else:
            review_group = "TYPE_CONTRACT_REVIEW"
        rows.append(
            {
                **item,
                "evidence_family": RULE_FAMILIES.get(native, "OTHER"),
                "review_group": review_group,
                "status": "UNVERIFIED_SOURCE_CANDIDATE",
                "context": [
                    {"line": number, "source": lines[number - 1]}
                    for number in range(max(1, line - 2), min(len(lines), line + 2) + 1)
                ],
            }
        )
    groups = collections.Counter(row["review_group"] for row in rows)
    rules = collections.Counter(row["native_rule"] for row in rows)
    return {
        "schema": "rqg.compiler_review.v1",
        "source_root": str(root),
        "provider": report["provider"],
        "version": report["version"],
        "source_inventory_sha256": report["source_inventory_sha256"],
        "total": len(rows),
        "groups": dict(sorted(groups.items())),
        "rules": dict(sorted(rules.items())),
        "checked_source_files": len(sources),
        "rows": rows,
    }


def main() -> None:
    """Run the compiler observation triage CLI and write explicit evidence and review outputs.

    Returns:
        None; results are stored on the visitor, emitted to the requested file, or resources are released."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=pathlib.Path)
    parser.add_argument("--pyright-entry", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--review-output", type=pathlib.Path)
    parser.add_argument(
        "--static-r1",
        action="store_true",
        help="Cross-check direct arity mismatches with compiler declarations",
    )
    args = parser.parse_args()
    report = collect(args.source_root, args.pyright_entry)
    if args.static_r1:
        import ast
        from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
        from tools.spike_core.python_compiler_provider import (
            certified_static_call_mismatches,
        )

        root = args.source_root.resolve(strict=True)
        units = {}
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            source = path.read_text(encoding="utf-8-sig")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                tree = None
            units[relative] = PythonUnit(relative, source, tree)
        snapshot = RepositoryAnalysisSnapshot(root, "DIRECTORY", units)
        report["r1_static_witnesses"] = certified_static_call_mismatches(
            snapshot, report["observations"]
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if args.review_output is not None:
        review = triage(report, args.source_root)
        args.review_output.parent.mkdir(parents=True, exist_ok=True)
        args.review_output.write_text(
            json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in ("observations", "limitations", "source_root")
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
