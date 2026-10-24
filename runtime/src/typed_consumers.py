"""Source-bound TypedDict consumer observations on the shared topology.

The implementation records structural operations and syntactic guards.  It does
not infer that an arbitrary call, developer name, or type annotation certifies
runtime values, nor does it supply a global MUST/dominance verdict.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from .typed_guard_flow import presence_witnesses
from .analysis_snapshot import RepositoryAnalysisSnapshot
from .python_import_resolution import python_module_name, resolve_local_import_symbol
from .topology_facts import RepositoryTopology, TypedConsumerFact, TypedSchemaFieldFact


def _relative_module(path: str, stmt: ast.ImportFrom) -> str | None:
    """Resolve import syntax within repository package boundaries."""
    if not stmt.level:
        return stmt.module or ""
    module = python_module_name(path)
    package = (
        module
        if path.endswith("/__init__.py") or path == "__init__.py"
        else module.rpartition(".")[0]
    )
    parts = [part for part in package.split(".") if part]
    if stmt.level - 1 > len(parts):
        return None
    return ".".join(
        parts[: len(parts) - (stmt.level - 1)] + ([stmt.module] if stmt.module else [])
    )


def _scope_binding_names(tree: ast.Module) -> set[str]:
    """Conservatively reject names rebound in the module lexical scope."""

    class Bindings(ast.NodeVisitor):
        """Collect imported and assigned names at module lexical scope, without entering nested definitions.

        All observations remain local to the parsed source; runtime execution is not inferred.
        """

        def __init__(self) -> None:
            """Create independent imported-name and assigned-name collections.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            self.imported: set[str] = set()
            self.assigned: set[str] = set()

        def visit_Import(self, node: ast.Import) -> None:
            """Record the locally bound names from direct import statements.

            Args:
                node: Authored ast.Import under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            for item in node.names:
                self.imported.add(item.asname or item.name.split(".", 1)[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            """Record local bindings introduced by from-import declarations.

            Args:
                node: Authored ast.ImportFrom under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            for item in node.names:
                self.imported.add(item.asname or item.name)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            """Treat a function definition as one assignment to its name, not its body.

            Args:
                node: Authored ast.FunctionDef under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            self.assigned.add(node.name)

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Lambda(self, node: ast.Lambda) -> None:
            """Do not treat bindings inside lambda expressions as module bindings.

            Args:
                node: Authored ast.Lambda under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            return

        def visit_Name(self, node: ast.Name) -> None:
            """Record assignment or deletion targets for module-bound name conflict checks.

            Args:
                node: Authored ast.Name under examination; no user code is executed.

            Returns:
                None; updates the collector state or intentionally skips this lexical scope.
            """
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self.assigned.add(node.id)

    collector = Bindings()
    for stmt in tree.body:
        collector.visit(stmt)
    return collector.imported & collector.assigned


def _imports(path: str, tree: ast.Module, redefined: set[str]) -> dict[str, str]:
    """Use only unambiguous top-level authored import bindings."""
    imports: dict[str, str] = {}
    all_bindings: set[str] = set()
    duplicates: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.ImportFrom, ast.Import)):
            module = (
                _relative_module(path, stmt)
                if isinstance(stmt, ast.ImportFrom)
                else None
            )
            for item in stmt.names:
                alias = item.asname or item.name.split(".", 1)[0]
                if alias in all_bindings:
                    duplicates.add(alias)
                all_bindings.add(alias)
                if (
                    isinstance(stmt, ast.ImportFrom)
                    and module is not None
                    and item.name != "*"
                ):
                    imports[alias] = f"{module}.{item.name}".strip(".")
    return {
        a: target for a, target in imports.items() if a not in duplicates | redefined
    }


def _keys(node: ast.AST, parameters: set[str]) -> tuple[str, str] | None:
    """Match a literal-string indexed read whose receiver is a declared parameter; ignore other expressions."""
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id in parameters
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.value.id, node.slice.value
    return None


def _membership(test: ast.expr, parameters: set[str]) -> tuple[str, str, bool] | None:
    """Extract the polarity and literal key of an authored membership test on a parameter."""
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], (ast.In, ast.NotIn))
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Name)
        and test.comparators[0].id in parameters
        and isinstance(test.left, ast.Constant)
        and isinstance(test.left.value, str)
    ):
        return test.comparators[0].id, test.left.value, isinstance(test.ops[0], ast.In)
    return None


