"""Conservative source-bound argument binding for uniquely declared Python functions.

No project module is imported or executed. Only an explicit module-level
function or an explicit repository-local import is eligible; decorators,
rebindings, unresolved imports, dynamic splats and methods remain open-world.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
from collections import Counter

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .core_rule_contract import CoreQualityRule
from .model import Finding
from .python_import_resolution import python_module_name


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> inspect.Signature:
    """Build the declaration's authored signature without evaluating defaults."""
    args = node.args
    positional = (*args.posonlyargs, *args.args)
    default_start = len(positional) - len(args.defaults)
    params = [
        inspect.Parameter(
            arg.arg,
            (
                inspect.Parameter.POSITIONAL_ONLY
                if index < len(args.posonlyargs)
                else inspect.Parameter.POSITIONAL_OR_KEYWORD
            ),
            default=(object() if index >= default_start else inspect.Parameter.empty),
        )
        for index, arg in enumerate(positional)
    ]
    if args.vararg:
        params.append(
            inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL)
        )
    params.extend(
        inspect.Parameter(
            arg.arg,
            inspect.Parameter.KEYWORD_ONLY,
            default=(object() if default is not None else inspect.Parameter.empty),
        )
        for arg, default in zip(args.kwonlyargs, args.kw_defaults)
    )
    if args.kwarg:
        params.append(inspect.Parameter(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
    return inspect.Signature(params)


def _rebound_names(tree: ast.Module) -> frozenset[str]:
    """Conservatively reject any lexical stores, parameters or nested definitions.

    This may discard valid findings; it cannot make a known local rebinding
    look like a fixed module import or declaration.
    """
    unsafe: set[str] = set()
    for part in ast.walk(tree):
        if isinstance(part, ast.Name) and isinstance(part.ctx, (ast.Store, ast.Del)):
            unsafe.add(part.id)
        elif isinstance(part, ast.arg):
            unsafe.add(part.arg)
        elif isinstance(part, (ast.Import, ast.ImportFrom)):
            if part not in tree.body:
                unsafe.update(
                    alias.asname
                    or (
                        alias.name.split(".")[0]
                        if isinstance(part, ast.Import)
                        else alias.name
                    )
                    for alias in part.names
                )
        elif isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # A nested lexical declaration can shadow an imported binding.
            if part not in tree.body:
                unsafe.add(part.name)
        elif isinstance(part, (ast.Global, ast.Nonlocal)):
            unsafe.update(part.names)
    # Dynamic global mutation cannot establish static target identity.
    if any(
        isinstance(part, ast.Call)
        and isinstance(part.func, ast.Name)
        and part.func.id in {"exec", "eval", "globals", "locals"}
        for part in ast.walk(tree)
    ):
        return frozenset((*unsafe, "*"))
    return frozenset(unsafe)


class SourceBoundCallRule(CoreQualityRule[RepositoryAnalysisSnapshot]):
    """Recognize QG209 source-certified invalid direct argument bindings.

    Only uniquely resolved module-level functions and statically bound calls
    qualify; ambiguous imports, shadowing and dynamic invocation remain open.
    """

    code = "QG209"

    def evaluate(self, snapshot: RepositoryAnalysisSnapshot) -> list[Finding]:
        """Bind unambiguous local signatures; report only concrete argument conflicts.

        The result is a review-level source witness, not proof of runtime closure.

        Args:
            snapshot: Frozen parsed source and module units used to reconstruct lexical obligations.

        Returns:
            Source-certified invalid direct calls; dynamic dispatch remains open-world."""
        declarations: dict[str, tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        counts: Counter[str] = Counter()
        for path in snapshot.paths:
            tree = snapshot.units[path].tree
            if tree is None:
                continue
            module = python_module_name(path)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = f"{module}.{node.name}".lstrip(".")
                    counts[name] += 1
                    if not node.decorator_list:
                        declarations[name] = (path, node)

        # Eliminate module-name collisions and decorated/overloaded declarations.
        module_counts = Counter(python_module_name(path) for path in snapshot.paths)
        declarations = {
            symbol: value
            for symbol, value in declarations.items()
            if counts[symbol] == 1 and module_counts[python_module_name(value[0])] == 1
        }
        results: list[Finding] = []
        for path in snapshot.paths:
            unit = snapshot.units[path]
            tree = unit.tree
            if tree is None:
                continue
            unsafe = _rebound_names(tree)
            if "*" in unsafe or any(
                isinstance(part, ast.ImportFrom)
                and any(alias.name == "*" for alias in part.names)
                for part in ast.walk(tree)
            ):
                continue
            module = python_module_name(path)
            imports: dict[str, set[str]] = {}
            module_aliases: dict[str, set[str]] = {}
            for statement in tree.body:
                if isinstance(statement, ast.ImportFrom) and statement.level == 0:
                    for alias in statement.names:
                        if alias.name == "*":
                            continue
                        binding = alias.asname or alias.name
                        imports.setdefault(binding, set()).add(
                            f"{statement.module}.{alias.name}"
                        )
                elif isinstance(statement, ast.Import):
                    for alias in statement.names:
                        if alias.asname:
                            binding = alias.asname
                            target = alias.name
                        else:
                            binding = alias.name.split(".")[0]
                            target = binding
                        module_aliases.setdefault(binding, set()).add(target)
            import_counts: Counter[str] = Counter()
            for statement in tree.body:
                if isinstance(statement, (ast.ImportFrom, ast.Import)):
                    import_counts.update(
                        alias.asname
                        or (
                            alias.name.split(".")[0]
                            if isinstance(statement, ast.Import)
                            else alias.name
                        )
                        for alias in statement.names
                    )
            for call in ast.walk(tree):
                if not isinstance(call, ast.Call):
                    continue
                if any(isinstance(arg, ast.Starred) for arg in call.args) or any(
                    kw.arg is None for kw in call.keywords
                ):
                    continue
                target: str | None = None
                binding: str
                if isinstance(call.func, ast.Name):
                    binding = call.func.id
                    if binding in unsafe or import_counts[binding] > 1:
                        continue
                    local = f"{module}.{binding}".lstrip(".")
                    if local in declarations and import_counts[binding]:
                        # The module also imports this name: runtime order is
                        # not a uniquely authored declaration binding.
                        continue
                    candidates = set(imports.get(binding, ()))
                    if local in declarations:
                        candidates.add(local)
                    if len(candidates) == 1:
                        target = next(iter(candidates))
                elif isinstance(call.func, ast.Attribute) and isinstance(
                    call.func.value, ast.Name
                ):
                    binding = call.func.value.id
                    if (
                        binding in unsafe
                        or import_counts[binding] != 1
                        or len(module_aliases.get(binding, ())) != 1
                    ):
                        continue
                    imported_module = next(iter(module_aliases[binding]))
                    target = f"{imported_module}.{call.func.attr}"
                if target not in declarations:
                    continue
                declaration_path, declaration = declarations[target]
                # A second assignment or module import of the same global binding
                # makes runtime target selection ambiguous even if syntax matches.
                if (
                    isinstance(call.func, ast.Name)
                    and len(imports.get(call.func.id, ())) > 1
                ):
                    continue
                try:
                    _signature(declaration).bind(
                        *[object() for _ in call.args],
                        **{keyword.arg: object() for keyword in call.keywords},
                    )
                except TypeError as error:
                    results.append(
                        Finding(
                            code="QG209",
                            severity="warning",
                            confidence="medium",
                            path=path,
                            line=call.lineno,
                            column=call.col_offset + 1,
                            symbol=target,
                            message="仓库内唯一源码函数声明与调用实参不匹配；运行时重绑定仍开放。",
                            suggestion="核对真实入口与调用契约；本规则不证明动态派发封闭。",
                            evidence={
                                "kind": "R1_UNIQUE_SOURCE_CALL",
                                "capability": "SOURCE_UNIQUE_MODULE_DECLARATION_OPEN_RUNTIME",
                                "dispatch": "OPEN_WORLD",
                                "caller_sha256": hashlib.sha256(
                                    unit.source.encode()
                                ).hexdigest(),
                                "callee_sha256": hashlib.sha256(
                                    snapshot.units[declaration_path].source.encode()
                                ).hexdigest(),
                                "callee_path": declaration_path,
                                "signature_conflict": str(error),
                            },
                            source="source-bound-r1",
                        )
                    )
        return list(sorted(results, key=lambda row: (row.path, row.line, row.column)))


def source_bound_call_findings(
    snapshot: RepositoryAnalysisSnapshot,
) -> tuple[Finding, ...]:
    """Check source-bound calls against uniquely resolved function signatures.

    Args:
        snapshot: Canonical parsed module source and import graph input;
            no second source scan or runtime invocation is performed.

    Returns:
        Stable ordered QG209 conditional Findings for direct calls with
        deterministically incompatible positional or keyword arguments.
    """
    return tuple(SourceBoundCallRule().evaluate(snapshot))
