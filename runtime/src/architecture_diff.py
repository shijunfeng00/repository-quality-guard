from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .config import GuardConfig
from .git_utils import run_readonly_git
from .model import Finding

_IDENTIFIER_PART = re.compile(r"[A-Za-z][a-z0-9]*|[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
_IGNORED_NAME_PARTS = {"get", "new", "make", "build", "create", "helper", "private"}
_MIN_SHARED_FEATURES = 2
_CAPABILITY_SIMILARITY_THRESHOLD = 0.30
_MIN_SIBLING_IMPLEMENTATIONS = 2


@dataclass(slots=True, frozen=True)
class MethodSnapshot:
    """
    保存方法的结构指纹、依赖和状态访问事实。

    该快照用于比较 Git 基线与工作区中的方法实现和调用关系。
    """

    name: str
    line: int
    fingerprint: str
    call_names: frozenset[str]
    attribute_names: frozenset[str]
    receiver_call_names: frozenset[str]
    super_call_names: frozenset[str]
    explicit_parent_calls: frozenset[tuple[str, str]]
    typed_receiver_calls: frozenset[tuple[str, str]]


@dataclass(slots=True)
class ClassSnapshot:
    """
    保存单个类在一个 Git 快照中的继承与成员事实。

    该对象聚合父类、方法指纹和构造字段来源，不承担问题裁决。
    """

    path: str
    qualname: str
    name: str
    line: int
    bases: tuple[str, ...]
    methods: dict[str, MethodSnapshot] = field(default_factory=dict)
    assignments: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ParentCallEdge:
    """
    表示子类方法到继承方法的一条静态调用边。

    调用边同时记录调用方、父类所有者、方法名和源码位置。
    """

    child: str
    caller: str
    parent: str
    method: str
    path: str
    line: int


@dataclass(slots=True, frozen=True)
class TypedReceiverCallEdge:
    """表示显式类型接收者到公共方法的一条编排调用边。"""

    owner: str
    caller: str
    receiver_type: str
    method: str
    path: str
    line: int


@dataclass(slots=True)
class ArchitectureSnapshot:
    """
    汇总一个快照中的类、方法所有者和继承调用边。

    比较器只在完整快照之间生成确定性的架构差分。
    """

    classes: dict[str, ClassSnapshot]
    simple_names: dict[str, tuple[str, ...]]
    parent_calls: set[ParentCallEdge]
    typed_receiver_calls: set[TypedReceiverCallEdge]


class _MethodCollector(ast.NodeVisitor):
    """收集方法中的调用名称和属性访问，不进入嵌套作用域。"""

    def __init__(self) -> None:
        """
        初始化空的调用名称和属性名称集合。

        Returns:
            None。
        """
        self.call_names: set[str] = set()
        self.attribute_names: set[str] = set()
        self.receiver_names: frozenset[str] = frozenset()
        self.super_names: frozenset[str] = frozenset()
        self.parent_names: dict[str, frozenset[str]] = {}
        self.local_names: frozenset[str] = frozenset()
        self.base_receivers: dict[str, str] = {}
        self.receiver_call_names: set[str] = set()
        self.super_call_names: set[str] = set()
        self.explicit_parent_calls: set[tuple[str, str]] = set()
        self.typed_receivers: dict[str, frozenset[str]] = {}
        self.typed_receiver_calls: set[tuple[str, str]] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """
        忽略嵌套同步函数，不把闭包调用归入外层方法。

        Args:
            node: 嵌套同步函数节点。

        Returns:
            None。
        """

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """
        忽略嵌套异步函数，不把闭包调用归入外层方法。

        Args:
            node: 嵌套异步函数节点。

        Returns:
            None。
        """

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """
        忽略嵌套类，不把内部类行为归入外层方法。

        Args:
            node: 嵌套类节点。

        Returns:
            None。
        """

    def visit_Call(self, node: ast.Call) -> None:
        """
        记录被调用函数或属性的末级名称。

        Args:
            node: 函数调用节点。

        Returns:
            None。
        """
        if isinstance(node.func, ast.Name):
            self.call_names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            receiver = node.func.value
            self.call_names.add(method_name)
            if isinstance(receiver, ast.Name) and receiver.id in self.typed_receivers:
                self.typed_receiver_calls.update(
                    (receiver_type, method_name)
                    for receiver_type in self.typed_receivers[receiver.id]
                )
            if isinstance(receiver, ast.Name) and receiver.id in self.receiver_names:
                self.receiver_call_names.add(method_name)
            elif (isinstance(receiver, ast.Name) and receiver.id in self.super_names) or (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Name)
                and receiver.func.id == "super"
            ):
                self.super_call_names.add(method_name)
            else:
                receiver_text = _render(receiver)
                parent_names = (
                    set(self.parent_names[receiver_text])
                    if receiver_text in self.parent_names
                    else set()
                )
                receiver_root = receiver_text.split(".", 1)[0]
                if receiver_root not in self.local_names and receiver_text in self.base_receivers:
                    parent_names.add(self.base_receivers[receiver_text])
                self.explicit_parent_calls.update(
                    (parent_name, method_name) for parent_name in parent_names
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """
        记录实例属性末级名称。

        Args:
            node: 属性访问节点。

        Returns:
            None。
        """
        self.attribute_names.add(node.attr)
        self.generic_visit(node)


def _render(node: ast.AST | None) -> str:
    """将 AST 节点渲染为稳定文本。"""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return ast.dump(node, include_attributes=False)


def _receiver_annotation_names(annotation: ast.AST | None) -> frozenset[str]:
    """提取可直接充当接收者类型的简单类型名。"""
    result: frozenset[str] = frozenset()
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            parsed = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            parsed = None
        result = _receiver_annotation_names(parsed)
    elif isinstance(annotation, ast.Name):
        result = frozenset({annotation.id})
    elif isinstance(annotation, ast.Attribute):
        result = frozenset({annotation.attr})
    elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        result = _receiver_annotation_names(annotation.left) | _receiver_annotation_names(
            annotation.right
        )
    elif isinstance(annotation, ast.Subscript):
        wrapper = _render(annotation.value).split(".")[-1]
        result = (
            _receiver_annotation_names(annotation.slice)
            if wrapper in {"Optional", "Union", "Annotated"}
            else frozenset({wrapper})
        )
    elif isinstance(annotation, (ast.Tuple, ast.List)):
        result = frozenset().union(*(_receiver_annotation_names(item) for item in annotation.elts))
    return result


def _method_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """计算忽略名称、位置和 docstring 的方法体指纹。"""
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    normalized = ast.Module(body=body, type_ignores=[])
    payload = ast.dump(normalized, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _name_parts(name: str) -> set[str]:
    """把 snake/camel 名称拆成用于近似能力匹配的语义片段。"""
    parts = {
        item.lower()
        for chunk in name.strip("_").split("_")
        for item in _IDENTIFIER_PART.findall(chunk)
    }
    return parts - _IGNORED_NAME_PARTS


def _behavior_similarity(left: MethodSnapshot, right: MethodSnapshot) -> float:
    """根据调用和属性集合估算两个方法是否承载同一能力。"""
    left_features = set(left.call_names) | set(left.attribute_names)
    right_features = set(right.call_names) | set(right.attribute_names)
    if not left_features or not right_features:
        return 0.0
    shared = left_features & right_features
    if len(shared) < _MIN_SHARED_FEATURES:
        return 0.0
    return len(shared) / len(left_features | right_features)


def _is_shared_state_source(source: str) -> bool:
    """判断赋值来源是否可能表示父子类共享的同一外部状态。

    Args:
        source: 由 AST 稳定渲染得到的赋值右值文本。

    Returns:
        名称、属性或下标访问这类外部引用返回 True；字面量和新建对象返回 False。
    """
    if not source:
        return False
    try:
        expression = ast.parse(source, mode="eval").body
    except SyntaxError:
        return False
    return isinstance(expression, (ast.Name, ast.Attribute, ast.Subscript))


def _collect_assignments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """收集构造函数中直接写入 self 属性的值来源。"""
    result: dict[str, str] = {}
    for statement in ast.walk(node):
        if (
            isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and statement is not node
        ):
            continue
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
            value = statement.value
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                result[target.attr] = _render(value)
    return result


def _collect_class(path: str, node: ast.ClassDef, prefix: str = "") -> Iterable[ClassSnapshot]:
    """递归收集类及嵌套类。"""
    qualname = f"{prefix}.{node.name}" if prefix else node.name
    bases = tuple(_render(base).split(".")[-1] for base in node.bases)
    base_receivers = {
        receiver: base_name
        for base, base_name in zip(node.bases, bases, strict=True)
        for receiver in {_render(base), base_name}
    }
    snapshot = ClassSnapshot(
        path=path,
        qualname=qualname,
        name=node.name,
        line=node.lineno,
        bases=bases,
    )
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorator_names = {
                _render(item).split("(", 1)[0].split(".")[-1] for item in child.decorator_list
            }
            positional = [*child.args.posonlyargs, *child.args.args]
            receiver_names = (
                frozenset({positional[0].arg})
                if positional and "staticmethod" not in decorator_names
                else frozenset()
            )
            arguments = [
                *positional,
                *child.args.kwonlyargs,
                *([child.args.vararg] if child.args.vararg is not None else []),
                *([child.args.kwarg] if child.args.kwarg is not None else []),
            ]
            assignments = [
                (target.id, statement.value)
                for statement in child.body
                if isinstance(statement, ast.Assign)
                for target in statement.targets
                if isinstance(target, ast.Name)
            ]
            global_names = {
                name
                for statement in child.body
                if isinstance(statement, ast.Global)
                for name in statement.names
            }
            local_names = (
                {argument.arg for argument in arguments}
                | {target for target, _value in assignments}
            ) - global_names
            receiver_aliases = set(receiver_names)
            super_aliases: set[str] = set()
            parent_aliases: dict[str, set[str]] = {}
            for _pass in range(len(assignments) + 1):
                before = (
                    len(receiver_aliases),
                    len(super_aliases),
                    sum(len(parents) for parents in parent_aliases.values()),
                )
                receiver_aliases.update(
                    target
                    for target, value in assignments
                    if isinstance(value, ast.Name) and value.id in receiver_aliases
                )
                super_aliases.update(
                    target
                    for target, value in assignments
                    if (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "super"
                    )
                    or (isinstance(value, ast.Name) and value.id in super_aliases)
                )
                for target, value in assignments:
                    value_text = _render(value)
                    value_root = value_text.split(".", 1)[0]
                    parent_names = (
                        {base_receivers[value_text]}
                        if value_root not in local_names and value_text in base_receivers
                        else set(parent_aliases[value.id])
                        if isinstance(value, ast.Name) and value.id in parent_aliases
                        else set()
                    )
                    if parent_names:
                        existing = parent_aliases[target] if target in parent_aliases else set()
                        existing.update(parent_names)
                        parent_aliases[target] = existing
                after = (
                    len(receiver_aliases),
                    len(super_aliases),
                    sum(len(parents) for parents in parent_aliases.values()),
                )
                if after == before:
                    break
            collector = _MethodCollector()
            collector.receiver_names = frozenset(receiver_aliases)
            collector.super_names = frozenset(super_aliases)
            collector.parent_names = {
                name: frozenset(parents) for name, parents in parent_aliases.items()
            }
            collector.local_names = frozenset(local_names)
            collector.base_receivers = base_receivers
            collector.typed_receivers = {
                argument.arg: annotation_names
                for argument in arguments
                if (annotation_names := _receiver_annotation_names(argument.annotation))
            }
            collector.typed_receivers.update(
                {
                    statement.target.id: annotation_names
                    for statement in child.body
                    if isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and (annotation_names := _receiver_annotation_names(statement.annotation))
                }
            )
            for statement in child.body:
                collector.visit(statement)
            snapshot.methods[child.name] = MethodSnapshot(
                name=child.name,
                line=child.lineno,
                fingerprint=_method_fingerprint(child),
                call_names=frozenset(collector.call_names),
                attribute_names=frozenset(collector.attribute_names),
                receiver_call_names=frozenset(collector.receiver_call_names),
                super_call_names=frozenset(collector.super_call_names),
                explicit_parent_calls=frozenset(collector.explicit_parent_calls),
                typed_receiver_calls=frozenset(collector.typed_receiver_calls),
            )
            if child.name == "__init__":
                snapshot.assignments.update(_collect_assignments(child))
        elif isinstance(child, ast.ClassDef):
            yield from _collect_class(path, child, qualname)
    yield snapshot


def _classes_from_sources(
    sources: dict[str, str],
    trees: dict[str, ast.Module] | None = None,
) -> tuple[dict[str, ClassSnapshot], dict[str, tuple[str, ...]]]:
    """解析源码并建立类限定名和简单名称索引。"""
    classes: dict[str, ClassSnapshot] = {}
    simple: dict[str, list[str]] = {}
    for path, source in sorted(sources.items()):
        tree = trees[path] if trees is not None and path in trees else None
        if tree is None:
            try:
                tree = ast.parse(source, filename=path, type_comments=True)
            except SyntaxError:
                continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for item in _collect_class(path, node):
                key = f"{path}:{item.qualname}"
                classes[key] = item
                if item.name not in simple:
                    simple[item.name] = []
                simple[item.name].append(key)
    return classes, {name: tuple(keys) for name, keys in simple.items()}


def _parent_call_edges(
    classes: dict[str, ClassSnapshot],
    simple_names: dict[str, tuple[str, ...]],
) -> set[ParentCallEdge]:
    """根据类索引建立子类方法到唯一父类方法的调用边。"""
    parent_calls: set[ParentCallEdge] = set()
    for key, item in classes.items():
        for base_name in item.bases:
            if base_name not in simple_names:
                continue
            base_keys = simple_names[base_name]
            if len(base_keys) != 1:
                continue
            parent_key = base_keys[0]
            parent = classes[parent_key]
            for caller in item.methods.values():
                explicit_names = {
                    method_name
                    for owner_name, method_name in caller.explicit_parent_calls
                    if owner_name == base_name
                }
                candidate_names = (
                    caller.receiver_call_names | caller.super_call_names | explicit_names
                )
                for method_name in candidate_names:
                    is_direct_parent_call = (
                        method_name in caller.super_call_names or method_name in explicit_names
                    )
                    if (
                        not is_direct_parent_call and method_name in item.methods
                    ) or method_name not in parent.methods:
                        continue
                    parent_calls.add(
                        ParentCallEdge(
                            child=key,
                            caller=caller.name,
                            parent=parent_key,
                            method=method_name,
                            path=item.path,
                            line=caller.line,
                        )
                    )
    return parent_calls


def _typed_receiver_call_edges(
    classes: dict[str, ClassSnapshot],
) -> set[TypedReceiverCallEdge]:
    """汇总类方法中由显式参数类型证明的编排调用边。"""
    edges: set[TypedReceiverCallEdge] = set()
    for key, item in classes.items():
        for caller in item.methods.values():
            edges.update(
                TypedReceiverCallEdge(
                    owner=key,
                    caller=caller.name,
                    receiver_type=receiver_type,
                    method=method_name,
                    path=item.path,
                    line=caller.line,
                )
                for receiver_type, method_name in caller.typed_receiver_calls
            )
    return edges


def _snapshot_from_sources(
    sources: dict[str, str],
    trees: dict[str, ast.Module] | None = None,
) -> ArchitectureSnapshot:
    """从路径到源码的映射构造架构快照。"""
    classes, simple_names = _classes_from_sources(sources, trees)
    return ArchitectureSnapshot(
        classes=classes,
        simple_names=simple_names,
        parent_calls=_parent_call_edges(classes, simple_names),
        typed_receiver_calls=_typed_receiver_call_edges(classes),
    )


def _is_excluded(path: str, config: GuardConfig) -> bool:
    """按配置排除生成目录和第三方代码。"""
    return any(fnmatch(path, pattern) for pattern in config.exclude)


def _base_sources(root: Path, revision: str, config: GuardConfig) -> dict[str, str]:
    """读取指定 Git 基线中的 Python 源码。"""
    listed = run_readonly_git(root, "ls-tree", "-r", "--name-only", revision)
    if listed.returncode != 0:
        raise RuntimeError(listed.stderr.strip() or f"无法读取 Git 基线 {revision}")
    result: dict[str, str] = {}
    for path in listed.stdout.splitlines():
        if not path.endswith(".py") or _is_excluded(path, config):
            continue
        content = run_readonly_git(root, "show", f"{revision}:{path}")
        if content.returncode == 0:
            result[path] = content.stdout
    return result


def _worktree_sources(root: Path, config: GuardConfig) -> dict[str, str]:
    """读取当前工作区中的 Python 源码。"""
    listed = run_readonly_git(
        root, "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py"
    )
    if listed.returncode != 0:
        raise RuntimeError(listed.stderr.strip() or "无法读取工作区 Python 文件")
    result: dict[str, str] = {}
    for path in listed.stdout.splitlines():
        candidate = root / path
        if not candidate.is_file() or _is_excluded(path, config):
            continue
        result[path] = candidate.read_text(encoding="utf-8")
    return result


def _same_class(base_key: str, target: ArchitectureSnapshot) -> ClassSnapshot | None:
    """按路径和限定名称寻找目标快照中的同一个类。"""
    return target.classes.get(base_key)


def _removed_inheritance_findings(
    base: ArchitectureSnapshot,
    target: ArchitectureSnapshot,
) -> list[Finding]:
    """报告仍存在类上的继承边删除。"""
    findings: list[Finding] = []
    for key, before in base.classes.items():
        after = _same_class(key, target)
        if after is None:
            continue
        for base_name in sorted(set(before.bases) - set(after.bases)):
            findings.append(
                Finding(
                    code="QG164",
                    severity="error",
                    confidence="high",
                    path=after.path,
                    line=after.line,
                    column=1,
                    symbol=after.qualname,
                    message=f"Git 基线继承关系 `{after.qualname}({base_name})` 已从当前代码移除。",
                    suggestion="恢复继承，或在修改说明中披露新所有者、完整迁移链和验证证据。",
                    evidence={"child": after.qualname, "removed_base": base_name},
                )
            )
    return findings


def _replacement_finding(
    edge: ParentCallEdge,
    base: ArchitectureSnapshot,
    target: ArchitectureSnapshot,
) -> Finding | None:
    """在父类方法删除后识别子类新增的近似替代实现。"""
    child_before = base.classes[edge.child]
    child_after = target.classes.get(edge.child)
    parent_before = base.classes[edge.parent]
    parent_after = target.classes.get(edge.parent)
    if child_after is None:
        return None
    parent_method = parent_before.methods.get(edge.method)
    if parent_method is None or (parent_after is not None and edge.method in parent_after.methods):
        return None
    new_methods = [
        method for name, method in child_after.methods.items() if name not in child_before.methods
    ]
    for candidate in new_methods:
        name_overlap = _name_parts(edge.method) & _name_parts(candidate.name)
        similarity = _behavior_similarity(parent_method, candidate)
        if not name_overlap or similarity < _CAPABILITY_SIMILARITY_THRESHOLD:
            continue
        return Finding(
            code="QG163",
            severity="critical",
            confidence="high",
            path=child_after.path,
            line=candidate.line,
            column=1,
            symbol=f"{child_after.qualname}.{candidate.name}",
            message=(
                f"父类公共能力 `{parent_before.name}.{edge.method}` 的调用消失后，"
                f"子类以 `{candidate.name}` 重新实现了近似能力。"
            ),
            suggestion="恢复正确的公共所有者，删除子类替代实现和由此产生的影子状态。",
            evidence={
                "parent_method": f"{parent_before.name}.{edge.method}",
                "child_method": f"{child_after.qualname}.{candidate.name}",
                "name_overlap": sorted(name_overlap),
                "behavior_similarity": round(similarity, 3),
            },
        )
    return None


def _lost_parent_call_findings(
    base: ArchitectureSnapshot,
    target: ArchitectureSnapshot,
) -> list[Finding]:
    """报告基线继承能力调用链真正消失及其子类替代实现。"""
    findings: list[Finding] = []
    target_edges = {
        (edge.child, edge.caller, edge.parent, edge.method) for edge in target.parent_calls
    }
    capability_callers: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)
    base_typed_calls = {
        (item.owner, item.caller, item.receiver_type, item.method)
        for item in base.typed_receiver_calls
    }
    new_typed_calls = {
        (item.owner, item.caller, item.receiver_type, item.method)
        for item in target.typed_receiver_calls
        if (item.owner, item.caller, item.receiver_type, item.method) not in base_typed_calls
    }
    for target_edge in target.parent_calls:
        capability = (target_edge.child, target_edge.parent, target_edge.method)
        capability_callers[capability].add(target_edge.caller)
    for edge in sorted(base.parent_calls, key=lambda item: (item.path, item.line, item.method)):
        edge_key = (edge.child, edge.caller, edge.parent, edge.method)
        if edge_key in target_edges:
            continue
        child_after = target.classes.get(edge.child)
        if child_after is None:
            continue
        direct_callers = capability_callers[(edge.child, edge.parent, edge.method)]
        caller_survives = edge.caller in child_after.methods
        if not caller_survives and direct_callers:
            # The historical caller itself no longer exists, so preserving its edge identity
            # would force a dead compatibility wrapper to remain. The exact child -> exact
            # parent capability still has a target caller, which proves caller consolidation
            # rather than capability loss.
            continue
        pending = [edge.caller] if caller_survives else []
        visited: set[str] = set()
        capability_reachable = False
        while pending:
            caller = pending.pop()
            if caller in visited:
                continue
            visited.add(caller)
            if caller in direct_callers:
                capability_reachable = True
                break
            method = child_after.methods[caller]
            pending.extend(
                name
                for name in method.receiver_call_names
                if name in child_after.methods and name not in visited
            )
        if capability_reachable:
            continue
        parent_before = base.classes[edge.parent]
        explicit_owner_migration = any(
            owner != edge.child
            and receiver_type == parent_before.name
            and method_name == edge.method
            for owner, _caller, receiver_type, method_name in new_typed_calls
        )
        if edge.caller in child_after.methods and explicit_owner_migration:
            continue
        caller_after = child_after.methods.get(edge.caller)
        line = caller_after.line if caller_after is not None else child_after.line
        findings.append(
            Finding(
                code="QG162",
                severity="warning",
                confidence="high",
                path=child_after.path,
                line=line,
                column=1,
                symbol=f"{child_after.qualname}.{edge.caller}",
                message=(
                    f"Git 基线中的继承调用边 `{child_after.qualname}.{edge.caller}` → "
                    f"`{parent_before.name}.{edge.method}` 已消失。"
                ),
                suggestion=(
                    "恢复原调用者到父类能力的直接或同类 helper 可达调用链，"
                    "或披露功能删除/所有者迁移后的完整证据。"
                ),
                evidence={
                    "child": child_after.qualname,
                    "caller": edge.caller,
                    "parent": parent_before.name,
                    "method": edge.method,
                    "remaining_direct_callers": sorted(direct_callers),
                },
            )
        )
        replacement = _replacement_finding(edge, base, target)
        if replacement is not None:
            findings.append(replacement)
    return findings


def _duplicate_override_findings(
    base: ArchitectureSnapshot,
    target: ArchitectureSnapshot,
) -> list[Finding]:
    """识别子类新增的完全重复 override。"""
    findings: list[Finding] = []
    for key, child in target.classes.items():
        before_child = base.classes.get(key)
        if before_child is None:
            continue
        for base_name in child.bases:
            if base_name not in target.simple_names:
                continue
            parent_keys = target.simple_names[base_name]
            if len(parent_keys) != 1:
                continue
            parent = target.classes[parent_keys[0]]
            for name, method in child.methods.items():
                if name not in parent.methods:
                    continue
                parent_method = parent.methods[name]
                if (
                    name not in before_child.methods
                    and method.fingerprint == parent_method.fingerprint
                ):
                    findings.append(
                        Finding(
                            code="QG165",
                            severity="critical",
                            confidence="high",
                            path=child.path,
                            line=method.line,
                            column=1,
                            symbol=f"{child.qualname}.{name}",
                            message=f"子类新增方法与父类 `{parent.name}.{name}` 的实现完全重复。",
                            suggestion="删除重复 override，直接复用父类实现；确需覆盖时提供真实行为差异。",
                            evidence={"parent": parent.name, "method": name},
                        )
                    )
    return findings


def _shadow_state_findings(
    base: ArchitectureSnapshot,
    target: ArchitectureSnapshot,
) -> list[Finding]:
    """识别子类新增且与父类同源的影子状态。"""
    findings: list[Finding] = []
    for key, child in target.classes.items():
        before_child = base.classes.get(key)
        if before_child is None:
            continue
        for base_name in child.bases:
            if base_name not in target.simple_names:
                continue
            parent_keys = target.simple_names[base_name]
            if len(parent_keys) != 1:
                continue
            parent = target.classes[parent_keys[0]]
            for attr, source in child.assignments.items():
                if attr in before_child.assignments and before_child.assignments[attr] == source:
                    continue
                for parent_attr, parent_source in parent.assignments.items():
                    if (
                        attr == parent_attr
                        or source != parent_source
                        or not _is_shared_state_source(source)
                    ):
                        continue
                    init_method = child.methods.get("__init__")
                    findings.append(
                        Finding(
                            code="QG167",
                            severity="critical",
                            confidence="high",
                            path=child.path,
                            line=init_method.line if init_method is not None else child.line,
                            column=1,
                            symbol=f"{child.qualname}.{attr}",
                            message=(
                                f"子类新增字段 `{attr}` 与父类 `{parent.name}.{parent_attr}` "
                                f"保存同一来源 `{source}`，疑似形成影子状态。"
                            ),
                            suggestion="复用父类状态或明确唯一所有者，避免两套状态发生漂移。",
                            evidence={
                                "child_field": attr,
                                "parent_field": parent_attr,
                                "source": source,
                            },
                        )
                    )
                    break
    return findings


def _sibling_common_findings(
    base: ArchitectureSnapshot,
    target: ArchitectureSnapshot,
) -> list[Finding]:
    """识别多个兄弟子类新增的完全相同实现。"""
    groups: dict[tuple[str, str, str], list[tuple[ClassSnapshot, MethodSnapshot]]] = {}
    for key, child in target.classes.items():
        before_child = base.classes.get(key)
        if before_child is None:
            continue
        for base_name in child.bases:
            for name, method in child.methods.items():
                if name in before_child.methods or name.startswith("__"):
                    continue
                group_key = (base_name, name, method.fingerprint)
                if group_key not in groups:
                    groups[group_key] = []
                groups[group_key].append((child, method))
    findings: list[Finding] = []
    for (base_name, name, _fingerprint), implementations in groups.items():
        if len(implementations) < _MIN_SIBLING_IMPLEMENTATIONS:
            continue
        first, first_method = implementations[0]
        names = sorted(item.qualname for item, _ in implementations)
        findings.append(
            Finding(
                code="QG166",
                severity="critical",
                confidence="high",
                path=first.path,
                line=first_method.line,
                column=1,
                symbol=f"{first.qualname}.{name}",
                message=(
                    f"多个 `{base_name}` 子类新增了完全相同的 `{name}` 实现：{', '.join(names)}。"
                ),
                suggestion="检查是否应上提父类、引入中间基类或提取唯一公共协作者。",
                evidence={"base": base_name, "method": name, "children": names},
            )
        )
    return findings


def compare_git_architecture(
    root: Path,
    config: GuardConfig,
    revision: str = "HEAD",
    base_analysis: RepositoryAnalysisSnapshot | None = None,
    target_analysis: RepositoryAnalysisSnapshot | None = None,
) -> list[Finding]:
    """
    比较 Git 基线与工作区的继承、方法所有者和状态归属。

    Args:
        root: Git 仓库根目录。
        config: 文件排除和项目范围配置。
        revision: Git 基线 commit-ish。
        base_analysis: 可选的基线统一源码/AST 快照。
        target_analysis: 可选的工作树统一源码/AST 快照。

    Returns:
        去重并稳定排序的架构差分发现。
    """
    base_sources = (
        base_analysis.sources()
        if base_analysis is not None
        else _base_sources(root, revision, config)
    )
    target_sources = (
        target_analysis.sources()
        if target_analysis is not None
        else _worktree_sources(root, config)
    )
    base = _snapshot_from_sources(
        base_sources,
        base_analysis.trees() if base_analysis is not None else None,
    )
    target = _snapshot_from_sources(
        target_sources,
        target_analysis.trees() if target_analysis is not None else None,
    )
    findings = [
        *_removed_inheritance_findings(base, target),
        *_lost_parent_call_findings(base, target),
        *_duplicate_override_findings(base, target),
        *_shadow_state_findings(base, target),
        *_sibling_common_findings(base, target),
    ]
    unique = {finding.fingerprint: finding for finding in findings}
    return sorted(
        unique.values(),
        key=lambda item: (item.path, item.line, item.code, item.symbol),
    )
