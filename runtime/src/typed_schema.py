"""Source-bound TypedDict declarations and producer observations for the shared IR.

The Python AST and normalized repository symbol graph are the only inputs. A
source annotation is not a runtime type guarantee. Unknown import bindings or
rebound aliases cannot silently become schema evidence.
"""

from __future__ import annotations

import ast
import hashlib
import symtable
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .topology_facts import RepositoryTopology, TypedProducerFact, TypedSchemaFieldFact

_TYPING_MODULES = frozenset({"typing", "typing_extensions"})
_SCHEMA_MARKERS = frozenset({"TypedDict"})
_REQUIRED_MARKERS = frozenset({"Required", "NotRequired"})
_BUILTIN_TYPES = frozenset({"str", "int", "float", "bool", "bytes", "object"})


def _imported_contracts(
    tree: ast.Module, table: symtable.SymbolTable
) -> dict[str, str]:
    """Authenticate imported typing symbols against CPython's module bindings."""
    bindings: dict[str, str] = {}
    seen: set[str] = set()
    competing: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for item in stmt.names:
                bound = item.asname or item.name.split(".", 1)[0]
                if bound in seen:
                    competing.add(bound)
                seen.add(bound)
                if item.name in _TYPING_MODULES:
                    bindings[bound] = item.name
        elif isinstance(stmt, ast.ImportFrom):
            for item in stmt.names:
                bound = item.asname or item.name
                if bound in seen:
                    competing.add(bound)
                seen.add(bound)
                if stmt.module in _TYPING_MODULES and item.name in (
                    _SCHEMA_MARKERS | _REQUIRED_MARKERS
                ):
                    bindings[bound] = item.name
    return {
        name: value
        for name, value in bindings.items()
        if name not in competing
        and name in table.get_identifiers()
        and not table.lookup(name).is_assigned()
    }


def _marker(node: ast.expr, imports: dict[str, str]) -> str:
    """Resolve the authored Required or NotRequired annotation marker without runtime evaluation."""
    if isinstance(node, ast.Name):
        value = imports.get(node.id, "")
        return value if value in _SCHEMA_MARKERS | _REQUIRED_MARKERS else ""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if imports.get(node.value.id) in _TYPING_MODULES:
            return node.attr if node.attr in _SCHEMA_MARKERS | _REQUIRED_MARKERS else ""
    return ""


def _unwrap_annotation(
    annotation: ast.expr, imports: dict[str, str], total: bool
) -> tuple[ast.expr, bool] | None:
    """Extract the underlying schema field annotation and explicit requirement flag."""
    if isinstance(annotation, ast.Subscript):
        marker = _marker(annotation.value, imports)
        if marker in _REQUIRED_MARKERS:
            if isinstance(annotation.slice, ast.Tuple):
                return None
            return annotation.slice, marker == "Required"
        # Unresolved Required-like wrapper must not be interpreted as optional/required.
        if isinstance(annotation.value, (ast.Name, ast.Attribute)):
            spelling = (
                annotation.value.id
                if isinstance(annotation.value, ast.Name)
                else annotation.value.attr
            )
            if spelling in _REQUIRED_MARKERS:
                return None
    return annotation, total


def _binding(annotation: ast.expr, shadowed: frozenset[str]) -> str:
    """Return a source-only type binding for one explicit annotated expression."""
    if isinstance(annotation, ast.Name) and annotation.id in _BUILTIN_TYPES:
        return "BUILTIN" if annotation.id not in shadowed else "OPEN_REFERENCE"
    return "OPEN_REFERENCE"


def _literal_keys(value: ast.expr | None) -> tuple[str, ...]:
    """Extract exact string keys from an authored closed dictionary literal."""
    if not isinstance(value, ast.Dict):
        return ()
    keys: list[str] = []
    for key in value.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return ()
        keys.append(key.value)
    return tuple(keys)


def _declared_schema(
    annotation: ast.expr | None,
    schemas: dict[str, str],
    shadowed: frozenset[str],
) -> tuple[str, str] | None:
    """Resolve an explicitly declared TypedDict return annotation to a unique schema."""
    if isinstance(annotation, ast.Name) and annotation.id in schemas:
        return schemas[annotation.id], "DECLARED_RETURN"
    if not isinstance(annotation, ast.Subscript) or not isinstance(
        annotation.value, ast.Name
    ):
        return None
    container = annotation.value.id
    if container in shadowed:
        return None
    value = annotation.slice
    if container in {"list", "set", "tuple"} and isinstance(value, ast.Name):
        return (
            (schemas[value.id], "DECLARED_CONTAINER_RETURN")
            if value.id in schemas
            else None
        )
    if container == "dict" and isinstance(value, ast.Tuple) and len(value.elts) == 2:
        element = value.elts[1]
        if isinstance(element, ast.Name) and element.id in schemas:
            return schemas[element.id], "DECLARED_CONTAINER_RETURN"
    return None