class _DirectReads(ast.NodeVisitor):
    """Find direct indexing reads without descending into nested lexical scopes."""

    def __init__(self, parameters: set[str]) -> None:
        """Initialize the exact parameter set and source-located direct-read observations.

        Args:
            parameters: Lexically bound parameter names allowed as receiver identities.

        Returns:
            None; updates the collector state or intentionally skips this lexical scope.
        """
        self.parameters = parameters
        self.rows: list[tuple[str, str, int, int, str]] = []

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """Record parameter key reads in load context, then visit their child expressions.

        Args:
            node: Authored ast.Subscript under examination; no user code is executed.

        Returns:
            None; updates the collector state or intentionally skips this lexical scope.
        """
        match = _keys(node, self.parameters)
        if match is not None and isinstance(node.ctx, ast.Load):
            self.rows.append(
                (
                    match[0],
                    match[1],
                    node.lineno,
                    node.col_offset,
                    "DIRECT_SUBSCRIPT_READ",
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Record a literal `.get()` on an authenticated schema formal parameter."""
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.parameters
            and len(node.args) in {1, 2}
            and not node.keywords
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            self.rows.append(
                (
                    node.func.value.id,
                    node.args[0].value,
                    node.lineno,
                    node.col_offset,
                    "GET_DEFAULT" if len(node.args) == 2 else "GET_NO_DEFAULT",
                )
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Stop descent at nested functions to preserve the immediate lexical owner.

        Args:
            node: Authored ast.FunctionDef under examination; no user code is executed.

        Returns:
            None; updates the collector state or intentionally skips this lexical scope.
        """
        return

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef


def _candidate_reads(
    node: ast.AST, parameters: set[str]
) -> list[tuple[str, str, int, int, str]]:
    """Collect direct literal-key reads reachable in the supplied syntax node only."""
    visitor = _DirectReads(parameters)
    visitor.visit(node)
    return visitor.rows


def _literal_guard(stmt: ast.If, params: set[str]) -> tuple[str, str, str] | None:
    """Return an observed membership or exact-builtin type guard, not MUST proof."""
    member = _membership(stmt.test, params)
    if member is not None:
        return (
            member[0],
            member[1],
            "KEY_PRESENT_BRANCH" if member[2] else "KEY_ABSENT_BRANCH",
        )

    return None


def typed_consumer_facts(
    snapshot: RepositoryAnalysisSnapshot,
    topology: RepositoryTopology,
) -> tuple[TypedConsumerFact, ...]:
    """Bind literal-key reads to authored TypedDict fields without assuming runtime dominance.

    Args:
        snapshot: Immutable parsed source and its paths.
        topology: Existing symbol identities and authenticated schema field facts.

    Returns:
        Source-bound consumer facts including optional syntactic guard observations.
    """
    fields: dict[str, dict[str, TypedSchemaFieldFact]] = {}
    for fact in topology.typed_schema_fields:
        fields.setdefault(fact.schema_symbol_id, {})[fact.field] = fact
    functions = {
        (s.path.as_posix(), s.line): s
        for s in topology.symbols
        if s.language == "python" and s.kind in {"function", "method"} and not s.foreign
    }
    all_schema_ids = set(fields)
    imported_by_module: dict[str, dict[str, str]] = {}
    trees: dict[str, ast.Module] = {}
    for path in snapshot.paths:
        unit = snapshot.units[path]
        if unit.tree is None:
            continue
        trees[path] = unit.tree
        imported_by_module[python_module_name(path)] = _imports(
            path, unit.tree, _scope_binding_names(unit.tree)
        )
    rows: set[TypedConsumerFact] = set()
    for path, tree in trees.items():
        digest = hashlib.sha256(snapshot.units[path].source.encode()).hexdigest()
        module = python_module_name(path)
        schema_for_alias: dict[str, str] = {}
        for alias, target in imported_by_module[module].items():
            resolved = resolve_local_import_symbol(
                target, imported_by_module, all_schema_ids
            )
            if resolved is not None:
                schema_for_alias[alias] = resolved
        for schema_id in fields:
            if schema_id.rpartition(".")[0] == module:
                schema_for_alias[schema_id.rpartition(".")[2]] = schema_id
        declaration_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for stmt in tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                declaration_nodes.append(stmt)
            elif isinstance(stmt, ast.ClassDef):
                declaration_nodes.extend(
                    x
                    for x in stmt.body
                    if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        for declaration in declaration_nodes:
            symbol = functions.get((path, declaration.lineno))
            if symbol is None or declaration.decorator_list:
                continue
            params: dict[str, str] = {}
            args = (
                *declaration.args.posonlyargs,
                *declaration.args.args,
                *declaration.args.kwonlyargs,
            )
            for arg in args:
                if (
                    isinstance(arg.annotation, ast.Name)
                    and arg.annotation.id in schema_for_alias
                ):
                    params[arg.arg] = schema_for_alias[arg.annotation.id]
            if not params:
                continue
            # Any reassignment/escape of a receiver in the lexical body makes
            # annotation-only binding insufficient; remove it instead of guessing.
            modified = set()
            for body_node in declaration.body:
                for node in ast.walk(body_node):
                    if (
                        isinstance(node, ast.Name)
                        and node.id in params
                        and isinstance(node.ctx, (ast.Store, ast.Del))
                    ):
                        modified.add(node.id)
            for name in modified:
                params.pop(name, None)
            if not params:
                continue
            names = set(params)
            validated_reads = presence_witnesses(declaration, names)
            for stmt in declaration.body:
                guard = (
                    _literal_guard(stmt, names) if isinstance(stmt, ast.If) else None
                )
                # Condition is observed; branch execution / guard dominance is
                # deliberately not inferred from textual order.
                parts: list[tuple[ast.AST, str]] = []
                if isinstance(stmt, ast.If):
                    parts.append((stmt.test, "IF_TEST"))
                    parts.extend((child, "IF_BRANCH_OPEN") for child in stmt.body)
                    parts.extend((child, "ELSE_BRANCH_OPEN") for child in stmt.orelse)
                elif isinstance(
                    stmt,
                    (ast.Return, ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign),
                ):
                    parts.append((stmt, "DIRECT_STATEMENT"))
                # No implicit traversal of loops, exception handlers, nested scopes.
                for part, context in parts:
                    for name, key, line, column, operation in _candidate_reads(
                        part, names
                    ):
                        declaration_fact = fields[params[name]].get(key)
                        if declaration_fact is None:
                            continue
                        correlated = (
                            guard is not None
                            and guard[:2] == (name, key)
                            and context == "IF_BRANCH_OPEN"
                            and guard[2] != "KEY_ABSENT_BRANCH"
                        )
                        rows.add(
                            TypedConsumerFact(
                                schema_symbol_id=params[name],
                                consumer_symbol_id=symbol.symbol_id,
                                path=Path(path),
                                line=line,
                                column=column,
                                source_sha256=digest,
                                receiver=name,
                                field=key,
                                required=declaration_fact.required,
                                declared_type=declaration_fact.annotation,
                                observation=operation,
                                guard_observation=guard[2] if correlated else "NONE",
                                guard_line=stmt.lineno if correlated else 0,
                                presence_witness_line=(
                                    validated_reads.get((line, column, name, key), 0)
                                    if operation == "DIRECT_SUBSCRIPT_READ"
                                    else 0
                                ),
                            )
                        )
    return tuple(
        sorted(
            rows,
            key=lambda f: (
                f.path.as_posix(),
                f.line,
                f.column,
                f.schema_symbol_id,
                f.consumer_symbol_id,
            ),
        )
    )


def typed_required_get_findings(
    topology: RepositoryTopology, audited_paths: frozenset[str]
):
    """QG003/QG004 on positively proven TypedDict keys across module imports.

    Consume the already-normalized source graph: unknown schemas, unknown
    selectors and explicitly NotRequired fields cannot become deficiencies.
    """
    from .model import Finding

    findings = []
    for record in topology.typed_consumers:
        if (
            record.path.as_posix() not in audited_paths
            or record.observation not in {"GET_DEFAULT", "GET_NO_DEFAULT"}
            or not record.required
        ):
            continue
        rule = "QG004" if record.observation == "GET_DEFAULT" else "QG003"
        findings.append(
            Finding(
                code=rule,
                severity="warning",
                confidence="high",
                path=record.path.as_posix(),
                line=record.line,
                column=record.column,
                symbol=record.consumer_symbol_id,
                message=f"已认证 TypedDict `{record.schema_symbol_id}` 的必需键 `{record.field}` 使用 .get() 兜底。",
                suggestion="直接读取必需字段；如允许缺失，应修改真实 TypedDict 声明。",
                evidence={
                    "schema_symbol_id": record.schema_symbol_id,
                    "consumer_symbol_id": record.consumer_symbol_id,
                    "requiredness": "PROVEN_REQUIRED",
                    "field": record.field,
                    "topology_resolution": "authenticated-cross-module-schema",
                },
                source="source-bound-r3",
            )
        )
    return findings
