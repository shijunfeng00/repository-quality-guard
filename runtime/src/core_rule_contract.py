"""Core rule identity and evaluation contract over shared repository facts."""

from __future__ import annotations

from abc import ABC, abstractmethod
import ast
from pathlib import Path
from typing import ClassVar, Generic, TypeVar

from .model import Finding

ContextT = TypeVar("ContextT")


class CoreQualityRule(ABC, Generic[ContextT]):
    """Define the evaluation interface shared by independent Core QG owners.

    Rule objects receive already-derived facts; they must not parse a new source
    tree or fabricate an evidence graph. Each subclass declares its QG ``code``
    and returns zero or more findings for the supplied context.
    """

    code: ClassVar[str]

    @abstractmethod
    def evaluate(self, context: ContextT) -> list[Finding]:
        """Classify a shared analysis context according to this rule's contract.

        Implementations must keep source locations, confidence, and negative
        conditions grounded in their typed evidence; an empty result represents
        a case that this owner cannot prove violates its rule.

        Args:
            context: Rule-specific facts prepared by the scanner or evaluator.

        Returns:
            Independently attributable findings for this QG code, or an empty
            list when no contract violation is established.
        """
        raise NotImplementedError


def duplicate_rule_owners(source_root: Path) -> dict[str, tuple[str, ...]]:
    """Return every Core QG code declared by more than one rule object.

    The inventory is a release-integrity fact rather than a scanner finding:
    it reads all production rule declarations from the supplied source tree,
    without binding the check to an individual module path or source line.

    Args:
        source_root: Root directory containing the production Python sources.

    Returns:
        Deterministic QG code to fully-qualified owner names for duplicate
        declarations only.

    Raises:
        ValueError: The supplied path is not a readable source directory.
    """
    if not source_root.is_dir():
        raise ValueError("Core rule source root does not exist: " + str(source_root))
    owners: dict[str, list[str]] = {}
    for source_path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        module = source_path.relative_to(source_root).with_suffix("").as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not any(
                _core_rule_base(base) for base in node.bases
            ):
                continue
            code = _declared_rule_code(node)
            if code is not None:
                owners.setdefault(code, []).append(
                    module.replace("/", ".") + "." + node.name
                )
    return {
        code: tuple(sorted(owner_names))
        for code, owner_names in sorted(owners.items())
        if len(owner_names) > 1
    }


def _core_rule_base(base: ast.expr) -> bool:
    """Identify a direct ``CoreQualityRule`` base without importing rule modules."""
    value = base.value if isinstance(base, ast.Subscript) else base
    return isinstance(value, ast.Name) and value.id == "CoreQualityRule"


def _declared_rule_code(node: ast.ClassDef) -> str | None:
    """Extract one literal ``code`` declaration from a Core rule class."""
    for statement in node.body:
        target = (
            statement.targets[0]
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1
            else statement.target
            if isinstance(statement, ast.AnnAssign)
            else None
        )
        value = (
            statement.value
            if isinstance(statement, (ast.Assign, ast.AnnAssign))
            else None
        )
        if (
            isinstance(target, ast.Name)
            and target.id == "code"
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            return value.value
    return None