def _imported_schema_aliases(
    tree: ast.Module, table: symtable.SymbolTable, schema_ids: set[str]
) -> dict[str, str]:
    """Resolve only unique direct imports to already-authenticated local schemas.

    A same-spelled alias, a relative import, or later source assignment is an
    open boundary.  This deliberately accepts the CodeGraph-compatible pure
    alias case only when the local source snapshot provides one exact target.
    """
    candidates: dict[str, str] = {}
    ambiguous: set[str] = set()
    observed: dict[str, str] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            imports = (
                (item.asname or item.name.split(".", 1)[0], "OPEN_IMPORT:" + item.name)
                for item in statement.names
            )
        elif isinstance(statement, ast.ImportFrom):
            # Relative and external imports also occupy the alias.  They may
            # shadow an earlier known schema even when their target is open.
            imports = (
                (
                    item.asname or item.name,
                    (
                        statement.module + "." + item.name
                        if statement.module and statement.level == 0
                        else "OPEN_IMPORT:" + str(statement.module) + "." + item.name
                    ),
                )
                for item in statement.names
                if item.name != "*"
            )
        else:
            continue
        for alias, target in imports:
            if alias in observed and observed[alias] != target:
                ambiguous.add(alias)
            observed[alias] = target
            if target in schema_ids:
                candidates[alias] = target
    return {
        alias: target
        for alias, target in candidates.items()
        if alias not in ambiguous
        and alias in table.get_identifiers()
        and not table.lookup(alias).is_assigned()
    }


def _authored_returns(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.Return, ...]:
    """Collect branch/handler returns without crossing another lexical owner.

    A nested function or lambda has its own return contract; assigning its
    literal to the enclosing producer would invent a schema violation.
    """
    returns: list[ast.Return] = []

    class FindReturns(ast.NodeVisitor):
        """Collect immediate function return statements without crossing lexical owners.

        All observations remain local to the parsed source; runtime execution is not inferred.
        """

        def visit_Return(self, statement: ast.Return) -> None:
            """Record a return statement belonging to the current function body.

            Args:
                statement: ast.Return input used by the visit_Return analysis.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            returns.append(statement)

        def visit_FunctionDef(self, statement: ast.FunctionDef) -> None:
            """Stop at nested functions so their returns never belong to the outer function.

            Args:
                statement: ast.FunctionDef input used by the visit_FunctionDef analysis.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            return

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        visit_Lambda = visit_FunctionDef

    visitor = FindReturns()
    for statement in function.body:
        visitor.visit(statement)
    return tuple(returns)


