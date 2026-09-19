"""仓库关系图：从统一 RepositoryAnalysisSnapshot 派生符号、调用与影响关系。"""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .config import is_test_path
from .model import Finding, InterfaceChange, InterfaceDiffReport

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
    owner: str
    private: bool
    test: bool
    fingerprint: str


@dataclass(slots=True, frozen=True)
class _RelationEdge:
    """保存两个仓库节点之间的一条确定静态关系。"""

    source: str
    target: str
    kind: str


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
        self.nodes: list[_RelationNode] = []
        self.imports: dict[str, str] = {}
        self.calls: list[tuple[str, ast.expr]] = []
        self.classes: dict[str, tuple[str, ...]] = {}

    @property
    def caller(self) -> str:
        """返回当前定义的稳定 node id；模块级表达式归到 module 节点。"""
        qualname = ".".join([*self.class_stack, *self.function_stack])
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
                owner="",
                private=False,
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
        qualname = ".".join([*self.class_stack, *self.function_stack, node.name])
        node_id = f"{self.module}.{qualname}" if self.module else qualname
        owner = ".".join([*self.class_stack, *self.function_stack])
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
                owner=owner,
                private=node.name.startswith("_")
                and not (node.name.startswith("__") and node.name.endswith("__")),
                test=_is_test_source(self.path),
                fingerprint=_class_digest(node),
            )
        )
        self.classes[node_id] = bases
        self.class_stack.append(node.name)
        for child in node.body:
            self.visit(child)
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
        qualname = ".".join([*self.class_stack, *self.function_stack, node.name])
        node_id = f"{self.module}.{qualname}" if self.module else qualname
        owner = ".".join([*self.class_stack, *self.function_stack])
        kind = (
            "method"
            if self.class_stack
            else "test"
            if _is_test_source(self.path) and node.name.startswith("test")
            else "function"
        )
        self.nodes.append(
            _RelationNode(
                node_id=node_id,
                kind=kind,
                path=self.path,
                module=self.module,
                qualname=qualname,
                name=node.name,
                line=node.lineno,
                owner=owner,
                private=node.name.startswith("_")
                and not (node.name.startswith("__") and node.name.endswith("__")),
                test=_is_test_source(self.path),
                fingerprint=hashlib.sha256(
                    ast.dump(
                        node, annotate_fields=True, include_attributes=False
                    ).encode("utf-8")
                ).hexdigest()[:20],
            )
        )
        self.function_stack.append(node.name)
        for child in node.body:
            self.visit(child)
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
            self.visit(argument)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """继续遍历属性基表达式而不建立宽泛引用边。

        Args:
            node: 当前属性访问 AST 节点。

        Returns:
            None.
        """
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
        self._simple_names: dict[str, set[str]] = defaultdict(set)
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
            self._class_bases.update(collector.classes)
            for node in collector.nodes:
                self.nodes[node.node_id] = node
            self._pending_calls.extend(
                (collector.module, caller, expr) for caller, expr in collector.calls
            )

    def _index_definitions(self) -> None:
        """建立简单名称、测试节点以及 owner→definition 关系。"""
        for node_id, node in self.nodes.items():
            if node.kind == "module":
                continue
            self._simple_names[node.name].add(node_id)
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
        """解析类继承声明并返回每个类的确定父类节点。"""
        resolved_bases: dict[str, tuple[str, ...]] = {}
        for class_id, bases in self._class_bases.items():
            node = self.nodes[class_id]
            targets = tuple(
                target
                for base in bases
                if (target := self._resolve_name(node.module, class_id, base))
                is not None
                and self.nodes[target].kind == "class"
            )
            resolved_bases[class_id] = targets
            for target in targets:
                self._add_edge(class_id, target, "INHERITS")
        return resolved_bases

    def _link_calls(self) -> None:
        """把带 caller 上下文的调用表达式解析为静态 CALLS 边。"""
        for module, caller, expr in self._pending_calls:
            target = self._resolve_expr(module, caller, expr)
            if target is None:
                continue
            target_node = self.nodes[target]
            constructor = f"{target}.__init__"
            if target_node.kind == "class" and constructor in self.nodes:
                target = constructor
            self._add_edge(caller, target, "CALLS")

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
        """解析简单名称或 import 别名到唯一仓库节点。"""
        if name in {"self", "cls", "super"}:
            return None
        imports = self._imports[module] if module in self._imports else {}
        root, dot, suffix = name.partition(".")
        if root in imports:
            candidate = imports[root] + (f".{suffix}" if dot else "")
            if candidate in self.nodes:
                return candidate
        local = f"{module}.{name}" if module else name
        if local in self.nodes:
            return local
        caller_node = self.nodes[caller] if caller in self.nodes else None
        if caller_node is not None and caller_node.owner:
            class_prefix = (
                f"{module}.{caller_node.owner}" if module else caller_node.owner
            )
            candidate = f"{class_prefix}.{name}"
            if candidate in self.nodes:
                return candidate
        matches = self._simple_names[name] if name in self._simple_names else set()
        return next(iter(matches)) if len(matches) == 1 else None

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
        return candidate if candidate in self.nodes else None

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

    Returns:
        BASE 图、TARGET 图与紧凑关系摘要；缺少 Git 基线时返回两个 ``None`` 和空摘要。
    """
    if base_snapshot is None:
        return None, None, {}
    base = RepositoryRelationGraph(base_snapshot)
    current = RepositoryRelationGraph(current_snapshot)
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
