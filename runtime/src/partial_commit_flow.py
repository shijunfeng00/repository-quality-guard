"""Source-bound review of non-atomic state changes under a swallowed exception.

Only CPython built-in set/dict state authored in the same class is eligible.
The witness is conditional: a later mutation can fail after an earlier one
commits, while the handler returns without restoring either field.
"""

from __future__ import annotations

import ast
import hashlib

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .core_rule_contract import CoreQualityRule
from .model import Finding


def _receiver_field(node: ast.AST, receiver: str) -> str | None:
    """Identify one direct field on the authored method receiver."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == receiver
    ):
        return node.attr
    return None


def _initial_fields(cls: ast.ClassDef) -> dict[str, str]:
    """Recognize locally constructed built-in set/dict fields, not inferred names."""
    result: dict[str, str] = {}
    init = next(
        (
            item
            for item in cls.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == "__init__"
            and not item.decorator_list
        ),
        None,
    )
    if init is None or not init.args.args:
        return result
    receiver = init.args.args[0].arg
    for stmt in init.body:
        if isinstance(stmt, ast.AnnAssign):
            pairs = [(stmt.target, stmt.value)]
        elif isinstance(stmt, ast.Assign):
            pairs = [(target, stmt.value) for target in stmt.targets]
        else:
            continue
        for target, value in pairs:
            field = _receiver_field(target, receiver)
            if field is None or value is None:
                continue
            kind = None
            if isinstance(value, ast.Set):
                kind = "set"
            elif isinstance(value, ast.Dict) and not value.keys:
                kind = "dict"
            elif (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in {"set", "dict"}
                and not value.args
                and not value.keywords
            ):
                kind = value.func.id
            if kind is not None:
                result[field] = kind
            else:
                result.pop(field, None)
    return result


def _mutating_step(
    stmt: ast.stmt, receiver: str, fields: dict[str, str]
) -> tuple[str, str] | None:
    """Recognize only explicit built-in mutations; no arbitrary method heuristics."""
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
        if isinstance(call.func, ast.Attribute):
            field = _receiver_field(call.func.value, receiver)
            if (
                field is not None
                and fields.get(field) == "set"
                and call.func.attr == "add"
                and len(call.args) == 1
                and not call.keywords
            ):
                return field, "set.add"
    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        if isinstance(stmt, ast.AnnAssign):
            target = stmt.target
        elif len(stmt.targets) == 1:
            target = stmt.targets[0]
        else:
            return None
        if isinstance(target, ast.Subscript):
            field = _receiver_field(target.value, receiver)
            if field is not None and fields.get(field) == "dict":
                return field, "dict.__setitem__"
    return None


def _handler_may_compensate(
    handler: ast.ExceptHandler, receiver: str, affected: set[str]
) -> bool:
    """Unknown recovery of either state must prevent a positive finding."""
    for part in ast.walk(handler):
        if isinstance(part, ast.Attribute):
            if _receiver_field(part, receiver) in affected and isinstance(
                part.ctx, (ast.Store, ast.Del)
            ):
                return True
        if isinstance(part, ast.Subscript):
            if _receiver_field(part.value, receiver) in affected and isinstance(
                part.ctx, (ast.Store, ast.Del)
            ):
                return True
        if isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute):
            if _receiver_field(part.func.value, receiver) in affected:
                return True
    return False


def _returns_without_reraise(handler: ast.ExceptHandler) -> bool:
    """A simple fallback path with no authored rollback or re-raise."""
    if not handler.body or not isinstance(handler.body[-1], ast.Return):
        return False
    return not any(
        isinstance(part, (ast.Raise, ast.Delete, ast.Try, ast.TryStar))
        for stmt in handler.body
        for part in ast.walk(stmt)
    )


class PartialCommitRule(CoreQualityRule[RepositoryAnalysisSnapshot]):
    """Detect QG213 potentially non-atomic state writes before swallowed errors.

    Walk frozen AST statements in lexical order and preserve exact code-site
    evidence only when a handler returns without re-raising or rollback.
    """

    code = "QG213"

    def evaluate(self, snapshot: RepositoryAnalysisSnapshot) -> list[Finding]:
        """Emit conditional R3 witnesses from the frozen source, not runtime guesses.

        Args:
            snapshot: Frozen parsed source and module units used to reconstruct lexical obligations.

        Returns:
            Conditional risks where separately proven writes can leave state partially committed."""
        findings: list[Finding] = []
        for path in snapshot.paths:
            unit = snapshot.units[path]
            if unit.tree is None:
                continue
            # A locally shadowed set()/dict() call has no certified built-in type.
            if any(
                isinstance(part, ast.Name)
                and isinstance(part.ctx, (ast.Store, ast.Del))
                and part.id in {"set", "dict"}
                or isinstance(
                    part, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
                and part.name in {"set", "dict"}
                or isinstance(part, ast.arg)
                and part.arg in {"set", "dict"}
                or isinstance(part, (ast.Import, ast.ImportFrom))
                and any(alias.asname in {"set", "dict"} for alias in part.names)
                for part in ast.walk(unit.tree)
            ):
                continue
            source_hash = hashlib.sha256(unit.source.encode()).hexdigest()
            for cls in unit.tree.body:
                if not isinstance(cls, ast.ClassDef):
                    continue
                fields = _initial_fields(cls)
                if not fields:
                    continue
                for method in cls.body:
                    if (
                        not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                        or not method.args.args
                    ):
                        continue
                    receiver = method.args.args[0].arg
                    for stmt in method.body:
                        if not isinstance(stmt, ast.Try):
                            continue
                        broad = [
                            handler
                            for handler in stmt.handlers
                            if handler.type is None
                            or isinstance(handler.type, ast.Name)
                            and handler.type.id in {"Exception", "BaseException"}
                        ]
                        if not any(
                            _returns_without_reraise(handler) for handler in broad
                        ):
                            continue
                        prior: tuple[ast.stmt, str, str] | None = None
                        for body_stmt in stmt.body:
                            mutation = _mutating_step(body_stmt, receiver, fields)
                            if mutation is None:
                                # Only certify contiguous authored state mutations,
                                # not flow through a branch, with, or callback.
                                prior = None
                                continue
                            field, operation = mutation
                            if prior is not None and prior[1] != field:
                                first, first_field, first_op = prior
                                if any(
                                    _handler_may_compensate(
                                        handler, receiver, {first_field, field}
                                    )
                                    for handler in broad
                                    if _returns_without_reraise(handler)
                                ):
                                    break
                                findings.append(
                                    Finding(
                                        code="QG213",
                                        severity="warning",
                                        confidence="medium",
                                        path=path,
                                        line=body_stmt.lineno,
                                        column=body_stmt.col_offset + 1,
                                        symbol=f"{cls.name}.{method.name}",
                                        message="已知容器状态先后变更处于同一异常转结果路径，可能留下部分提交。",
                                        suggestion="核查第二次变更失败时第一项状态是否需回滚或合并为不可分割事务；有意允许部分提交时记录不变量。",
                                        evidence={
                                            "source_sha256": source_hash,
                                            "receiver": receiver,
                                            "first_field": first_field,
                                            "first_operation": first_op,
                                            "first_line": first.lineno,
                                            "second_field": field,
                                            "second_operation": operation,
                                            "second_line": body_stmt.lineno,
                                            "handler_lines": [
                                                h.lineno
                                                for h in broad
                                                if _returns_without_reraise(h)
                                            ],
                                            "capability": "SOURCE_BOUND_PARTIAL_COMMIT_OPEN_RUNTIME",
                                            "status": "CONDITIONAL_REVIEW_NOT_CONFIRMED_FAILURE",
                                        },
                                        source="source-bound-r3",
                                    )
                                )
                                break
                            prior = (body_stmt, field, operation)
        return findings


def partial_commit_findings(
    snapshot: RepositoryAnalysisSnapshot,
) -> tuple[Finding, ...]:
    """Evaluate partial-commit risks over an existing source snapshot.

    Args:
        snapshot: Parsed Git worktree source and stable paths, shared with
            other R3 source-bound rules.

    Returns:
        Ordered QG213 conditional-review Findings for state writes that could
        survive a swallowed failure; no findings for safe or unknown patterns.
    """
    return tuple(PartialCommitRule().evaluate(snapshot))
