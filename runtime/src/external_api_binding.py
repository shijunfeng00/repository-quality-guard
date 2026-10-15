"""Conservative binding of statically imported, documented Python APIs.

The only recognized names are third-party/stdlib *API identities* after an
unambiguous module-scope import, never developer-authored symbol spellings.
Unresolved local aliases or any possible rebinding return UNKNOWN (None).
"""

from __future__ import annotations

import ast
from functools import lru_cache

from .facts import ModuleFacts

# The public synchronous module-level APIs have known timeout/blocking semantics.
# Instance methods need certified receiver types; their names alone are insufficient.
_NETWORK_METHODS = frozenset(
    {"request", "get", "post", "put", "patch", "delete", "head", "options"}
)
NETWORK_APIS = frozenset(
    f"{module}.{method}"
    for module in ("requests", "httpx")
    for method in _NETWORK_METHODS
) | frozenset({"aiohttp.request"})
BLOCKING_APIS = (
    frozenset(
        {
            "subprocess.run",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "subprocess.Popen",
            "time.sleep",
        }
    )
    | frozenset(f"requests.{method}" for method in _NETWORK_METHODS)
    | frozenset(f"httpx.{method}" for method in _NETWORK_METHODS)
)
KNOWN_APIS = NETWORK_APIS | BLOCKING_APIS


def _top_level_bindings(tree: ast.Module) -> dict[str, str]:
    """Collect unique direct imports and reject potentially rebound aliases."""
    imports: dict[str, str] = {}
    duplicates: set[str] = set()
    writes: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            pairs = (
                (alias.asname or alias.name.split(".", 1)[0], alias.name)
                for alias in statement.names
                if alias.asname or "." not in alias.name
            )
        elif isinstance(statement, ast.ImportFrom) and statement.level == 0:
            pairs = (
                (alias.asname or alias.name, f"{statement.module}.{alias.name}")
                for alias in statement.names
                if alias.name != "*" and statement.module
            )
        else:
            pairs = ()
        for alias, target in pairs:
            if alias in imports:
                duplicates.add(alias)
            imports[alias] = target
        # Module-level writes also include names assigned through top-level if/try.
        # A nested function/class has its own lexical scope and is not searched.
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            writes.add(statement.name)
            continue
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        for child in ast.walk(statement):
            if isinstance(child, ast.Name) and isinstance(
                child.ctx, (ast.Store, ast.Del)
            ):
                writes.add(child.id)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                # Conditional or other nested imports may overwrite a binding.
                for alias in child.names:
                    writes.add(alias.asname or alias.name.split(".", 1)[0])
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            global_aliases = {
                name
                for child in ast.walk(node)
                if isinstance(child, ast.Global)
                for name in child.names
            }
            if global_aliases:
                writes.update(
                    child.id
                    for child in ast.walk(node)
                    if isinstance(child, ast.Name)
                    and isinstance(child.ctx, (ast.Store, ast.Del))
                    and child.id in global_aliases
                )
    return {
        alias: target
        for alias, target in imports.items()
        if alias not in duplicates and alias not in writes
    }


@lru_cache(maxsize=128)
def _cached_bindings(tree: ast.Module) -> dict[str, str]:
    """Reuse immutable module declaration bindings associated with the frozen source digest."""
    return _top_level_bindings(tree)


def _local_bindings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Find lexical shadows; no text-based assumptions about their semantics."""
    bindings = {
        arg.arg
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    }
    if node.args.vararg:
        bindings.add(node.args.vararg.arg)
    if node.args.kwarg:
        bindings.add(node.args.kwarg.arg)
    pending: list[ast.AST] = list(node.body)
    while pending:
        child = pending.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.add(child.name)
            continue
        if isinstance(child, ast.Lambda):
            continue
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            for alias in child.names:
                bindings.add(alias.asname or alias.name.split(".", 1)[0])
            continue
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            bindings.add(child.id)
        if isinstance(child, ast.ExceptHandler) and child.name:
            bindings.add(child.name)
        pending.extend(ast.iter_child_nodes(child))
    return bindings


def bound_external_api(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    call: ast.Call,
) -> str | None:
    """Resolve a call to a verified import identity or explicitly return UNKNOWN.

    Args:
        facts: Previously established facts used as the sole evidence input.
        function: Source-bound function declaration, not a dynamically invoked object.
        call: ast.Call supplied to the source-bound bound_external_api analysis.

    Returns:
        Authenticated external callable signatures and their binding evidence, or an unknown result."""
    if facts.tree is None or facts.repository_root is None:
        return None
    expr = call.func
    if isinstance(expr, ast.Name):
        root, suffix = expr.id, ""
    elif isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        root, suffix = expr.value.id, f".{expr.attr}"
    else:
        # A member on a custom object needs compiler-confirmed receiver type.
        return None
    if root in _local_bindings(function):
        return None
    target = _cached_bindings(facts.tree).get(root)
    if target is None:
        return None
    dependency_root = target.split(".", 1)[0]
    # An authored module can shadow a third-party/stdlib import. Without the
    # resolver's dependency identity, that binding is not a certified API.
    repository_root = facts.repository_root
    if (repository_root / f"{dependency_root}.py").exists() or (
        repository_root / dependency_root / "__init__.py"
    ).exists():
        return None
    qualified = f"{target}{suffix}"
    return qualified if qualified in KNOWN_APIS else None
