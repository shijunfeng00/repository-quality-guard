"""Detect closed literal producers that violate a source-declared required schema.

Unlike generic mapping defaults, a fully literal return/assignment is a closed
producer observation.  The field contract comes from the same normalized
TypedDict identity rather than from lexical names or inferred runtime shapes.
"""

from __future__ import annotations

import ast
import hashlib

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .model import Finding
from .topology_facts import RepositoryTopology


def typed_producer_contract_findings(
    snapshot: RepositoryAnalysisSnapshot, topology: RepositoryTopology
) -> list[Finding]:
    """Compare verified closed mapping literals against required TypedDict fields.

    Args:
        snapshot: The authoritative in-memory repository source and AST snapshot.
        topology: Schema and producer facts generated from that same snapshot.

    Returns:
        Evidence-bound conditional contract violations, never guesses for spread
        dictionaries, dynamic keys, optional fields, or unknown producer types.
    """
    required: dict[str, dict[str, object]] = {}
    for field in topology.typed_schema_fields:
        if field.required:
            required.setdefault(field.schema_symbol_id, {})[field.field] = field
    # Literal return facts inherit a schema identity from their declaration,
    # but the same identity may also describe list[Item] or dict[str, Item].
    # Only a direct TypedDict return type makes the returned mapping itself
    # responsible for all required fields.
    direct_return_producers = {
        (row.schema_symbol_id, row.producer_symbol_id)
        for row in topology.typed_producers
        if row.kind == "DECLARED_RETURN"
    }
    results: list[Finding] = []
    for producer in topology.typed_producers:
        if producer.kind not in {"OBSERVED_RETURN_LITERAL", "ANNOTATED_ASSIGNMENT"}:
            continue
        if (
            producer.kind == "OBSERVED_RETURN_LITERAL"
            and (producer.schema_symbol_id, producer.producer_symbol_id)
            not in direct_return_producers
        ):
            continue
        fields = required.get(producer.schema_symbol_id)
        if not fields:
            continue
        path = producer.path.as_posix()
        unit = snapshot.units.get(path)
        if unit is None or unit.tree is None:
            continue
        if hashlib.sha256(unit.source.encode()).hexdigest() != producer.source_sha256:
            continue
        # The schema topology currently emits observed keys even for open dicts.
        # Recheck the *actual* RHS before using absence as a proof of omission.
        candidates: list[ast.Dict] = []
        for node in ast.walk(unit.tree):
            if getattr(node, "lineno", None) != producer.line:
                continue
            if producer.kind == "OBSERVED_RETURN_LITERAL" and isinstance(
                node, ast.Return
            ):
                if isinstance(node.value, ast.Dict):
                    candidates.append(node.value)
            elif producer.kind == "ANNOTATED_ASSIGNMENT" and isinstance(
                node, ast.AnnAssign
            ):
                if isinstance(node.value, ast.Dict):
                    candidates.append(node.value)
        if len(candidates) != 1:
            continue
        literal = candidates[0]
        if not all(
            isinstance(key, ast.Constant) and isinstance(key.value, str)
            for key in literal.keys
        ):
            continue
        observed = frozenset(key.value for key in literal.keys)
        missing = sorted(set(fields) - observed)
        if not missing:
            continue
        results.append(
            Finding(
                code="QG216",
                severity="error",
                confidence="high",
                path=path,
                line=producer.line,
                column=literal.col_offset,
                symbol=producer.producer_symbol_id,
                message=(
                    "确定的 TypedDict 生产者字典字面量缺少必需字段："
                    + ", ".join(missing)
                ),
                suggestion=(
                    "检查声明与生产者是否属于同一有效契约；"
                    "修复返回/赋值字面量或调整真实可选字段的 Schema。"
                ),
                evidence={
                    "capability": "SOURCE_SCHEMA_TO_CLOSED_LITERAL_PRODUCER",
                    "source_sha256": producer.source_sha256,
                    "schema_symbol_id": producer.schema_symbol_id,
                    "producer_symbol_id": producer.producer_symbol_id,
                    "producer_kind": producer.kind,
                    "schema_field_lines": {name: fields[name].line for name in missing},
                    "observed_fields": sorted(observed),
                    "missing_required_fields": missing,
                    "status": "CONDITIONAL_PRODUCER_CONTRACT_VIOLATION",
                },
                source="source-bound-r3",
            )
        )
    return sorted(
        results, key=lambda finding: (finding.path, finding.line, finding.symbol)
    )
