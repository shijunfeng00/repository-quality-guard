"""Source-bound instance receiver operations projected into the shared topology.

This restricted, source-local observation is not a proof of field initialization,
constructor reachability, alias completeness, or runtime dispatch identity.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .topology_facts import InstanceFieldAccessFact, RepositoryTopology


class _LocalAccesses(ast.NodeVisitor):
    """Visit only the immediate method scope; nested closures need alias proof."""

    def __init__(self, receiver: str) -> None:
        self.receiver = receiver
        self.rebound = False
        self.accesses: list[tuple[ast.Attribute, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Reject nested lexical scopes, whose receiver ownership can escape."""

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef

    def visit_Name(self, node: ast.Name) -> None:
        """Any assignment/deletion of the formal receiver invalidates the domain."""
        if node.id == self.receiver and isinstance(node.ctx, (ast.Store, ast.Del)):
            self.rebound = True

    def visit_Global(self, node: ast.Global) -> None:
        """Global redeclaration invalidates lexical parameter identity."""
        if self.receiver in node.names:
            self.rebound = True

    visit_Nonlocal = visit_Global

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """An unassigned annotation has no field write side effect."""
        if node.value is not None:
            self.visit(node.value)
            self.visit(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """Track an attribute augmented assignment as a read followed by a write."""
        target = node.target
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == self.receiver
        ):
            self.accesses.append((target, "READ_WRITE"))
            self.visit(target.value)
        else:
            self.visit(target)
        self.visit(node.value)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Record real AST read/write/delete, independent of attribute spelling."""
        if isinstance(node.value, ast.Name) and node.value.id == self.receiver:
            operation = (
                "WRITE"
                if isinstance(node.ctx, ast.Store)
                else "DELETE"
                if isinstance(node.ctx, ast.Del)
                else "READ"
            )
            self.accesses.append((node, operation))
        self.generic_visit(node)


def instance_field_accesses(
    snapshot: RepositoryAnalysisSnapshot,
    topology: RepositoryTopology,
) -> tuple[InstanceFieldAccessFact, ...]:
    """Bind source-local field effects to one authored class and receiver parameter.

    Args:
        snapshot: Exact immutable Python source and parsed AST.
        topology: Already normalized repository symbol identities.

    Returns:
        Located conservative field-operation facts without runtime MUST claims.
    """
    classes = {
        (item.path.as_posix(), item.line): item
        for item in topology.symbols
        if item.language == "python" and item.kind == "class"
    }
    methods = {
        (item.path.as_posix(), item.line): item
        for item in topology.symbols
        if item.language == "python" and item.kind == "method"
    }
    result: set[InstanceFieldAccessFact] = set()
    for path in snapshot.paths:
        unit = snapshot.units[path]
        if unit.tree is None:
            continue
        source_hash = hashlib.sha256(unit.source.encode()).hexdigest()
        for class_node in unit.tree.body:
            if not isinstance(class_node, ast.ClassDef):
                continue
            owner = classes.get((path, class_node.lineno))
            if owner is None:
                continue
            for method_node in class_node.body:
                if not isinstance(method_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                method = methods.get((path, method_node.lineno))
                if method is None or method.owner_id != owner.symbol_id:
                    continue
                # A decorator can change parameter binding/dispatch; do not infer
                # anything from decorator names (including @staticmethod).
                if method_node.decorator_list:
                    continue
                positional = (*method_node.args.posonlyargs, *method_node.args.args)
                if not positional:
                    continue
                visitor = _LocalAccesses(positional[0].arg)
                for statement in method_node.body:
                    visitor.visit(statement)
                if visitor.rebound:
                    continue
                for access, operation in visitor.accesses:
                    for op in (operation,):
                        result.add(
                            InstanceFieldAccessFact(
                                path=Path(path),
                                line=access.lineno,
                                column=access.col_offset + 1,
                                source_sha256=source_hash,
                                owner_symbol_id=owner.symbol_id,
                                method_symbol_id=method.symbol_id,
                                receiver_parameter_index=0,
                                member=access.attr,
                                operation=op,
                            )
                        )
    # The same authored class can declare callable members.  A callable reference
    # is not evidence of a state-field read unless that member is also actually
    # assigned/deleted in the class's observed instance-parameter domain.
    methods_by_owner: dict[str, set[str]] = {}
    for symbol in topology.symbols:
        if symbol.language == "python" and symbol.kind == "method":
            methods_by_owner.setdefault(symbol.owner_id, set()).add(symbol.name)
    written = {
        (fact.owner_symbol_id, fact.member)
        for fact in result
        if fact.operation in {"WRITE", "READ_WRITE", "DELETE"}
    }
    result = {
        fact
        for fact in result
        if fact.operation != "READ"
        or fact.member not in methods_by_owner.get(fact.owner_symbol_id, set())
        or (fact.owner_symbol_id, fact.member) in written
    }
    return tuple(
        sorted(
            result,
            key=lambda x: (
                x.path.as_posix(),
                x.line,
                x.column,
                x.method_symbol_id,
                x.member,
                x.operation,
            ),
        )
    )
