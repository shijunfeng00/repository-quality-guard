"""Conservative, source-pinned Python executor scheduling relationships.

A scheduling edge is possible dispatch, not proof the submitted callable ran.
Only local ``ThreadPoolExecutor`` bindings and uniquely initialized instance
fields are certified. Unknown, rebound and nonstandard executor owners remain
unconnected rather than relying on method-name matching.
"""

from __future__ import annotations

import ast
from pathlib import Path

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .relation_graph import RepositoryRelationGraph
from .topology_facts import UsageEdge, UsageKind


def _executor_aliases(tree: ast.Module) -> set[str]:
    aliases = set()
    for item in tree.body:
        if isinstance(item, ast.ImportFrom) and item.module == "concurrent.futures":
            aliases.update(
                (part.asname or part.name)
                for part in item.names if part.name == "ThreadPoolExecutor"
            )
    return aliases


def _is_executor(value: ast.AST, aliases: set[str]) -> bool:
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in aliases
    )


def _writes(node: ast.AST):
    for item in ast.walk(node):
        if isinstance(item, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = item.targets if isinstance(item, ast.Assign) else (item.target,)
            value = item.value
            for target in targets:
                yield target, value, item.lineno
        elif isinstance(item, ast.AugAssign):
            yield item.target, None, item.lineno


def _field_is_unique_executor(klass: ast.ClassDef, field: str, aliases: set[str]) -> bool:
    writes = [
        (method.name, value)
        for method in klass.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        for target, value, _line in _writes(method)
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        and target.attr == field
    ]
    return len(writes) == 1 and writes[0][0] == "__init__" and _is_executor(writes[0][1], aliases)


def _local_is_executor(method: ast.AST, name: str, call_line: int, aliases: set[str]) -> bool:
    writes = [
        (value, lineno)
        for target, value, lineno in _writes(method)
        if isinstance(target, ast.Name) and target.id == name
    ]
    for item in ast.walk(method):
        if not isinstance(item, (ast.With, ast.AsyncWith)):
            continue
        for binding in item.items:
            if (
                isinstance(binding.optional_vars, ast.Name)
                and binding.optional_vars.id == name
                and _is_executor(binding.context_expr, aliases)
                and item.lineno < call_line <= item.end_lineno
            ):
                return not any(line <= call_line for _, line in writes)
    return (
        len(writes) == 1
        and writes[0][1] < call_line
        and _is_executor(writes[0][0], aliases)
    )


def executor_dispatch_edges(
    graph: RepositoryRelationGraph, edges: set[UsageEdge]
) -> set[UsageEdge]:
    """Link callbacks to a confirmed standard executor's ``submit`` call site."""
    result: set[UsageEdge] = set()
    snapshot = graph.snapshot
    captures = {
        (nested, variable)
        for nested, outer, variable, _line in graph.topology_source.closure_captures
        if outer in graph.nodes and graph.nodes[outer].kind == "method"
    }
    registrations = [e for e in edges if e.kind == UsageKind.CALLBACK_REGISTRATION]
    # Parse each source file once rather than scan all sources for every edge.
    grouped: dict[str, list[UsageEdge]] = {}
    for edge in registrations:
        grouped.setdefault(edge.path.as_posix(), []).append(edge)
    for path, references in grouped.items():
        unit = snapshot.units.get(path)
        if unit is None or not isinstance(unit.tree, ast.Module):
            continue
        aliases = _executor_aliases(unit.tree)
        if not aliases:
            continue
        source_nodes = {
            (node.name, node.lineno): node
            for node in ast.walk(unit.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        class_nodes = {
            item.name: item for item in unit.tree.body
            if isinstance(item, ast.ClassDef)
        }
        for edge in references:
            if edge.source_id == edge.target_id:
                continue
            source = graph.nodes.get(edge.source_id)
            target = graph.nodes.get(edge.target_id)
            if source is None or target is None or source.path != path:
                continue
            method = source_nodes.get((source.name, source.line))
            if method is None:
                continue
            # A nested function may capture the outer method's self; the graph
            # already records this lexical fact. Do not infer it from name alone.
            klass = None
            if target.kind == "method" and target.owner in class_nodes:
                candidate = class_nodes[target.owner]
                class_id = f"{target.module}.{target.owner}" if target.module else target.owner
                if edge.source_id.startswith(class_id + ".") and (
                    source.owner == target.owner
                    or (edge.source_id, "self") in captures
                ):
                    klass = candidate
            for call in ast.walk(method):
                if not isinstance(call, ast.Call) or call.lineno != edge.line:
                    continue
                if not isinstance(call.func, ast.Attribute) or call.func.attr != "submit":
                    continue
                if not call.args or not isinstance(call.args[0], (ast.Name, ast.Attribute)):
                    continue
                callback = call.args[0]
                if isinstance(callback, ast.Name):
                    if callback.id != target.name:
                        continue
                else:
                    if not (
                        isinstance(callback.value, ast.Name)
                        and callback.value.id == "self"
                        and callback.attr == target.name
                        and klass is not None
                        and target.node_id.startswith(
                            (f"{source.module}.{klass.name}" if source.module else klass.name) + "."
                        )
                    ):
                        continue
                receiver = call.func.value
                certified = False
                if isinstance(receiver, ast.Name):
                    certified = _local_is_executor(method, receiver.id, call.lineno, aliases)
                elif (
                    klass is not None
                    and isinstance(receiver, ast.Attribute)
                    and isinstance(receiver.value, ast.Name)
                    and receiver.value.id == "self"
                ):
                    certified = _field_is_unique_executor(klass, receiver.attr, aliases)
                if certified:
                    result.add(UsageEdge(edge.source_id, edge.target_id, UsageKind.MAY_CALLBACK, Path(path), call.lineno, "medium"))
    return result
