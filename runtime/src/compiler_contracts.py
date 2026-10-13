"""Replay source-bound compiler observations as conservative QG209 findings.

The checker identifies a declaration and independently binds its authored signature;
RQG owns severity and preserves the explicitly open Python runtime dispatch.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .model import Finding
from .topology_facts import CompilerDiagnosticFact, RepositoryTopology


def r1_bound_call_findings(
    snapshot: RepositoryAnalysisSnapshot,
    witnesses: Iterable[dict[str, object]],
    *,
    provider_identity: dict[str, str] | None = None,
) -> tuple[Finding, ...]:
    """Validate the frozen caller/callee evidence before issuing QG209 REVIEW.

    Args:
        snapshot: Immutable source/AST units from the same compiler invocation.
        witnesses: Unique declaration and independently checked signature mismatches.
        provider_identity: Pinned executable and typeshed identities, when available.

    Returns:
        Located warning findings, or fail-closed rejection of forged/stale records.
    """
    result: list[Finding] = []
    seen: set[tuple[str, int, int]] = set()
    for witness in witnesses:
        if (
            witness.get("verdict") != "STATIC_ARITY_MISMATCH_REVIEW_REQUIRED"
            or witness.get("runtime_dispatch") != "OPEN_WORLD"
            or witness.get("capability") != "COMPILER_UNIQUE_SOURCE_SIGNATURE"
        ):
            raise ValueError("Unsupported compiler capability or closed-world claim")
        path = witness.get("path")
        symbol = witness.get("symbol")
        if not isinstance(path, str) or not isinstance(symbol, str):
            raise ValueError("Compiler witness lacks source identity")
        if path not in snapshot.units or snapshot.units[path].tree is None:
            raise ValueError("Compiler witness source absent from snapshot")
        unit = snapshot.units[path]
        if hashlib.sha256(unit.source.encode()).hexdigest() != witness.get(
            "caller_sha256"
        ):
            raise ValueError("Compiler witness caller digest differs from snapshot")
        line, column = witness.get("line"), witness.get("column")
        if not isinstance(line, int) or not isinstance(column, int):
            raise ValueError("Compiler witness location invalid")
        # The provider reports the Call's start, never an inferred callee name.
        calls = [
            node
            for node in ast.walk(unit.tree)
            if isinstance(node, ast.Call)
            and node.lineno == line
            and node.col_offset + 1 == column
        ]
        if len(calls) != 1:
            raise ValueError("Compiler witness does not identify one authored call")
        target_path = witness.get("callee_path")
        target = (
            snapshot.units.get(target_path) if isinstance(target_path, str) else None
        )
        if target is None or target.tree is None:
            raise ValueError("Compiler witness callee path is not in snapshot")
        if hashlib.sha256(target.source.encode()).hexdigest() != witness.get(
            "callee_sha256"
        ):
            raise ValueError("Compiler witness callee digest differs from snapshot")
        from .python_import_resolution import python_module_name

        declarations = [
            node
            for node in target.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and f"{python_module_name(target_path)}.{node.name}" == symbol
        ]
        if len(declarations) != 1:
            raise ValueError(
                "Compiler witness symbol does not match authored declaration"
            )
        key = (path, line, column)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            Finding(
                code="QG209",
                severity="warning",
                confidence="medium",
                path=path,
                line=line,
                column=column,
                symbol=symbol,
                message="编译器绑定声明的实参契约冲突；运行时重绑定仍开放，需复核。",
                suggestion="核对真实入口、装饰器、运行时重绑定及签名与调用方。",
                evidence={
                    "kind": "R1_COMPILER_BOUND_CALL",
                    "capability": "COMPILER_UNIQUE_SOURCE_SIGNATURE",
                    "dispatch": "OPEN_WORLD",
                    "caller_sha256": witness["caller_sha256"],
                    "callee_sha256": witness["callee_sha256"],
                    "callee_path": target_path,
                    "signature_conflict": str(witness.get("witness", "")),
                    "provider_identity": dict(provider_identity or {}),
                },
                source="compiler-bound-r1",
            )
        )
    return tuple(sorted(result, key=lambda row: (row.path, row.line, row.column)))


def r2_initialization_observations(
    snapshot: RepositoryAnalysisSnapshot,
    diagnostics: Iterable[dict[str, object]],
    topology: RepositoryTopology,
    *,
    provider_identity: Mapping[str, str],
) -> tuple[CompilerDiagnosticFact, ...]:
    """Bind genuine Pyright initialization diagnostics to authored class/field AST.

    The original native diagnostic stays an observation. An unresolved AST
    location remains explicitly UNKNOWN; this function never claims an
    uninitialized read, complete constructor lifecycle, or MUST violation.

    Args:
        snapshot: Immutable Python source and parsed AST units.
        diagnostics: Output of an authenticated Pyright observation run.
        topology: Existing normalized symbol identities, not a second graph.
        provider_identity: Source-associated Pyright and typeshed SHA identities.

    Returns:
        Source-bound, capability-limited facts for the shared repository topology.
    """
    required = ("pyright_sha256", "typeshed_sha256")
    if any(not provider_identity.get(key) for key in required):
        raise ValueError("R2 requires actual Pyright executable and typeshed identity")
    provider = tuple(sorted(provider_identity.items()))
    symbols = {
        (item.path.as_posix(), item.line): item.symbol_id
        for item in topology.symbols
        if item.kind == "class"
    }
    facts: set[CompilerDiagnosticFact] = set()
    for report in diagnostics:
        if report.get("native_rule") != "reportUninitializedInstanceVariable":
            continue
        path = report.get("source_path")
        line, column = report.get("line"), report.get("column")
        if (
            not isinstance(path, str)
            or path not in snapshot.units
            or not isinstance(line, int)
            or not isinstance(column, int)
            or line < 1
            or column < 1
        ):
            raise ValueError("R2 diagnostic references an invalid snapshot position")
        unit = snapshot.units[path]
        if hashlib.sha256(unit.source.encode()).hexdigest() != report.get(
            "source_sha256"
        ):
            raise ValueError("R2 diagnostic source digest differs from snapshot")
        lines = unit.source.splitlines()
        if unit.tree is None or line > len(lines):
            raise ValueError("R2 diagnostic has no valid Python AST source")
        # Pyright uses zero-indexed UTF-16 code-unit columns, while Python AST
        # uses UTF-8 byte offsets. Match the exact attribute token span.
        parent = {
            child: node
            for node in ast.walk(unit.tree)
            for child in ast.iter_child_nodes(node)
        }
        matched: list[tuple[str, str]] = []
        for node in ast.walk(unit.tree):
            if (
                not isinstance(node, ast.Attribute)
                or not isinstance(node.ctx, ast.Store)
                or node.lineno != line
                or not isinstance(node.value, ast.Name)
                or node.end_col_offset is None
            ):
                continue
            # Attribute spelling only identifies the observed AST target;
            # it is never used to infer writes, purity, or business behavior.
            member_start = node.end_col_offset - len(node.attr.encode("utf-8"))
            prefix = lines[line - 1].encode("utf-8")[:member_start].decode("utf-8")
            target_column = len(prefix.encode("utf-16-le")) // 2 + 1
            if target_column != column:
                continue
            method = parent.get(node)
            while method is not None and not isinstance(
                method, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                method = parent.get(method)
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            owner = parent.get(method)
            if not isinstance(owner, ast.ClassDef) or method.decorator_list:
                continue
            parameters = (*method.args.posonlyargs, *method.args.args)
            if not parameters or node.value.id != parameters[0].arg:
                continue
            owner_id = symbols.get((path, owner.lineno))
            if owner_id is not None:
                matched.append((owner_id, node.attr))
        if len(matched) > 1:
            raise ValueError("Ambiguous type-checker attribute identity")
        owner_id, member = matched[0] if matched else ("", "")
        facts.add(
            CompilerDiagnosticFact(
                path=Path(path),
                line=line,
                column=column,
                source_sha256=report["source_sha256"],
                native_rule="reportUninitializedInstanceVariable",
                owner_symbol_id=owner_id,
                member=member,
                binding=(
                    "BOUND_CLASS_ATTRIBUTE_STORE" if matched else "UNKNOWN_SOURCE_SHAPE"
                ),
                provider_identity=provider,
            )
        )
    return tuple(
        sorted(
            facts,
            key=lambda fact: (fact.path.as_posix(), fact.line, fact.column),
        )
    )
