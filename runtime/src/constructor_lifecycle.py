"""Source-authenticated constructor hierarchy observations in the shared IR.

Only statically resolved, undecorated local class declarations enter the bounded
C3 domain. C3 merge is computed from proven source inheritance edges without constructing
runtime types or inspecting MRO attributes. This is not runtime dispatch closure.
"""

from __future__ import annotations

import ast
import hashlib
import symtable
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .topology_facts import ConstructorLifecycleFact, RepositoryTopology, UsageKind


def _immediate_calls(method: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, ...]:
    """Locate zero-argument builtin-super constructor calls outside nested scopes."""

    class Calls(ast.NodeVisitor):
        def __init__(self) -> None:
            self.lines: set[int] = set()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        visit_Lambda = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            target = node.func
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "__init__"
                and isinstance(target.value, ast.Call)
                and isinstance(target.value.func, ast.Name)
                and target.value.func.id == "super"
                and not target.value.args
                and not target.value.keywords
            ):
                self.lines.add(node.lineno)
            self.generic_visit(node)

    visitor = Calls()
    for statement in method.body:
        visitor.visit(statement)
    return tuple(sorted(visitor.lines))


def _bound_builtin_super(
    table: symtable.SymbolTable, owner: ast.ClassDef, method: ast.FunctionDef
) -> bool:
    """Use CPython's lexical symbol table, not a method-name dictionary."""
    if "super" in table.get_identifiers():
        binding = table.lookup("super")
        if binding.is_assigned() or binding.is_imported() or binding.is_namespace():
            return False
    cls = next(
        (
            child
            for child in table.get_children()
            if child.get_type() == "class" and child.get_lineno() == owner.lineno
        ),
        None,
    )
    if cls is None:
        return False
    func = next(
        (
            child
            for child in cls.get_children()
            if child.get_lineno() == method.lineno and child.get_type() == "function"
        ),
        None,
    )
    if func is None or "super" not in func.get_identifiers():
        return False
    binding = func.lookup("super")
    return not (
        binding.is_parameter()
        or binding.is_local()
        or binding.is_free()
        or binding.is_declared_global()
    )


