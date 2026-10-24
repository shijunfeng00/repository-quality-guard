"""Verify closed literal producers against normalized required TypedDict fields."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .core_rule_contract import CoreQualityRule
from .model import Finding
from .topology_facts import RepositoryTopology, TypedProducerFact, TypedSchemaFieldFact


@dataclass(frozen=True)
class TypedLiteralRuleContext:
    """Bind one declared producer to its unique AST literal and schema fields.

    Bind one producer fact, closed AST dictionary and required schema fields.
    """

    producer: TypedProducerFact
    literal: ast.Dict
    required_fields: dict[str, TypedSchemaFieldFact]


class TypedRequiredLiteralRule(CoreQualityRule[TypedLiteralRuleContext]):
    """QG216: reject a source-bound closed mapping missing required schema keys.

    Evaluate an independently instantiated QG216 against shared typed facts.
    """

    code = "QG216"

    def evaluate(self, context: TypedLiteralRuleContext) -> list[Finding]:
        """Check an exact TypedDict producer literal for missing mandatory keys.

        Args:
            context: Proven schema/producer identity, closed dictionary AST literal
                and required fields resolved from typed topology facts.

        Returns:
            QG216 high-confidence Findings with missing field names and source
            evidence, or none for complete and dynamically unprovable mappings.
        """
        producer = context.producer
        literal = context.literal
        if not all(
            isinstance(key, ast.Constant) and isinstance(key.value, str)
            for key in literal.keys
        ):
            return []
        observed = frozenset(key.value for key in literal.keys)
        missing = sorted(set(context.required_fields) - observed)
        if not missing:
            return []
        return [
            Finding(
                code=self.code,
                severity="error",
                confidence="high",
                path=producer.path.as_posix(),
                line=producer.line,
                column=literal.col_offset,
                symbol=producer.producer_symbol_id,
                message="确定的 TypedDict 生产者字典字面量缺少必需字段："
                + ", ".join(missing),
                suggestion="检查声明与生产者是否属于同一有效契约；修复返回/赋值字面量或调整真实可选字段的 Schema。",
                evidence={
                    "capability": "SOURCE_SCHEMA_TO_CLOSED_LITERAL_PRODUCER",
                    "source_sha256": producer.source_sha256,
                    "schema_symbol_id": producer.schema_symbol_id,
                    "producer_symbol_id": producer.producer_symbol_id,
                    "producer_kind": producer.kind,
                    "schema_field_lines": {
                        name: context.required_fields[name].line for name in missing
                    },
                    "observed_fields": sorted(observed),
                    "missing_required_fields": missing,
                    "status": "CONDITIONAL_PRODUCER_CONTRACT_VIOLATION",
                },
                source="source-bound-r3",
            )
        ]


def _literal_index(tree: ast.Module) -> dict[tuple[int, str], list[ast.Dict]]:
    """Index existing AST literals once by their authored statement location.

    The rule consumes shared source facts and retains stable Finding evidence.
    """
    candidates: dict[tuple[int, str], list[ast.Dict]] = {}
    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            candidates.setdefault((line, "OBSERVED_RETURN_LITERAL"), []).append(
                node.value
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict):
            candidates.setdefault((line, "ANNOTATED_ASSIGNMENT"), []).append(node.value)
    return candidates


def typed_producer_contract_findings(
    snapshot: RepositoryAnalysisSnapshot, topology: RepositoryTopology
) -> list[Finding]:
    """Join TypedDict producer facts with their exact source AST literals.

    Args:
        snapshot: Parsed source units keyed by stable repository-relative path.
        topology: Schema requiredness and typed producer facts with source hashes.

    Returns:
        Sorted QG216 Findings for uniquely matched return/assignment literals;
        unmatched, changed, or unresolved producers are not guessed.
    """
    required: dict[str, dict[str, TypedSchemaFieldFact]] = {}
    for field in topology.typed_schema_fields:
        if field.required:
            required.setdefault(field.schema_symbol_id, {})[field.field] = field
    direct_returns = {
        (row.schema_symbol_id, row.producer_symbol_id)
        for row in topology.typed_producers
        if row.kind == "DECLARED_RETURN"
    }
    index_by_path: dict[str, dict[tuple[int, str], list[ast.Dict]]] = {}
    rule = TypedRequiredLiteralRule()
    results: list[Finding] = []
    for producer in topology.typed_producers:
        if producer.kind not in {"OBSERVED_RETURN_LITERAL", "ANNOTATED_ASSIGNMENT"}:
            continue
        if (
            producer.kind == "OBSERVED_RETURN_LITERAL"
            and (producer.schema_symbol_id, producer.producer_symbol_id)
            not in direct_returns
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
        literals = index_by_path.get(path)
        if literals is None:
            literals = _literal_index(unit.tree)
            index_by_path[path] = literals
        candidates = literals.get((producer.line, producer.kind), [])
        if len(candidates) != 1:
            continue
        results.extend(
            rule.evaluate(TypedLiteralRuleContext(producer, candidates[0], fields))
        )
    return sorted(
        results, key=lambda finding: (finding.path, finding.line, finding.symbol)
    )
