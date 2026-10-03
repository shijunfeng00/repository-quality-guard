"""仓库关系图：从统一 RepositoryAnalysisSnapshot 派生符号、调用与影响关系。"""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .ast_utils import decorator_names
from .docstrings import definition_code_lines, function_parameter_names
from .config import is_test_path
from .model import Finding, InterfaceChange, InterfaceDiffReport
from .python_dependency_facts import StaticPythonDependencyResolver
from .python_import_resolution import resolve_local_import_symbol
from .topology_facts import (
    OwnerFact,
    OwnerKind,
    RepositoryTopology,
    SymbolFact,
    UsageEdge,
    UsageKind,
    Visibility,
)

_MAX_PATH_DEPTH = 24
_MAX_RENDERED_ITEMS = 12


def _module_name(path: str) -> str:
    """把仓库相对 Python 路径转换为 import module 名。"""
    module = path[:-3].replace("/", ".")
    if module == "__init__":
        return ""
    return module[: -len(".__init__")] if module.endswith(".__init__") else module


def _is_test_source(path: str) -> bool:
    """判断路径是否是真正的测试源码，而不是 profile 的其他非阻断域。"""
    return is_test_path(path)


def _class_digest(node: ast.ClassDef) -> str:
    """只对类声明头和非方法直接语句取摘要，避免重复序列化全部方法体。"""
    header = (
        node.name,
        tuple(ast.dump(base, include_attributes=False) for base in node.bases),
        tuple(ast.dump(item, include_attributes=False) for item in node.decorator_list),
        tuple(
            ast.dump(item, include_attributes=False)
            for item in node.body
            if not isinstance(
                item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
        ),
    )
    return hashlib.sha256(repr(header).encode("utf-8")).hexdigest()[:20]


def _dotted(node: ast.AST | None) -> str:
    """把简单 Name/Attribute 表达式转换为点式名称。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _resolve_relative_module(path: str, module: str, level: int) -> str:
    """按当前文件路径解析 Python 相对导入模块。"""
    if level == 0:
        return module
    current = _module_name(path)
    package = (
        current
        if path.endswith("/__init__.py") or path == "__init__.py"
        else current.rpartition(".")[0]
    )
    parts = [part for part in package.split(".") if part]
    remove = level - 1
    if remove > len(parts):
        return ""
    base = parts[: len(parts) - remove]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


class _ScopeNameCollector(ast.NodeVisitor):
    """Collect immediate function-scope bindings and loads without descending scopes."""

    def __init__(self) -> None:
        """Initialize one immediate lexical-scope accumulator.

        Returns:
            None.
        """
        self.bound: set[str] = set()
        self.loaded: set[str] = set()
        self.loaded_lines: dict[str, int] = {}
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        """Record one loaded or bound name in the current lexical scope.

        Args:
            node: Name expression owned by the current lexical scope.

        Returns:
            None.
        """
        if isinstance(node.ctx, ast.Load):
            self.loaded.add(node.id)
            self.loaded_lines[node.id] = node.lineno
        else:
            self.bound.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Treat a nested synchronous function name as a local binding.

        Args:
            node: Nested synchronous function declaration.

        Returns:
            None.
        """
        self.bound.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Treat a nested asynchronous function name as a local binding.

        Args:
            node: Nested asynchronous function declaration.

        Returns:
            None.
        """
        self.bound.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Treat a nested class name as a local binding without descending into it.

        Args:
            node: Nested class declaration.

        Returns:
            None.
        """
        self.bound.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Stop at lambda boundaries because they own a separate lexical scope.

        Args:
            node: Lambda expression that starts a nested lexical scope.

        Returns:
            None.
        """
        return

    def visit_Import(self, node: ast.Import) -> None:
        """Record names introduced by a normal import statement.

        Args:
            node: Import statement owned by the current lexical scope.

        Returns:
            None.
        """
        for alias in node.names:
            self.bound.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record explicit names introduced by a from-import statement.

        Args:
            node: From-import statement owned by the current lexical scope.

        Returns:
            None.
        """
        for alias in node.names:
            if alias.name != "*":
                self.bound.add(alias.asname or alias.name)

    def visit_Global(self, node: ast.Global) -> None:
        """Mark names explicitly bound outside the current function scope.

        Args:
            node: Global declaration in the current lexical scope.

        Returns:
            None.
        """
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        """Mark names explicitly captured from an enclosing function scope.

        Args:
            node: Nonlocal declaration in the current lexical scope.

        Returns:
            None.
        """
        self.nonlocal_names.update(node.names)
        for name in node.names:
            self.loaded_lines[name] = node.lineno

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Record exception aliases while continuing through the handler body.

        Args:
            node: Exception handler owned by the current lexical scope.

        Returns:
            None.
        """
        if node.name:
            self.bound.add(node.name)
        for statement in node.body:
            self.visit(statement)


def _function_scope_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], set[str], dict[str, int]]:
    """Return immediate lexical bindings and loads for one authored function scope."""
    collector = _ScopeNameCollector()
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    collector.bound.update(argument.arg for argument in arguments)
    if node.args.vararg is not None:
        collector.bound.add(node.args.vararg.arg)
    if node.args.kwarg is not None:
        collector.bound.add(node.args.kwarg.arg)
    for statement in node.body:
        collector.visit(statement)
    collector.bound.difference_update(collector.global_names | collector.nonlocal_names)
    collector.loaded.update(collector.nonlocal_names)
    return collector.bound, collector.loaded, collector.loaded_lines


@dataclass(slots=True, frozen=True)
class _RelationNode:
    """保存一个可参与仓库关系分析的模块、类、函数、方法或测试节点。"""

    node_id: str
    kind: str
    path: str
    module: str
    qualname: str
    name: str
    line: int
    end_line: int
    lines: int
    parameter_count: int
    decorators: tuple[str, ...]
    bases: tuple[str, ...]
    owner: str
    private: bool
    nested: bool
    test: bool
    fingerprint: str


@dataclass(slots=True, frozen=True)
class _RelationEdge:
    """保存两个仓库节点之间的一条确定静态关系。"""

    source: str
    target: str
    kind: str


@dataclass(slots=True, frozen=True)
class _ResolvedReference:
    """Store one repository-local callable consumption resolved by the graph owner."""

    source: str
    target: str
    line: int
    kind: UsageKind


@dataclass(slots=True, frozen=True)
class _ResolvedFieldAccess:
    """Store one authored instance-field access after method-name disambiguation."""

    source: str
    class_id: str
    field_name: str
    line: int


@dataclass(slots=True, frozen=True)
class _ExternalBase:
    """Store one unresolved project inheritance target as a qualified dependency name."""

    class_id: str
    qualified_name: str


@dataclass(slots=True, frozen=True)
class _TopologyProjectionSource:
    """Immutable relation facts consumed by the language-neutral topology projector."""

    module_nodes: tuple[tuple[str, str], ...]
    explicit_exports: tuple[tuple[str, tuple[str, ...]], ...]
    references: tuple[_ResolvedReference, ...]
    field_accesses: tuple[_ResolvedFieldAccess, ...]
    closure_captures: tuple[tuple[str, str, str, int], ...]
    external_bases: tuple[_ExternalBase, ...]


@dataclass(slots=True, frozen=True)
class _InterfaceImpact:
    """保存一个接口变化对应的静态调用方、传递影响和受影响测试。"""

    change: str
    kind: str
    path: str
    symbol: str
    node_id: str
    direct_callers: tuple[str, ...]
    transitive_dependents: tuple[str, ...]
    affected_tests: tuple[str, ...]
    affected_test_files: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """把接口影响转换为紧凑可序列化事实。

        Returns:
            包含静态 caller、传递依赖和受影响测试数量及样例的映射。
        """
        return {
            "change": self.change,
            "kind": self.kind,
            "path": self.path,
            "symbol": self.symbol,
            "node_id": self.node_id,
            "direct_callers": list(self.direct_callers[:_MAX_RENDERED_ITEMS]),
            "direct_caller_count": len(self.direct_callers),
            "transitive_dependents": list(
                self.transitive_dependents[:_MAX_RENDERED_ITEMS]
            ),
            "transitive_dependent_count": len(self.transitive_dependents),
            "affected_tests": list(self.affected_tests[:_MAX_RENDERED_ITEMS]),
            "affected_test_count": len(self.affected_tests),
            "affected_test_files": list(self.affected_test_files[:_MAX_RENDERED_ITEMS]),
            "affected_test_file_count": len(self.affected_test_files),
            "resolution": "static",
        }


class _Collector(ast.NodeVisitor):
    """从已解析 AST 收集定义、导入和带 caller 上下文的调用表达式。"""

    def __init__(self, path: str, source: str) -> None:
        """初始化单文件 AST 关系收集器。

        Args:
            path: 仓库相对 Python 文件路径。
            source: Snapshot 已读取的源码，仅用于稳定摘要。

        Returns:
            None.
        """
        self.path = path
        self.module = _module_name(path)
        self.source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.scope_stack: list[str] = []
        self.function_scopes: list[tuple[str, set[str]]] = []
        self.nodes: list[_RelationNode] = []
        self.imports: dict[str, str] = {}
        self.calls: list[tuple[str, ast.expr]] = []
        self.references: list[tuple[str, ast.expr, UsageKind]] = []
        self.reference_context: list[UsageKind] = []
        self.field_accesses: list[tuple[str, str, str, int]] = []
        self.closure_captures: list[tuple[str, str, str, int]] = []
        self.classes: dict[str, tuple[str, ...]] = {}
        self.explicit_exports: set[str] = set()

    @property
    def caller(self) -> str:
        """返回当前定义的稳定 node id；模块级表达式归到 module 节点。"""
        qualname = ".".join(self.scope_stack)
        return (
            f"{self.module}.{qualname}"
            if self.module and qualname
            else qualname or f"module:{self.path}"
        )

    def collect_module(self, tree: ast.Module) -> None:
        """记录模块节点并遍历 Snapshot 已解析 AST。

        Args:
            tree: Snapshot 中当前文件的已解析模块 AST。

        Returns:
            None.
        """
        self.nodes.append(
            _RelationNode(
                node_id=f"module:{self.path}",
                kind="module",
                path=self.path,
                module=self.module,
                qualname=self.module,
                name=PurePosixPath(self.path).name,
                line=1,
                end_line=max(
                    (item.end_lineno or 1 for item in tree.body),
                    default=1,
                ),
                lines=max(
                    (item.end_lineno or 1 for item in tree.body),
                    default=1,
                ),
                parameter_count=0,
                decorators=(),
                bases=(),
                owner="",
                private=False,
                nested=False,
                test=_is_test_source(self.path),
                fingerprint=self.source_digest,
            )
        )
        self.visit(tree)

    def visit_Import(self, node: ast.Import) -> None:
        """记录普通 import 的本地绑定。

        Args:
            node: 当前 import AST 节点。

        Returns:
            None.
        """
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            self.imports[local] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """记录绝对或相对 from-import 的本地绑定。

        Args:
            node: 当前 from-import AST 节点。

        Returns:
            None.
        """
        module = _resolve_relative_module(self.path, node.module or "", node.level)
        if not module:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            local = alias.asname or alias.name
            self.imports[local] = f"{module}.{alias.name}"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """记录类节点、父类声明并继续遍历类体。

        Args:
            node: 当前类定义 AST 节点。

        Returns:
            None.
        """
        qualname = ".".join([*self.scope_stack, node.name])
        node_id = f"{self.module}.{qualname}" if self.module else qualname
        owner = ".".join(self.scope_stack)
        bases = tuple(filter(None, (_dotted(base) for base in node.bases)))
        self.nodes.append(
            _RelationNode(
                node_id=node_id,
                kind="class",
                path=self.path,
                module=self.module,
                qualname=qualname,
                name=node.name,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                lines=(node.end_lineno or node.lineno) - node.lineno + 1,
                parameter_count=0,
                decorators=decorator_names(node.decorator_list),
                bases=bases,
                owner=owner,
                private=node.name.startswith("_")
                and not (node.name.startswith("__") and node.name.endswith("__")),
                nested=bool(self.function_stack),
                test=_is_test_source(self.path),
                fingerprint=_class_digest(node),
            )
        )
        self.classes[node_id] = bases
        self.class_stack.append(qualname)
        self.scope_stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.scope_stack.pop()
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """记录同步函数定义。

        Args:
            node: 当前同步函数 AST 节点。

        Returns:
            None.
        """
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """记录异步函数定义。

        Args:
            node: 当前异步函数 AST 节点。

        Returns:
            None.
        """
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """建立函数/方法/测试节点并在 caller 栈下遍历函数体。"""
        qualname = ".".join([*self.scope_stack, node.name])
        node_id = f"{self.module}.{qualname}" if self.module else qualname
        owner = ".".join(self.scope_stack)
        kind = (
            "method"
            if self.class_stack
            else "test"
            if _is_test_source(self.path) and node.name.startswith("test")
            else "function"
        )
        bound_names, loaded_names, loaded_lines = _function_scope_names(node)
        if self.function_scopes:
            free_names = loaded_names - bound_names
            for name in sorted(free_names):
                for outer_id, outer_bound in reversed(self.function_scopes):
                    if name not in outer_bound:
                        continue
                    self.closure_captures.append(
                        (node_id, outer_id, name, loaded_lines[name])
                    )
                    break
        self.nodes.append(
            _RelationNode(
                node_id=node_id,
                kind=kind,
                path=self.path,
                module=self.module,
                qualname=qualname,
                name=node.name,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                lines=definition_code_lines(node),
                parameter_count=len(function_parameter_names(node)),
                decorators=decorator_names(node.decorator_list),
                bases=(),
                owner=owner,
                private=node.name.startswith("_")
                and not (node.name.startswith("__") and node.name.endswith("__")),
                nested=bool(self.function_stack),
                test=_is_test_source(self.path),
                fingerprint=hashlib.sha256(
                    ast.dump(
                        node, annotate_fields=True, include_attributes=False
                    ).encode("utf-8")
                ).hexdigest()[:20],
            )
        )
        self.function_stack.append(node.name)
        self.scope_stack.append(node.name)
        self.function_scopes.append((node_id, bound_names))
        for child in node.body:
            self.visit(child)
        self.function_scopes.pop()
        self.scope_stack.pop()
        self.function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        """记录调用表达式与其当前 caller。

        Args:
            node: 当前调用 AST 节点。

        Returns:
            None.
        """
        self.calls.append((self.caller, node.func))
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            self.reference_context.append(UsageKind.CALLBACK_REGISTRATION)
            self.visit(argument)
            self.reference_context.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        """记录静态 ``__all__`` 导出并继续遍历赋值值。

        Args:
            node: 当前赋值 AST 节点。

        Returns:
            None.
        """
        export_assignment = any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        )
        if export_assignment and isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
            for item in node.value.elts:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    self.explicit_exports.add(item.value)
        for target in node.targets:
            self.visit(target)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """记录带类型注解赋值中的状态写入与引用。

        Args:
            node: 当前注解赋值 AST 节点。

        Returns:
            None.
        """
        self.visit(node.target)
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)

    def visit_Name(self, node: ast.Name) -> None:
        """记录非调用位置的名称引用候选。

        Args:
            node: 当前名称 AST 节点。

        Returns:
            None.
        """
        if isinstance(node.ctx, ast.Load):
            kind = (
                self.reference_context[-1]
                if self.reference_context
                else UsageKind.CALLABLE_REFERENCE
            )
            self.references.append((self.caller, node, kind))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """记录属性引用与 ``self/cls`` 状态访问候选。

        Args:
            node: 当前属性访问 AST 节点。

        Returns:
            None.
        """
        if isinstance(node.ctx, ast.Load):
            kind = (
                self.reference_context[-1]
                if self.reference_context
                else UsageKind.CALLABLE_REFERENCE
            )
            self.references.append((self.caller, node, kind))
        dotted = _dotted(node)
        parts = dotted.split(".") if dotted else []
        if (
            len(parts) == 2
            and parts[0] in {"self", "cls"}
            and self.class_stack
            and self.function_stack
        ):
            class_qualname = self.class_stack[-1]
            class_id = (
                f"{self.module}.{class_qualname}"
                if self.module and class_qualname
                else class_qualname
            )
            self.field_accesses.append((self.caller, class_id, parts[1], node.lineno))
        self.visit(node.value)


class RepositoryRelationGraph:
    """从统一 AST Snapshot 派生只读仓库关系图。

    图只消费 ``RepositoryAnalysisSnapshot`` 已解析 AST，不读取源码文件、不建立第二 parser、
    数据库或 watcher；节点和边仅用于静态关系事实与影响范围计算。
    """

    def __init__(self, snapshot: RepositoryAnalysisSnapshot) -> None:
        """从一个统一仓库快照建立内存关系图。

        Args:
            snapshot: 已由 Quality Guard 统一解析得到的仓库分析快照。

        Returns:
            None.
        """
        self.snapshot = snapshot
        self.nodes: dict[str, _RelationNode] = {}
        self.edges: set[_RelationEdge] = set()
        self._out: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self._in: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self._imports: dict[str, dict[str, str]] = {}
        self._class_bases: dict[str, tuple[str, ...]] = {}
        self._pending_calls: list[tuple[str, str, ast.expr]] = []
        self._pending_references: list[tuple[str, str, ast.expr, UsageKind]] = []
        self._pending_field_accesses: list[tuple[str, str, str, int]] = []
        self._pending_closure_captures: list[tuple[str, str, str, int]] = []
        self._explicit_exports: dict[str, set[str]] = {}
        self._resolved_references: list[_ResolvedReference] = []
        self._resolved_field_accesses: list[_ResolvedFieldAccess] = []
        self._external_bases: list[_ExternalBase] = []
        self._module_nodes = {
            _module_name(path): f"module:{path}" for path in self.snapshot.paths
        }
        self._module_test_nodes: dict[str, set[str]] = defaultdict(set)
        self._collect_snapshot()
        self._index_definitions()
        self._link_imports()
        resolved_bases = self._link_inheritance()
        self._link_calls()
        self._link_overrides(resolved_bases)
        self.topology_source = _TopologyProjectionSource(
            module_nodes=tuple(sorted(self._module_nodes.items())),
            explicit_exports=tuple(
                (module, tuple(sorted(names)))
                for module, names in sorted(self._explicit_exports.items())
            ),
            references=tuple(self._resolved_references),
            field_accesses=tuple(self._resolved_field_accesses),
            closure_captures=tuple(self._pending_closure_captures),
            external_bases=tuple(self._external_bases),
        )

    def _add_edge(self, source: str, target: str, kind: str) -> None:
        """加入一条两端均已解析的去重关系边。"""
        if source == target or source not in self.nodes or target not in self.nodes:
            return
        edge = _RelationEdge(source, target, kind)
        if edge in self.edges:
            return
        self.edges.add(edge)
        self._out[kind][source].add(target)
        self._in[kind][target].add(source)

    def _collect_snapshot(self) -> None:
        """从已有 AST 收集节点、import、继承声明和待解析调用。"""
        for path in self.snapshot.paths:
            unit = self.snapshot.units[path]
            if unit.tree is None:
                continue
            collector = _Collector(path, unit.source)
            collector.collect_module(unit.tree)
            self._imports[collector.module] = collector.imports
            self._explicit_exports[collector.module] = collector.explicit_exports
            self._class_bases.update(collector.classes)
            for node in collector.nodes:
                self.nodes[node.node_id] = node
            self._pending_calls.extend(
                (collector.module, caller, expr) for caller, expr in collector.calls
            )
            self._pending_references.extend(
                (collector.module, caller, expr, kind)
                for caller, expr, kind in collector.references
            )
            self._pending_field_accesses.extend(collector.field_accesses)
            self._pending_closure_captures.extend(collector.closure_captures)

    def _index_definitions(self) -> None:
        """建立测试节点以及 owner→definition 关系。"""
        for node_id, node in self.nodes.items():
            if node.kind == "module":
                continue
            if node.test and node.kind == "test":
                self._module_test_nodes[node.module].add(node_id)
            owner_id = (
                f"{node.module}.{node.owner}"
                if node.module and node.owner
                else node.owner or f"module:{node.path}"
            )
            if owner_id in self.nodes:
                self._add_edge(owner_id, node_id, "DEFINES")

    def _link_imports(self) -> None:
        """把模块 import 声明解析为仓库内部模块关系。"""
        for module, imports in self._imports.items():
            if module not in self._module_nodes:
                continue
            source = self._module_nodes[module]
            for imported in imports.values():
                target_module = imported
                while target_module and target_module not in self._module_nodes:
                    target_module = target_module.rpartition(".")[0]
                if target_module:
                    self._add_edge(source, self._module_nodes[target_module], "IMPORTS")

    def _link_inheritance(self) -> dict[str, tuple[str, ...]]:
        """解析内部继承，并记录无法解析到项目节点的外部父类名称。"""
        resolved_bases: dict[str, tuple[str, ...]] = {}
        for class_id, bases in self._class_bases.items():
            node = self.nodes[class_id]
            targets: list[str] = []
            for base in bases:
                target = self._resolve_name(node.module, class_id, base)
                if target is not None:
                    if self.nodes[target].kind == "class":
                        targets.append(target)
                    continue
                root, dot, suffix = base.partition(".")
                imports = self._imports[node.module]
                qualified = (
                    imports[root] + (f".{suffix}" if dot else "")
                    if root in imports
                    else base
                )
                if qualified:
                    self._external_bases.append(_ExternalBase(class_id, qualified))
            resolved_bases[class_id] = tuple(targets)
            for target in targets:
                self._add_edge(class_id, target, "INHERITS")
        return resolved_bases

    def _link_calls(self) -> None:
        """解析普通调用，并同时归并 callable-reference 与 field-access 补充事实。"""
        for module, caller, expr in self._pending_calls:
            target = self._resolve_expr(module, caller, expr)
            if target is None:
                continue
            target_node = self.nodes[target]
            constructor = f"{target}.__init__"
            if target_node.kind == "class" and constructor in self.nodes:
                target = constructor
            self._add_edge(caller, target, "CALLS")
            self._resolved_references.append(
                _ResolvedReference(caller, target, expr.lineno, UsageKind.DIRECT_CALL)
            )

        for module, caller, expr, kind in self._pending_references:
            target = self._resolve_expr(module, caller, expr)
            if target is None or target == caller:
                continue
            if self.nodes[target].kind not in {"class", "function", "method", "test"}:
                continue
            self._resolved_references.append(
                _ResolvedReference(caller, target, expr.lineno, kind)
            )

        for caller, class_id, field_name, line in self._pending_field_accesses:
            if self._resolve_class_method(class_id, field_name) is not None:
                continue
            self._resolved_field_accesses.append(
                _ResolvedFieldAccess(caller, class_id, field_name, line)
            )

    def _link_overrides(self, resolved_bases: dict[str, tuple[str, ...]]) -> None:
        """根据已解析继承关系建立方法 override 边。"""
        for node_id, node in tuple(self.nodes.items()):
            if node.kind != "method" or not node.owner:
                continue
            class_id = f"{node.module}.{node.owner}" if node.module else node.owner
            if class_id not in resolved_bases:
                continue
            for base_id in resolved_bases[class_id]:
                target = f"{base_id}.{node.name}"
                if target in self.nodes:
                    self._add_edge(node_id, target, "OVERRIDES")

    def _resolve_name(self, module: str, caller: str, name: str) -> str | None:
        """按显式 import、词法作用域和模块作用域解析静态名称。"""
        if name in {"self", "cls", "super"}:
            return None
        imports = self._imports[module] if module in self._imports else {}
        root, dot, suffix = name.partition(".")
        if root in imports:
            candidate = imports[root] + (f".{suffix}" if dot else "")
            resolved = resolve_local_import_symbol(candidate, self._imports, self.nodes)
            if resolved is not None:
                return resolved
        caller_node = self.nodes[caller] if caller in self.nodes else None
        if caller_node is not None and not dot:
            qualname = caller_node.qualname
            while qualname:
                candidate = (
                    f"{module}.{qualname}.{name}" if module else f"{qualname}.{name}"
                )
                if candidate in self.nodes:
                    return candidate
                parent = qualname.rpartition(".")[0]
                if not parent:
                    break
                parent_id = f"{module}.{parent}" if module else parent
                if parent_id in self.nodes and self.nodes[parent_id].kind == "class":
                    break
                qualname = parent
        local = f"{module}.{name}" if module else name
        if local in self.nodes:
            return local
        return None

    def _resolve_class_method(self, class_id: str, method_name: str) -> str | None:
        """沿确定继承边查找类自身或父类方法。"""
        queue: deque[str] = deque([class_id])
        seen = {class_id}
        while queue:
            current = queue.popleft()
            candidate = f"{current}.{method_name}"
            if candidate in self.nodes:
                return candidate
            for base in sorted(self._out["INHERITS"][current]):
                if base not in seen:
                    seen.add(base)
                    queue.append(base)
        return None

    def _resolve_owned_attribute(
        self,
        module: str,
        caller_node: _RelationNode | None,
        parts: list[str],
    ) -> str | None:
        """解析 self/cls/super 属性调用到当前类或确定父类成员。"""
        if caller_node is None or not caller_node.owner:
            return None
        class_id = f"{module}.{caller_node.owner}" if module else caller_node.owner
        if parts[0] in {"self", "cls"}:
            if len(parts) == 2:
                return self._resolve_class_method(class_id, parts[1])
            candidate = f"{class_id}." + ".".join(parts[1:])
            return candidate if candidate in self.nodes else None
        if parts[0] != "super":
            return None
        for base in self._out["INHERITS"][class_id]:
            candidate = f"{base}.{parts[-1]}"
            if candidate in self.nodes:
                return candidate
        return None

    def _resolve_external_attribute(
        self,
        module: str,
        parts: list[str],
    ) -> str | None:
        """仅解析 import 根明确绑定的属性调用；未知对象属性保持未知。"""
        imports = self._imports[module] if module in self._imports else {}
        if parts[0] not in imports:
            return None
        candidate = ".".join([imports[parts[0]], *parts[1:]])
        return resolve_local_import_symbol(candidate, self._imports, self.nodes)

    def _resolve_expr(self, module: str, caller: str, expr: ast.expr) -> str | None:
        """解析调用表达式到唯一静态目标；动态目标保持未知。"""
        if isinstance(expr, ast.Name):
            return self._resolve_name(module, caller, expr.id)
        if not isinstance(expr, ast.Attribute):
            return None
        dotted = _dotted(expr)
        if not dotted:
            return None
        parts = dotted.split(".")
        caller_node = self.nodes[caller] if caller in self.nodes else None
        owned = self._resolve_owned_attribute(module, caller_node, parts)
        return (
            owned
            if owned is not None
            else self._resolve_external_attribute(module, parts)
        )

    def outgoing(self, node_id: str, kind: str = "CALLS") -> tuple[str, ...]:
        """返回指定关系的稳定排序出边目标。

        Args:
            node_id: 起点节点标识。
            kind: 关系类型，默认 ``CALLS``。

        Returns:
            目标节点标识的稳定排序元组。
        """
        return tuple(sorted(self._out[kind][node_id]))

    def incoming(self, node_id: str, kind: str = "CALLS") -> tuple[str, ...]:
        """返回指定关系的稳定排序入边来源。

        Args:
            node_id: 终点节点标识。
            kind: 关系类型，默认 ``CALLS``。

        Returns:
            来源节点标识的稳定排序元组。
        """
        return tuple(sorted(self._in[kind][node_id]))

    def shortest_path(
        self, source: str, target: str, kind: str = "CALLS"
    ) -> tuple[str, ...]:
        """计算指定单一关系下的最短静态路径。

        Args:
            source: 起点节点标识。
            target: 终点节点标识。
            kind: 关系类型，默认 ``CALLS``。

        Returns:
            含首尾节点的最短路径；不可达时返回空元组。
        """
        if source == target and source in self.nodes:
            return (source,)
        queue: deque[tuple[str, tuple[str, ...]]] = deque([(source, (source,))])
        seen = {source}
        while queue:
            current, path = queue.popleft()
            if len(path) > _MAX_PATH_DEPTH:
                continue
            for target_id in sorted(self._out[kind][current]):
                if target_id == target:
                    return (*path, target_id)
                if target_id not in seen:
                    seen.add(target_id)
                    queue.append((target_id, (*path, target_id)))
        return ()

    def reverse_impact(
        self, node_id: str, kinds: Iterable[str] = ("CALLS",)
    ) -> tuple[str, ...]:
        """计算经确定关系反向依赖指定节点的传递影响集合。

        Args:
            node_id: 被依赖节点标识。
            kinds: 参与反向遍历的关系类型。

        Returns:
            传递依赖节点的稳定排序元组，不包含输入节点本身。
        """
        requested = tuple(kinds)
        queue: deque[str] = deque([node_id])
        seen = {node_id}
        result: set[str] = set()
        while queue:
            current = queue.popleft()
            for kind in requested:
                for source in self._in[kind][current]:
                    if source in seen:
                        continue
                    seen.add(source)
                    result.add(source)
                    queue.append(source)
        return tuple(sorted(result))

    def affected_tests(self, node_id: str) -> tuple[str, ...]:
        """计算应优先运行的保守测试集合。

        Args:
            node_id: 被修改或移除的生产节点标识。

        Returns:
            经静态调用或反向模块 import 关系可达的测试用例节点。该集合只表示 RUN set，
            不表示测试必须被修改。
        """
        affected = {
            node
            for node in self.reverse_impact(node_id)
            if node in self.nodes
            and self.nodes[node].test
            and self.nodes[node].kind == "test"
        }
        if node_id not in self.nodes:
            return tuple(sorted(affected))
        source_node = self.nodes[node_id]
        if source_node.module not in self._module_nodes:
            return tuple(sorted(affected))
        queue: deque[str] = deque([self._module_nodes[source_node.module]])
        seen_modules = set(queue)
        while queue:
            current = queue.popleft()
            for importer in sorted(self._in["IMPORTS"][current]):
                if importer in seen_modules:
                    continue
                seen_modules.add(importer)
                queue.append(importer)
                importer_node = self.nodes[importer]
                if importer_node.test:
                    affected.update(self._module_test_nodes[importer_node.module])
        return tuple(sorted(affected))

    def node_for_interface_change(self, change: InterfaceChange) -> str | None:
        """把接口差分映射到唯一关系图节点。

        Args:
            change: 现有接口差分记录。

        Returns:
            唯一匹配的关系节点标识；无法静态唯一解析时返回 ``None``。
        """
        if change.kind == "file":
            candidate = f"module:{change.path}"
            return candidate if candidate in self.nodes else None
        module = _module_name(change.path)
        candidate = f"{module}.{change.symbol}" if module else change.symbol
        if candidate in self.nodes:
            return candidate
        matches = [
            node_id
            for node_id, node in self.nodes.items()
            if node.path == change.path and node.qualname == change.symbol
        ]
        return matches[0] if len(matches) == 1 else None

    def stats(self) -> dict[str, int]:
        """返回节点与各类关系的紧凑统计。

        Returns:
            节点、总边以及各关系类型的数量映射。
        """
        result = {"nodes": len(self.nodes), "edges": len(self.edges)}
        for kind in ("DEFINES", "IMPORTS", "CALLS", "INHERITS", "OVERRIDES"):
            result[kind.lower()] = sum(len(items) for items in self._out[kind].values())
        return result


def _normalized_visibility(name: str, private: bool) -> Visibility:
    """Map Python naming/access evidence onto shared visibility facts."""
    if name.startswith("__") and not name.endswith("__"):
        return Visibility.PRIVATE
    if private:
        return Visibility.INTERNAL
    return Visibility.PUBLIC


def _project_authored_topology(
    graph: RepositoryRelationGraph, source: _TopologyProjectionSource
) -> tuple[dict[str, OwnerFact], dict[str, SymbolFact]]:
    """Project authored module/class/function facts without supplemental relations."""
    module_nodes = dict(source.module_nodes)
    explicit_exports = dict(source.explicit_exports)
    owner_map: dict[str, OwnerFact] = {}
    symbol_map: dict[str, SymbolFact] = {}
    for node_id, node in graph.nodes.items():
        if node.kind == "module":
            owner_map[node_id] = OwnerFact(
                owner_id=node_id,
                language="python",
                kind=OwnerKind.MODULE,
                path=Path(node.path),
                line=1,
            )
            continue

        module_owner = module_nodes[node.module]
        lexical_owner = (
            f"{node.module}.{node.owner}" if node.module and node.owner else node.owner
        )
        if lexical_owner in graph.nodes and graph.nodes[lexical_owner].kind == "class":
            owner_id = lexical_owner
            owner_kind = OwnerKind.CLASS
        elif node.nested and lexical_owner:
            owner_id = lexical_owner
            owner_kind = OwnerKind.UNKNOWN
        else:
            owner_id = module_owner
            owner_kind = OwnerKind.MODULE

        normalized_kind = (
            "function" if node.nested and node.kind == "method" else node.kind
        )
        symbol_map[node_id] = SymbolFact(
            symbol_id=node_id,
            language="python",
            kind=normalized_kind,
            owner_id=owner_id,
            owner_kind=owner_kind,
            visibility=_normalized_visibility(node.name, node.private),
            path=Path(node.path),
            line=node.line,
            end_line=node.end_line,
            lines=node.lines,
            parameter_count=node.parameter_count,
            name=node.name,
            module=node.module,
            qualname=node.qualname,
            decorators=node.decorators,
            bases=node.bases,
            exported=(not node.owner and node.name in explicit_exports[node.module]),
            nested=node.nested,
            test=node.test,
            fingerprint=node.fingerprint,
        )
        if node.kind == "class":
            owner_map[node_id] = OwnerFact(
                owner_id=node_id,
                language="python",
                kind=OwnerKind.CLASS,
                path=Path(node.path),
                line=node.line,
            )
    return owner_map, symbol_map


def _project_legacy_topology_edges(
    graph: RepositoryRelationGraph,
) -> set[UsageEdge]:
    """Project established relation-graph edges into normalized usage kinds."""
    legacy_kinds = {
        "IMPORTS": UsageKind.DEPENDENCY,
        "INHERITS": UsageKind.INHERITANCE,
        "OVERRIDES": UsageKind.OVERRIDE,
    }
    result: set[UsageEdge] = set()
    for edge in graph.edges:
        if edge.kind not in legacy_kinds:
            continue
        source = graph.nodes[edge.source]
        result.add(
            UsageEdge(
                source_id=edge.source,
                target_id=edge.target,
                kind=legacy_kinds[edge.kind],
                path=Path(source.path),
                line=source.line,
            )
        )
    return result


def _populate_external_protocols(
    graph: RepositoryRelationGraph,
    source: _TopologyProjectionSource,
    dependency_resolver: StaticPythonDependencyResolver,
    owner_map: dict[str, OwnerFact],
    symbol_map: dict[str, SymbolFact],
    edges: set[UsageEdge],
) -> None:
    """Enrich topology with statically resolved external inheritance/protocol hooks."""
    methods_by_owner: dict[str, list[_RelationNode]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind == "method":
            owner_id = f"{node.module}.{node.owner}" if node.module else node.owner
            methods_by_owner[owner_id].append(node)

    for external_base in source.external_bases:
        external = dependency_resolver.resolve_class(external_base.qualified_name)
        if external is None:
            continue
        class_node = graph.nodes[external_base.class_id]
        external_class_id = f"external:{external.qualified_name}"
        external_module = external.qualified_name.rpartition(".")[0]
        external_owner_id = f"external-module:{external_module}"
        owner_map[external_owner_id] = OwnerFact(
            owner_id=external_owner_id,
            language="python",
            kind=OwnerKind.MODULE,
            path=external.source,
            line=1,
        )
        symbol_map[external_class_id] = SymbolFact(
            symbol_id=external_class_id,
            language="python",
            kind="class",
            owner_id=external_owner_id,
            owner_kind=OwnerKind.MODULE,
            visibility=Visibility.PUBLIC,
            path=external.source,
            line=external.line,
            end_line=external.line,
            lines=1,
            name=external.qualified_name.rpartition(".")[2],
            module=external_module,
            qualname=external.qualified_name,
            foreign=True,
        )
        edges.add(
            UsageEdge(
                source_id=external_base.class_id,
                target_id=external_class_id,
                kind=UsageKind.INHERITANCE,
                path=Path(class_node.path),
                line=class_node.line,
                confidence=external.confidence,
            )
        )
        external_methods = frozenset(external.methods)
        for method in methods_by_owner[external_base.class_id]:
            if method.name not in external_methods:
                continue
            external_method_id = f"external:{external.qualified_name}.{method.name}"
            symbol_map[external_method_id] = SymbolFact(
                symbol_id=external_method_id,
                language="python",
                kind="method",
                owner_id=external_class_id,
                owner_kind=OwnerKind.CLASS,
                visibility=Visibility.PUBLIC,
                path=external.source,
                line=external.line,
                end_line=external.line,
                lines=1,
                name=method.name,
                module=external_module,
                qualname=f"{external.qualified_name}.{method.name}",
                foreign=True,
            )
            edges.add(
                UsageEdge(
                    source_id=method.node_id,
                    target_id=external_method_id,
                    kind=UsageKind.PROTOCOL_HOOK,
                    path=Path(method.path),
                    line=method.line,
                    confidence=external.confidence,
                )
            )


def _populate_local_reuse_facts(
    graph: RepositoryRelationGraph,
    source: _TopologyProjectionSource,
    symbol_map: dict[str, SymbolFact],
    edges: set[UsageEdge],
) -> None:
    """Project callable references, state access and lexical captures resolved by the graph."""
    for reference in source.references:
        caller = graph.nodes[reference.source]
        edges.add(
            UsageEdge(
                source_id=reference.source,
                target_id=reference.target,
                kind=reference.kind,
                path=Path(caller.path),
                line=reference.line,
            )
        )

    for access in source.field_accesses:
        caller = graph.nodes[access.source]
        owner = graph.nodes[access.class_id]
        field_id = f"field:{access.class_id}.{access.field_name}"
        symbol_map[field_id] = SymbolFact(
            symbol_id=field_id,
            language="python",
            kind="field",
            owner_id=access.class_id,
            owner_kind=OwnerKind.CLASS,
            visibility=_normalized_visibility(
                access.field_name, access.field_name.startswith("_")
            ),
            path=Path(caller.path),
            line=access.line,
            end_line=access.line,
            lines=1,
            name=access.field_name,
            module=owner.module,
            qualname=f"{owner.qualname}.{access.field_name}",
        )
        edges.add(
            UsageEdge(
                source_id=access.source,
                target_id=field_id,
                kind=UsageKind.FIELD_ACCESS,
                path=Path(caller.path),
                line=access.line,
            )
        )

    for source_id, outer_id, name, line in source.closure_captures:
        nested = graph.nodes[source_id]
        outer = graph.nodes[outer_id]
        binding_id = f"binding:{outer_id}:{name}"
        symbol_map[binding_id] = SymbolFact(
            symbol_id=binding_id,
            language="python",
            kind="binding",
            owner_id=outer_id,
            owner_kind=OwnerKind.UNKNOWN,
            visibility=Visibility.INTERNAL,
            path=Path(outer.path),
            line=outer.line,
            end_line=outer.line,
            lines=1,
            name=name,
            module=outer.module,
            qualname=f"{outer.qualname}:{name}",
        )
        edges.add(
            UsageEdge(
                source_id=source_id,
                target_id=binding_id,
                kind=UsageKind.CLOSURE_CAPTURE,
                path=Path(nested.path),
                line=line,
            )
        )


def normalized_topology(
    graph: RepositoryRelationGraph,
    dependency_resolver: StaticPythonDependencyResolver | None = None,
) -> RepositoryTopology:
    """Project authoritative Python relation facts into the language-neutral topology.

    Supplemental facts exist only in normalized topology and are not inserted into the
    legacy relation-edge set used by existing impact/reachability rules.

    Args:
        graph: Authoritative repository relation graph built from one analysis snapshot.
        dependency_resolver: Optional static resolver for external protocol contracts.

    Returns:
        Normalized owners, symbols and typed usage edges for shared quality policy.
    """
    source = graph.topology_source
    owner_map, symbol_map = _project_authored_topology(graph, source)
    edges = _project_legacy_topology_edges(graph)
    _populate_local_reuse_facts(graph, source, symbol_map, edges)
    if dependency_resolver is not None:
        _populate_external_protocols(
            graph, source, dependency_resolver, owner_map, symbol_map, edges
        )
    return RepositoryTopology(
        symbols=tuple(sorted(symbol_map.values(), key=lambda item: item.symbol_id)),
        owners=tuple(sorted(owner_map.values(), key=lambda item: item.owner_id)),
        edges=tuple(
            sorted(
                edges,
                key=lambda item: (
                    item.path.as_posix(),
                    item.line,
                    item.source_id,
                    item.target_id,
                    item.kind,
                ),
            )
        ),
    )


def _changed_symbols(
    base: RepositoryRelationGraph,
    current: RepositoryRelationGraph,
) -> tuple[set[str], set[str], set[str]]:
    """返回 added/removed/modified 节点集合。"""
    base_ids = set(base.nodes)
    current_ids = set(current.nodes)
    added = current_ids - base_ids
    removed = base_ids - current_ids
    modified = {
        node_id
        for node_id in base_ids & current_ids
        if base.nodes[node_id].fingerprint != current.nodes[node_id].fingerprint
    }
    return added, removed, modified


def _preserved_direct_edges(
    base: RepositoryRelationGraph,
    current: RepositoryRelationGraph,
    modified: set[str],
) -> list[dict[str, object]]:
    """找出 BASE 直接调用边消失但 TARGET 传递可达性仍存在的关系。"""
    rows: list[dict[str, object]] = []
    for edge in sorted(
        base.edges, key=lambda item: (item.source, item.target, item.kind)
    ):
        if edge.kind != "CALLS" or edge.source not in modified:
            continue
        if edge.source not in current.nodes or edge.target not in current.nodes:
            continue
        if edge.target in current.outgoing(edge.source, "CALLS"):
            continue
        path = current.shortest_path(edge.source, edge.target, "CALLS")
        if len(path) >= 3:
            rows.append(
                {
                    "source": edge.source,
                    "target": edge.target,
                    "path": list(path),
                    "distance_before": 1,
                    "distance_after": len(path) - 1,
                }
            )
        if len(rows) >= _MAX_RENDERED_ITEMS:
            break
    return rows


def _interface_impacts(
    base: RepositoryRelationGraph,
    current: RepositoryRelationGraph,
    interface_diff: InterfaceDiffReport,
) -> list[_InterfaceImpact]:
    """为修改/删除的函数、方法和类计算 blast radius。"""
    impacts: list[_InterfaceImpact] = []
    for change in interface_diff.changes:
        if (
            _is_test_source(change.path)
            or change.kind not in {"function", "method", "class"}
            or change.change == "added"
        ):
            continue
        graph = current if change.change == "modified" else base
        node_id = graph.node_for_interface_change(change)
        if node_id is None:
            continue
        direct = graph.incoming(node_id, "CALLS")
        dependents = graph.reverse_impact(node_id)
        tests = tuple(
            node
            for node in dependents
            if node in graph.nodes
            and graph.nodes[node].test
            and graph.nodes[node].kind == "test"
        )
        test_files = tuple(sorted({graph.nodes[node].path for node in tests}))
        impacts.append(
            _InterfaceImpact(
                change=change.change,
                kind=change.kind,
                path=change.path,
                symbol=change.symbol,
                node_id=node_id,
                direct_callers=direct,
                transitive_dependents=dependents,
                affected_tests=tests,
                affected_test_files=test_files,
            )
        )
    return impacts


def build_relation_graph_summary(
    base_snapshot: RepositoryAnalysisSnapshot | None,
    current_snapshot: RepositoryAnalysisSnapshot,
    interface_diff: InterfaceDiffReport,
    base_graph: RepositoryRelationGraph | None = None,
    current_graph: RepositoryRelationGraph | None = None,
) -> tuple[
    RepositoryRelationGraph | None,
    RepositoryRelationGraph | None,
    dict[str, object],
]:
    """派生 BASE/TARGET 关系图及面向审计的紧凑差分事实。

    Args:
        base_snapshot: Git 基线快照；无可用 Git 基线时为 ``None``。
        current_snapshot: 当前比较目标的统一仓库快照。
        interface_diff: 已由现有接口审计生成的接口差分。
        base_graph: 可选的已构建基线图；存在时直接复用。
        current_graph: 可选的已构建目标图；存在时直接复用。

    Returns:
        BASE 图、TARGET 图与紧凑关系摘要；缺少 Git 基线时返回两个 ``None`` 和空摘要。
    """
    if base_snapshot is None:
        return None, None, {}
    base = (
        base_graph if base_graph is not None else RepositoryRelationGraph(base_snapshot)
    )
    current = (
        current_graph
        if current_graph is not None
        else RepositoryRelationGraph(current_snapshot)
    )
    added, removed, modified = _changed_symbols(base, current)
    changed_production = {
        node_id
        for node_id in (added | modified)
        if node_id in current.nodes and not current.nodes[node_id].test
    }
    affected: set[str] = set()
    for node_id in changed_production:
        affected.update(current.affected_tests(node_id))
    for node_id in removed:
        if node_id in base.nodes and not base.nodes[node_id].test:
            affected.update(base.affected_tests(node_id))
    affected_files = tuple(
        sorted(
            {
                graph.nodes[node_id].path
                for graph in (base, current)
                for node_id in affected
                if node_id in graph.nodes and graph.nodes[node_id].test
            }
        )
    )
    summary: dict[str, object] = {
        "base": base.stats(),
        "current": current.stats(),
        "changed_symbols": {
            "added": sorted(added)[:_MAX_RENDERED_ITEMS],
            "added_count": len(added),
            "removed": sorted(removed)[:_MAX_RENDERED_ITEMS],
            "removed_count": len(removed),
            "modified": sorted(modified)[:_MAX_RENDERED_ITEMS],
            "modified_count": len(modified),
        },
        "preserved_reachability": _preserved_direct_edges(base, current, modified),
        "interface_impacts": [
            item.to_dict() for item in _interface_impacts(base, current, interface_diff)
        ],
        "affected_tests": list(affected_files[:_MAX_RENDERED_ITEMS]),
        "affected_test_count": len(affected_files),
        "affected_test_nodes": sorted(affected)[:_MAX_RENDERED_ITEMS],
        "affected_test_node_count": len(affected),
        "suppressed_direct_edge_findings": [],
    }
    return base, current, summary


def _affected_test_mutation_finding(
    node: _RelationNode,
    test_id: str,
    current: RepositoryRelationGraph,
    affected_by: tuple[str, ...],
) -> Finding:
    """构造一个既有 affected test 被修改/删除的关系语义候选。"""
    return Finding(
        code="QG193",
        severity="info",
        confidence="high",
        path=node.path,
        line=node.line,
        column=0,
        symbol=node.qualname,
        message=(
            "本轮修改了经仓库关系证明会受生产变更影响的既有测试用例；"
            "必须先解释旧断言为何失效，不能把 affected tests 当成同步编辑清单。"
        ),
        suggestion=(
            "优先用 accepted baseline 的原测试验证生产修改；只有明确需求变化、"
            "Bug 修复或测试自身缺陷才能修改该测试，并保留可观察契约证据。"
        ),
        evidence={
            "semantic_review_required": True,
            "semantic_review_question": "Q11",
            "semantic_review_kind": "affected-test-mutated",
            "test_change": "removed" if test_id not in current.nodes else "modified",
            "affected_by": list(affected_by[:_MAX_RENDERED_ITEMS]),
            "affected_by_count": len(affected_by),
        },
        source="relation-graph-candidate",
    )


def _affected_test_mutation_findings(
    base: RepositoryRelationGraph,
    current: RepositoryRelationGraph,
    added: set[str],
    removed: set[str],
    modified: set[str],
) -> list[Finding]:
    """把真正位于 affected RUN set 的既有测试用例修改提升为 QG193。"""
    base_tests = {
        node_id
        for node_id, node in base.nodes.items()
        if node.test and node.kind == "test"
    }
    current_tests = {
        node_id
        for node_id, node in current.nodes.items()
        if node.test and node.kind == "test"
    }
    changed_tests = sorted((removed | modified) & (base_tests | current_tests))
    base_production = {node_id for node_id, node in base.nodes.items() if not node.test}
    current_production = {
        node_id for node_id, node in current.nodes.items() if not node.test
    }
    production_nodes = base_production | current_production
    changed_production = sorted((added | removed | modified) & production_nodes)
    affected_by_test: dict[str, set[str]] = defaultdict(set)
    for node_id in changed_production:
        graph = current if node_id in current.nodes else base
        for test_id in graph.affected_tests(node_id):
            affected_by_test[test_id].add(node_id)

    findings: list[Finding] = []
    for test_id in changed_tests:
        affected_by = tuple(sorted(affected_by_test[test_id]))
        if not affected_by:
            continue
        node = (
            current.nodes[test_id] if test_id in current.nodes else base.nodes[test_id]
        )
        findings.append(
            _affected_test_mutation_finding(node, test_id, current, affected_by)
        )
    return findings


def _interface_coverage_unknown_findings(
    base: RepositoryRelationGraph,
    current: RepositoryRelationGraph,
    interface_diff: InterfaceDiffReport,
) -> list[Finding]:
    """把有生产依赖却无可证明测试关系的接口变化提升为 QG194。"""
    unproven_by_path: dict[str, list[_InterfaceImpact]] = defaultdict(list)
    for impact in _interface_impacts(base, current, interface_diff):
        if impact.transitive_dependents and not impact.affected_tests:
            unproven_by_path[impact.path].append(impact)

    findings: list[Finding] = []
    for path, impacts in sorted(unproven_by_path.items()):
        strongest = max(impacts, key=lambda item: len(item.transitive_dependents))
        findings.append(
            Finding(
                code="QG194",
                severity="info",
                confidence="medium",
                path=path,
                line=(
                    current.nodes[strongest.node_id].line
                    if strongest.node_id in current.nodes
                    else base.nodes[strongest.node_id].line
                ),
                column=0,
                symbol=strongest.symbol,
                message=(
                    "本轮接口变化存在静态可达的生产依赖，但关系图无法证明任何既有测试"
                    "覆盖这些接口；这是测试覆盖关系未知，不等同于断言没有测试。"
                ),
                suggestion=(
                    "在 Q11 中给出实际行为测试入口；若测试依赖动态注册/fixture/HTTP 路由，"
                    "说明该可观察路径，否则补充稳定接口/返回契约回归测试。"
                ),
                evidence={
                    "semantic_review_required": True,
                    "semantic_review_question": "Q2,Q11",
                    "semantic_review_kind": "interface-test-coverage-unknown",
                    "interface_count": len(impacts),
                    "interfaces": [
                        item.symbol for item in impacts[:_MAX_RENDERED_ITEMS]
                    ],
                    "max_direct_caller_count": max(
                        len(item.direct_callers) for item in impacts
                    ),
                    "max_transitive_dependent_count": len(
                        strongest.transitive_dependents
                    ),
                },
                source="relation-graph-candidate",
            )
        )
    return findings


def relation_graph_candidate_findings(
    base: RepositoryRelationGraph | None,
    current: RepositoryRelationGraph | None,
    interface_diff: InterfaceDiffReport,
) -> list[Finding]:
    """把关系图新暴露的跨文件风险转换为语义审计候选。

    Args:
        base: Git accepted baseline 的派生关系图；无 Git 基线时为 None。
        current: 当前目标树的派生关系图；无 Git 基线时为 None。
        interface_diff: 既有接口差分，用于判断接口 blast radius 与测试覆盖关系。

    Returns:
        QG193/QG194 关系型语义候选列表；关系未知时保持保守，不伪造确定缺陷。
    """
    if base is None or current is None:
        return []
    added, removed, modified = _changed_symbols(base, current)
    return [
        *_affected_test_mutation_findings(base, current, added, removed, modified),
        *_interface_coverage_unknown_findings(base, current, interface_diff),
    ]


def enrich_findings_with_relations(
    findings: list[Finding],
    graph: RepositoryRelationGraph,
    base_graph: RepositoryRelationGraph | None = None,
) -> list[Finding]:
    """为 helper finding 增补关系事实并标记需语义裁决的责任边界。

    Args:
        findings: 现有静态 finding 列表，不删除 QG001/QG010/QG168 本体。
        graph: 当前 TARGET 关系图。
        base_graph: 可选 BASE 关系图，用于区分历史节点与本轮修改节点。

    Returns:
        保留原规则编码并补充 caller/callee/affected-tests 证据的 finding 列表。
    """
    from dataclasses import replace

    enriched: list[Finding] = []
    helper_codes = {"QG001", "QG010", "QG168"}
    for finding in findings:
        if finding.symbol not in graph.nodes or finding.code not in helper_codes:
            enriched.append(finding)
            continue
        node = graph.nodes[finding.symbol]
        callers = graph.incoming(node.node_id, "CALLS")
        callees = graph.outgoing(node.node_id, "CALLS")
        affected_tests = graph.affected_tests(node.node_id)
        evidence = dict(finding.evidence)
        evidence.update(
            {
                "relation_direct_callers": list(callers[:_MAX_RENDERED_ITEMS]),
                "relation_direct_caller_count": len(callers),
                "relation_callees": list(callees[:_MAX_RENDERED_ITEMS]),
                "relation_callee_count": len(callees),
                "relation_affected_tests": list(affected_tests[:_MAX_RENDERED_ITEMS]),
                "relation_affected_test_count": len(affected_tests),
            }
        )
        base_node = (
            base_graph.nodes[node.node_id]
            if base_graph is not None and node.node_id in base_graph.nodes
            else None
        )
        changed = base_node is None or base_node.fingerprint != node.fingerprint
        if (
            finding.code == "QG001"
            and changed
            and len(callers) == 1
            and len(callees) >= 3
        ):
            evidence.update(
                {
                    "semantic_review_required": True,
                    "semantic_review_question": "Q3,Q4",
                    "semantic_review_kind": "single-use-responsibility-boundary",
                    "qg179_exempt": True,
                }
            )
        enriched.append(replace(finding, evidence=evidence))
    return enriched


def suppress_preserved_direct_edge_findings(
    findings: list[Finding],
    graph: RepositoryRelationGraph,
) -> tuple[list[Finding], list[dict[str, object]]]:
    """移除仅因直接边消失产生且已有替代传递路径证明的 QG162 误报。

    Args:
        findings: 当前生产 finding 列表。
        graph: TARGET 关系图，用于验证父类能力是否仍静态可达。

    Returns:
        未被抑制的 findings 与被抑制 QG162 的替代关系路径证据。
    """
    kept: list[Finding] = []
    suppressed: list[dict[str, object]] = []
    for finding in findings:
        if finding.code != "QG162":
            kept.append(finding)
            continue
        source_candidates = [
            node_id
            for node_id, node in graph.nodes.items()
            if node.path == finding.path and node.qualname == finding.symbol
        ]
        if not source_candidates:
            source_candidates = [
                node_id
                for node_id, node in graph.nodes.items()
                if node.path == finding.path and node_id.endswith(f".{finding.symbol}")
            ]
        parent = str(finding.evidence["parent"]) if "parent" in finding.evidence else ""
        method = str(finding.evidence["method"]) if "method" in finding.evidence else ""
        target_candidates = [
            node_id
            for node_id, node in graph.nodes.items()
            if node.kind == "method" and node.qualname.endswith(f"{parent}.{method}")
        ]
        preserved_path: tuple[str, ...] = ()
        source_id = source_candidates[0] if len(source_candidates) == 1 else ""
        target_id = ""
        for candidate in target_candidates if source_id else ():
            path = graph.shortest_path(source_id, candidate, "CALLS")
            if len(path) >= 3:
                preserved_path = path
                target_id = candidate
                break
        if not preserved_path:
            kept.append(finding)
            continue
        suppressed.append(
            {
                "code": "QG162",
                "path": finding.path,
                "symbol": finding.symbol,
                "source": source_id,
                "target": target_id,
                "replacement_path": list(preserved_path),
            }
        )
    return kept, suppressed


def apply_relation_finding_context(
    findings: list[Finding],
    graph: RepositoryRelationGraph | None,
    base_graph: RepositoryRelationGraph | None,
    summary: dict[str, object],
    interface_diff: InterfaceDiffReport,
) -> tuple[list[Finding], dict[str, object]]:
    """把关系图证据安全应用到既有 findings 与报告摘要。

    Args:
        findings: 当前生产 finding 列表。
        graph: TARGET 关系图；无 Git 基线时为 ``None``。
        base_graph: BASE 关系图；无 Git 基线时为 ``None``。
        summary: 当前关系摘要；无 Git 基线时为空映射。
        interface_diff: 既有接口差分，用于生成关系型新增召回候选。

    Returns:
        增强后的 findings 与包含 QG162 抑制路径的关系摘要。无图时原样返回。
    """
    if graph is None:
        return findings, summary
    kept, suppressed = suppress_preserved_direct_edge_findings(findings, graph)
    kept.extend(relation_graph_candidate_findings(base_graph, graph, interface_diff))
    updated_summary = dict(summary)
    updated_summary["suppressed_direct_edge_findings"] = suppressed
    return enrich_findings_with_relations(kept, graph, base_graph), updated_summary