def constructor_lifecycle(
    snapshot: RepositoryAnalysisSnapshot,
    topology: RepositoryTopology,
) -> tuple[ConstructorLifecycleFact, ...]:
    """Project bounded native-C3 constructor facts from existing symbol bindings.

    Static inheritance edges certify identity only for local classes; any missing
    base, dynamic class feature, or ambiguous source binding is explicitly OPEN.
    """
    classes = {
        s.symbol_id: s
        for s in topology.symbols
        if s.language == "python" and s.kind == "class" and not s.foreign
    }
    methods = {
        s.symbol_id: s
        for s in topology.symbols
        if s.language == "python" and s.kind == "method" and not s.foreign
    }
    edges: dict[str, list[str]] = {}
    for edge in topology.edges:
        if (
            edge.kind == UsageKind.INHERITANCE
            and edge.source_id in classes
            and edge.target_id in classes
        ):
            edges.setdefault(edge.source_id, []).append(edge.target_id)

    declarations: dict[str, tuple[str, ast.ClassDef]] = {}
    source_hashes: dict[str, str] = {}
    compiled_scopes: dict[str, symtable.SymbolTable | None] = {}
    by_line = {(s.path.as_posix(), s.line): s for s in classes.values()}
    for path in snapshot.paths:
        unit = snapshot.units[path]
        if unit.tree is None:
            continue
        source_hashes[path] = hashlib.sha256(unit.source.encode()).hexdigest()
        try:
            compiled_scopes[path] = symtable.symtable(unit.source, path, "exec")
        except SyntaxError:
            compiled_scopes[path] = None
        for node in unit.tree.body:
            if isinstance(node, ast.ClassDef):
                symbol = by_line.get((path, node.lineno))
                if symbol is not None and not symbol.nested:
                    declarations[symbol.symbol_id] = (path, node)

    # C3 uses only proven lexical inheritance edges. Dynamic type creation and
    # MRO reflection are unnecessary: these are source identities, not classes.
    linearizations: dict[str, tuple[str, ...]] = {}
    unresolved: set[str] = set()
    resolving: set[str] = set()

    def build(identity: str) -> tuple[str, ...] | None:
        if identity in linearizations:
            return linearizations[identity]
        if identity in unresolved or identity in resolving:
            unresolved.add(identity)
            return None
        declaration = declarations.get(identity)
        if declaration is None:
            unresolved.add(identity)
            return None
        _, node = declaration
        if (
            node.decorator_list
            or node.keywords
            or any(not isinstance(base, ast.Name) for base in node.bases)
        ):
            unresolved.add(identity)
            return None
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                child.name == "__new__"
                or (child.name == "__init__" and child.decorator_list)
            ):
                unresolved.add(identity)
                return None
        direct: list[str] = []
        known = edges.get(identity, [])
        if len(node.bases) != len(known):
            unresolved.add(identity)
            return None
        for base in node.bases:
            matching = [
                candidate for candidate in known if classes[candidate].name == base.id
            ]
            if len(matching) != 1 or matching[0] in direct:
                unresolved.add(identity)
                return None
            direct.append(matching[0])
        resolving.add(identity)
        parents = [build(parent) for parent in direct]
        resolving.remove(identity)
        if any(parent is None for parent in parents):
            unresolved.add(identity)
            return None
        sequences = [list(parent) for parent in parents if parent is not None]
        sequences.append(list(direct))
        result = [identity]
        while any(sequences):
            candidate = next(
                (
                    seq[0]
                    for seq in sequences
                    if seq and not any(seq[0] in tail[1:] for tail in sequences)
                ),
                None,
            )
            if candidate is None:  # C3 inconsistent; do not invent an MRO.
                unresolved.add(identity)
                return None
            result.append(candidate)
            for seq in sequences:
                if seq and seq[0] == candidate:
                    seq.pop(0)
        linearizations[identity] = tuple(result)
        return linearizations[identity]

    result: list[ConstructorLifecycleFact] = []
    for identity in sorted(declarations):
        path, node = declarations[identity]
        mro = build(identity) or ()
        constructor = next(
            (f"{owner}.__init__" for owner in mro if f"{owner}.__init__" in methods), ""
        )
        calls: list[tuple[str, int]] = []
        if mro:
            for owner in mro:
                decl = declarations[owner][1]
                for method in decl.body:
                    if (
                        not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                        or method.name != "__init__"
                    ):
                        continue
                    if (
                        method.decorator_list
                        or not method.args.posonlyargs + method.args.args
                    ):
                        continue
                    # Super may be rebound by a local formal or by module-level
                    # bindings; unverified builtin dispatch is never a proof.
                    formals = (
                        *method.args.posonlyargs,
                        *method.args.args,
                        *method.args.kwonlyargs,
                    )
                    if any(arg.arg == "super" for arg in formals):
                        continue
                    if isinstance(method, ast.AsyncFunctionDef):
                        continue
                    method_path = declarations[owner][0]
                    scope = compiled_scopes[method_path]
                    if scope is None or not _bound_builtin_super(scope, decl, method):
                        continue
                    for line in _immediate_calls(method):
                        calls.append((f"{owner}.__init__", line))
        # Resolve authored post-construction method entries using the exact
        # source-derived C3 symbol order. Attribute *names* are Python's
        # lookup keys, not indicators of purity, mutation or business purpose.
        # Any authored dynamic attribute hook or uncertain method descriptor
        # invalidates the source-only entry candidate; never infer a negative
        # runtime calling edge from a missing/unknown declaration.
        entries: dict[str, str] = {}
        uncertain: set[str] = set()
        if mro:
            # A method in a base is not reachable if a descendant class body
            # shadows that name with a data attribute/descriptor. Conditional
            # class-body execution may also rewrite bindings. Never treat MRO
            # function declarations alone as a complete method namespace.
            uncertain_namespace = any(
                isinstance(
                    part,
                    (
                        ast.If,
                        ast.For,
                        ast.AsyncFor,
                        ast.While,
                        ast.Try,
                        ast.With,
                        ast.AsyncWith,
                        ast.Match,
                        ast.Delete,
                    ),
                )
                for ancestor in mro
                for part in declarations[ancestor][1].body
            )
            shadowed: set[str] = set()
            for ancestor in mro:
                for part in declarations[ancestor][1].body:
                    if isinstance(part, ast.Assign):
                        for target in part.targets:
                            if isinstance(target, ast.Name):
                                shadowed.add(target.id)
                    elif (
                        isinstance(part, ast.AnnAssign)
                        and isinstance(part.target, ast.Name)
                        and part.value is not None
                    ):
                        shadowed.add(part.target.id)
            dynamic_access = uncertain_namespace or any(
                method.name in {"__getattribute__", "__getattr__"}
                for ancestor in mro
                for method in declarations[ancestor][1].body
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            for ancestor in mro:
                for member in declarations[ancestor][1].body:
                    if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    name = member.name
                    if name == "__init__" or name in entries or name in uncertain:
                        continue
                    member_id = f"{ancestor}.{name}"
                    # Decorators, descriptor rewrites and unknown binding of
                    # the implicit receiver cannot certify an instance entry.
                    positional = member.args.posonlyargs + member.args.args
                    if (
                        dynamic_access
                        or name in shadowed
                        or member.decorator_list
                        or not positional
                        or member_id not in methods
                    ):
                        uncertain.add(name)
                    else:
                        entries[name] = member_id
        result.append(
            ConstructorLifecycleFact(
                path=Path(path),
                line=node.lineno,
                source_sha256=source_hashes[path],
                owner_symbol_id=identity,
                constructor_symbol_id=constructor,
                mro_symbol_ids=mro,
                super_init_calls=tuple(calls),
                post_init_method_entries=tuple(sorted(entries.items())),
                unknown_method_entries=tuple(sorted(uncertain)),
                capability=(
                    "SOURCE_BOUND_CPYTHON_C3_OPEN_RUNTIME"
                    if mro
                    else "UNKNOWN_DYNAMIC_OR_UNBOUND_HIERARCHY"
                ),
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: (item.path.as_posix(), item.line, item.owner_symbol_id),
        )
    )