def typed_schema_facts(
    snapshot: RepositoryAnalysisSnapshot,
    topology: RepositoryTopology,
) -> tuple[tuple[TypedSchemaFieldFact, ...], tuple[TypedProducerFact, ...]]:
    """Project verified source-authored TypedDict contracts into one topology.

    Args:
        snapshot: Frozen source/AST analysis identity.
        topology: Existing normalized symbol identities.

    Returns:
        Stable schema field and explicit producer-declaration observations.
    """
    classes = {
        (symbol.path.as_posix(), symbol.line): symbol
        for symbol in topology.symbols
        if symbol.language == "python" and symbol.kind == "class" and not symbol.foreign
    }
    functions = {
        (symbol.path.as_posix(), symbol.line): symbol
        for symbol in topology.symbols
        if symbol.language == "python"
        and symbol.kind in {"function", "method"}
        and not symbol.foreign
    }
    field_rows: list[TypedSchemaFieldFact] = []
    producer_rows: list[TypedProducerFact] = []
    for path in snapshot.paths:
        unit = snapshot.units[path]
        if unit.tree is None:
            continue
        try:
            module_scope = symtable.symtable(unit.source, path, "exec")
        except SyntaxError:
            continue
        imports = _imported_contracts(unit.tree, module_scope)
        digest = hashlib.sha256(unit.source.encode()).hexdigest()
        shadowed = frozenset(
            name
            for name in module_scope.get_identifiers()
            if module_scope.lookup(name).is_assigned()
            or module_scope.lookup(name).is_imported()
        )
        schemas: dict[str, str] = {}
        for stmt in unit.tree.body:
            if (
                not isinstance(stmt, ast.ClassDef)
                or stmt.decorator_list
                or len(stmt.bases) != 1
            ):
                continue
            if _marker(stmt.bases[0], imports) != "TypedDict":
                continue
            symbol = classes.get((path, stmt.lineno))
            if symbol is None or symbol.nested:
                continue
            if any(
                keyword.arg != "total"
                or not isinstance(keyword.value, ast.Constant)
                or not isinstance(keyword.value.value, bool)
                for keyword in stmt.keywords
            ):
                continue
            total = stmt.keywords[0].value.value if stmt.keywords else True
            if any(
                not isinstance(child, (ast.AnnAssign, ast.Expr, ast.Pass))
                for child in stmt.body
            ):
                continue
            schema_fields: list[TypedSchemaFieldFact] = []
            valid = True
            for child in stmt.body:
                if not isinstance(child, ast.AnnAssign):
                    continue
                if not isinstance(child.target, ast.Name) or child.value is not None:
                    valid = False
                    break
                if any(existing.field == child.target.id for existing in schema_fields):
                    valid = False
                    break
                resolved = _unwrap_annotation(child.annotation, imports, total)
                if resolved is None:
                    valid = False
                    break
                declared_type, required = resolved
                schema_fields.append(
                    TypedSchemaFieldFact(
                        schema_symbol_id=symbol.symbol_id,
                        path=Path(path),
                        line=child.lineno,
                        source_sha256=digest,
                        field=child.target.id,
                        annotation=ast.unparse(declared_type),
                        required=required,
                        type_binding=_binding(declared_type, shadowed),
                    )
                )
            if not valid:
                continue
            field_rows.extend(schema_fields)
            schemas[stmt.name] = symbol.symbol_id
        declarations: list[ast.stmt] = []
        for stmt in unit.tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                declarations.append(stmt)
            elif isinstance(stmt, ast.ClassDef):
                declarations.extend(
                    member
                    for member in stmt.body
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
            elif isinstance(stmt, ast.AnnAssign):
                declarations.append(stmt)
        for stmt in declarations:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = functions.get((path, stmt.lineno))
                declaration = _declared_schema(stmt.returns, schemas, shadowed)
                if symbol is None or declaration is None or stmt.decorator_list:
                    continue
                schema_id, kind = declaration
                # Decorators and nested callbacks can rebind runtime signatures.
                producer_rows.append(
                    TypedProducerFact(
                        schema_id,
                        symbol.symbol_id,
                        Path(path),
                        stmt.lineno,
                        digest,
                        kind,
                    )
                )
                for child in _authored_returns(stmt):
                    if isinstance(child.value, ast.Dict):
                        producer_rows.append(
                            TypedProducerFact(
                                schema_id,
                                symbol.symbol_id,
                                Path(path),
                                child.lineno,
                                digest,
                                "OBSERVED_RETURN_LITERAL",
                                _literal_keys(child.value),
                            )
                        )
            elif (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.annotation, ast.Name)
                and stmt.value is not None
            ):
                schema_id = schemas.get(stmt.annotation.id)
                if schema_id is not None and isinstance(stmt.target, ast.Name):
                    producer_rows.append(
                        TypedProducerFact(
                            schema_id,
                            f"{path}:{stmt.lineno}:{stmt.target.id}",
                            Path(path),
                            stmt.lineno,
                            digest,
                            "ANNOTATED_ASSIGNMENT",
                            _literal_keys(stmt.value),
                        )
                    )
    # The first pass authenticates every schema in the snapshot.  A second,
    # strictly direct-import pass can then connect an imported return
    # annotation to that exact schema without treating spelling as evidence.
    schema_ids = {field.schema_symbol_id for field in field_rows}
    for path in snapshot.paths:
        unit = snapshot.units[path]
        if unit.tree is None:
            continue
        try:
            module_scope = symtable.symtable(unit.source, path, "exec")
        except SyntaxError:
            continue
        aliases = _imported_schema_aliases(unit.tree, module_scope, schema_ids)
        if not aliases:
            continue
        digest = hashlib.sha256(unit.source.encode()).hexdigest()
        declarations: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for statement in unit.tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                declarations.append(statement)
            elif isinstance(statement, ast.ClassDef):
                declarations.extend(
                    member
                    for member in statement.body
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        for declaration_node in declarations:
            if not isinstance(declaration_node.returns, ast.Name):
                continue
            schema_id = aliases.get(declaration_node.returns.id)
            symbol = functions.get((path, declaration_node.lineno))
            if schema_id is None or symbol is None or declaration_node.decorator_list:
                continue
            producer_rows.append(
                TypedProducerFact(
                    schema_id,
                    symbol.symbol_id,
                    Path(path),
                    declaration_node.lineno,
                    digest,
                    "DECLARED_RETURN",
                )
            )
            for child in _authored_returns(declaration_node):
                if isinstance(child.value, ast.Dict):
                    producer_rows.append(
                        TypedProducerFact(
                            schema_id,
                            symbol.symbol_id,
                            Path(path),
                            child.lineno,
                            digest,
                            "OBSERVED_RETURN_LITERAL",
                            _literal_keys(child.value),
                        )
                    )

    return (
        tuple(
            sorted(
                set(field_rows),
                key=lambda x: (x.path.as_posix(), x.line, x.schema_symbol_id, x.field),
            )
        ),
        tuple(
            sorted(
                set(producer_rows),
                key=lambda x: (x.path.as_posix(), x.line, x.producer_symbol_id, x.kind),
            )
        ),
    )
