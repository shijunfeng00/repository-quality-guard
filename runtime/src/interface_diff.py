from __future__ import annotations

import ast
import hashlib
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .config import GuardConfig
from .docstrings import definition_code_lines
from .interface_visibility import (
    ImportAlias,
    filter_interface_symbols,
    resolve_import_aliases,
)
from .model import (
    InterfaceChange,
    InterfaceDiffReport,
    InterfaceKind,
    InterfaceParameter,
    InterfaceSymbol,
)
from .project_contracts import validate_project_contracts
from .project_profiles import ProjectProfile

_CHANGE_ORDER = {"removed": 0, "modified": 1, "added": 2}
_MAX_DECLARATION_TEXT = 240
_SETATTR_MIN_ARGS = 2
_SETATTR_VALUE_ARGS = 3
_KIND_ORDER = {
    "file": 0,
    "class": 1,
    "function": 2,
    "global_variable": 3,
    "method": 4,
    "member_variable": 5,
}


@dataclass(slots=True, frozen=True)
class _VariableCandidate:
    """
    保存尚未归并为接口符号的变量声明候选。

    多次声明同名变量时，优先保留带注解、类体声明或构造函数声明。
    """

    symbol: InterfaceSymbol
    priority: tuple[int, int, int]


class _NestedDefinitionCollector(ast.NodeVisitor):
    """收集一个函数体内直接可达的嵌套函数和嵌套类。"""

    def __init__(self) -> None:
        """
        初始化空的嵌套定义节点集合。

        Returns:
            None。
        """
        self.nodes: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """
        记录嵌套同步函数，不越过该作用域继续遍历。

        Args:
            node: 嵌套同步函数节点。

        Returns:
            None。
        """
        self.nodes.append(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """
        记录嵌套异步函数，不越过该作用域继续遍历。

        Args:
            node: 嵌套异步函数节点。

        Returns:
            None。
        """
        self.nodes.append(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """
        记录嵌套类，不越过该作用域继续遍历。

        Args:
            node: 嵌套类节点。

        Returns:
            None。
        """
        self.nodes.append(node)


def _nested_definitions(
    body: list[ast.stmt],
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, ...]:
    """返回函数体中的直接嵌套定义，包括条件和异常分支中的定义。"""
    collector = _NestedDefinitionCollector()
    for statement in body:
        collector.visit(statement)
    return tuple(collector.nodes)


class _InstanceAttributeCollector(ast.NodeVisitor):
    """
    收集单个成员函数直接写入的实例或类属性。

    收集器不会进入嵌套函数和嵌套类，避免把内部闭包状态误认为外层类成员。
    """

    def __init__(self, receiver: str, class_name: str, path: str, in_constructor: bool) -> None:
        """
        初始化成员属性收集器。

        Args:
            receiver: 当前成员函数的实例或类接收者参数名。
            class_name: 当前类的限定名称。
            path: 当前 Python 文件路径。
            in_constructor: 当前成员函数是否为构造函数。

        Returns:
            None。
        """
        self.receiver = receiver
        self.class_name = class_name
        self.path = path
        self.in_constructor = in_constructor
        self.candidates: list[_VariableCandidate] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """
        阻止进入嵌套同步函数。

        Args:
            node: 嵌套同步函数节点。

        Returns:
            None。
        """

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """
        阻止进入嵌套异步函数。

        Args:
            node: 嵌套异步函数节点。

        Returns:
            None。
        """

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """
        阻止进入嵌套类。

        Args:
            node: 嵌套类节点。

        Returns:
            None。
        """

    def visit_Assign(self, node: ast.Assign) -> None:
        """
        收集普通属性赋值。

        Args:
            node: 普通赋值节点。

        Returns:
            None。
        """
        for target in node.targets:
            self._collect_target(target, "", _render(node.value), node.lineno)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """
        收集带类型注解的属性赋值。

        Args:
            node: 注解赋值节点。

        Returns:
            None。
        """
        self._collect_target(
            node.target,
            _render(node.annotation),
            _render(node.value),
            node.lineno,
        )
        if node.value is not None:
            self.generic_visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """
        收集增量写入的属性。

        Args:
            node: 增量赋值节点。

        Returns:
            None。
        """
        self._collect_target(node.target, "", f"<augassign:{type(node.op).__name__}>", node.lineno)
        self.generic_visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        """
        收集 ``setattr(receiver, literal_name, value)`` 形式的成员写入。

        Args:
            node: 函数调用节点。

        Returns:
            None。
        """
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= _SETATTR_MIN_ARGS
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == self.receiver
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            value = _render(node.args[2]) if len(node.args) >= _SETATTR_VALUE_ARGS else ""
            self._add(node.args[1].value, "", value, node.lineno)
        self.generic_visit(node)

    def _collect_target(self, target: ast.expr, annotation: str, value: str, line: int) -> None:
        """
        从赋值目标中提取接收者属性。

        Args:
            target: 待解析的赋值目标。
            annotation: 属性类型注解文本。
            value: 属性初始值或写入表达式文本。
            line: 属性写入所在行号。

        Returns:
            None。
        """
        for item in _flatten_targets(target):
            if (
                isinstance(item, ast.Attribute)
                and isinstance(item.value, ast.Name)
                and item.value.id == self.receiver
            ):
                self._add(item.attr, annotation, value, line)

    def _add(self, name: str, annotation: str, value: str, line: int) -> None:
        """
        保存一个成员变量候选。

        Args:
            name: 成员变量名称。
            annotation: 成员变量类型注解。
            value: 成员变量初始值或写入表达式。
            line: 声明或首次写入所在行号。

        Returns:
            None。
        """
        symbol = InterfaceSymbol(
            kind="member_variable",
            path=self.path,
            qualname=f"{self.class_name}.{name}",
            line=line,
            annotation=annotation,
            value=value,
            storage="instance",
            code_lines=1,
        )
        self.candidates.append(
            _VariableCandidate(
                symbol=symbol,
                priority=(int(annotation != ""), int(self.in_constructor), -line),
            )
        )


class PythonInterfaceExtractor:
    """
    从 Python 源码中提取可比较的静态接口声明。

    默认覆盖模块文件、顶层类和函数、全局变量、成员函数和成员变量。
    """

    def __init__(self, path: str, source: str, tree: ast.Module | None = None) -> None:
        """
        初始化单文件接口提取器。

        Args:
            path: 相对仓库根目录的 Python 文件路径。
            source: 对应 Git 快照中的源码文本。
            tree: 可选的已解析 AST；存在时不重复执行 ``ast.parse``。

        Returns:
            None。
        """
        self.path = path
        self.source = source
        self.tree = tree
        self.symbols: dict[str, InterfaceSymbol] = {}
        self._variants: dict[tuple[str, str], int] = defaultdict(int)
        self._variables: dict[tuple[str, str], _VariableCandidate] = {}
        self.import_aliases: list[ImportAlias] = []
        self.loaded_names: set[str] = set()

    def extract(self) -> tuple[dict[str, InterfaceSymbol], tuple[str, ...]]:
        """
        解析源码并返回接口符号和语法错误。

        Returns:
            以稳定符号键索引的接口字典，以及无法解析时的错误列表。
        """
        tree = self.tree
        if tree is None:
            try:
                tree = ast.parse(self.source, filename=self.path, type_comments=True)
            except SyntaxError as error:
                message = f"{self.path}:{error.lineno or 1}:{error.offset or 1}: {error.msg}"
                return {}, (message,)
        self.loaded_names = {
            item.id
            for item in ast.walk(tree)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
        }
        self._add(InterfaceSymbol(kind="file", path=self.path, qualname=self.path, line=1))
        for node in _scope_statements(tree.body):
            self._extract_module_statement(node)
        for candidate in self._variables.values():
            self.symbols[candidate.symbol.key] = candidate.symbol
        return self.symbols, ()

    def _extract_module_statement(self, node: ast.stmt) -> None:
        """
        提取单个模块作用域声明。

        Args:
            node: 已展开条件作用域后的模块语句。

        Returns:
            None。
        """
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._add_callable(node, "function", node.name)
            return
        if isinstance(node, ast.ClassDef):
            self._process_class(node, node.name)
            return
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            self._collect_declared_variables(node, "", "global_variable", "module", 3)
            return
        if isinstance(node, ast.ImportFrom):
            self._collect_import_from(node)

    def _collect_import_from(self, node: ast.ImportFrom) -> None:
        """记录可在模块命名空间继续访问的仓库内导入别名。

        Args:
            node: 模块级 ``from ... import ...`` 节点。

        Returns:
            None。
        """
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            self.import_aliases.append(
                ImportAlias(
                    path=self.path,
                    module=module,
                    imported_name=alias.name,
                    local_name=alias.asname or alias.name,
                    level=node.level,
                    line=node.lineno,
                    used_in_module=(alias.asname or alias.name) in self.loaded_names,
                )
            )

    def _process_class(self, node: ast.ClassDef, qualname: str) -> None:
        """
        提取类声明、成员函数、嵌套类和成员变量。

        Args:
            node: 类定义节点。
            qualname: 当前类的限定名称。

        Returns:
            None。
        """
        bases = tuple(_render(item) for item in node.bases) + tuple(
            f"{item.arg}={_render(item.value)}" for item in node.keywords
        )
        self._add(
            InterfaceSymbol(
                kind="class",
                path=self.path,
                qualname=qualname,
                line=node.lineno,
                decorators=tuple(_render(item) for item in node.decorator_list),
                bases=bases,
                code_lines=definition_code_lines(node),
            )
        )
        for child in _scope_statements(node.body):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_name = f"{qualname}.{child.name}"
                self._add_callable(child, "method", method_name)
                self._collect_instance_variables(child, qualname)
            elif isinstance(child, ast.ClassDef):
                self._process_class(child, f"{qualname}.{child.name}")
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                self._collect_declared_variables(
                    child,
                    qualname,
                    "member_variable",
                    "class",
                    4,
                )
                self._collect_slots(child, qualname)

    def _add_callable(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        kind: InterfaceKind,
        qualname: str,
    ) -> None:
        """
        将函数或成员函数转换为接口符号。

        Args:
            node: 同步或异步函数定义节点。
            kind: 接口种类，应为 function 或 method。
            qualname: 函数在当前文件中的限定名称。

        Returns:
            None。
        """
        parameters = _parameters(node.args)
        symbol = InterfaceSymbol(
            kind=kind,
            path=self.path,
            qualname=qualname,
            line=node.lineno,
            parameters=parameters,
            return_type=_render(node.returns),
            decorators=tuple(_render(item) for item in node.decorator_list),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            code_lines=definition_code_lines(node),
        )
        self._add(symbol)
        for child in _nested_definitions(node.body):
            nested_name = f"{qualname}.<locals>.{child.name}"
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_callable(child, "function", nested_name)
            else:
                self._process_class(child, nested_name)

    def _collect_declared_variables(
        self,
        node: ast.Assign | ast.AnnAssign,
        owner: str,
        kind: InterfaceKind,
        storage: str,
        source_priority: int,
    ) -> None:
        """
        收集模块或类体中的变量声明。

        Args:
            node: 普通赋值或注解赋值节点。
            owner: 变量所属类限定名称，模块变量为空字符串。
            kind: global_variable 或 member_variable。
            storage: module 或 class。
            source_priority: 当前声明形态的优先级。

        Returns:
            None。
        """
        if isinstance(node, ast.AnnAssign):
            targets = _flatten_targets(node.target)
            annotation = _render(node.annotation)
            value = _render(node.value)
        else:
            targets = [item for target in node.targets for item in _flatten_targets(target)]
            annotation = node.type_comment or ""
            value = _render(node.value)
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            qualname = f"{owner}.{target.id}" if owner else target.id
            symbol = InterfaceSymbol(
                kind=kind,
                path=self.path,
                qualname=qualname,
                line=node.lineno,
                annotation=annotation,
                value=value,
                storage=storage,
                code_lines=1,
                exports=(
                    tuple(item.value for item in node.value.elts)
                    if target.id == "__all__"
                    and isinstance(node.value, (ast.List, ast.Tuple, ast.Set))
                    and all(
                        isinstance(item, ast.Constant) and isinstance(item.value, str)
                        for item in node.value.elts
                    )
                    else ()
                ),
            )
            self._upsert_variable(
                symbol,
                priority=(int(annotation != ""), source_priority, -node.lineno),
            )

    def _collect_instance_variables(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        class_name: str,
    ) -> None:
        """
        收集成员函数直接写入的实例属性。

        Args:
            node: 当前成员函数节点。
            class_name: 所属类的限定名称。

        Returns:
            None。
        """
        if any(
            _render(item.func if isinstance(item, ast.Call) else item).endswith("staticmethod")
            for item in node.decorator_list
        ):
            return
        positional = [*node.args.posonlyargs, *node.args.args]
        if not positional:
            return
        collector = _InstanceAttributeCollector(
            receiver=positional[0].arg,
            class_name=class_name,
            path=self.path,
            in_constructor=node.name in {"__init__", "__new__"},
        )
        for statement in node.body:
            collector.visit(statement)
        for candidate in collector.candidates:
            self._upsert_variable(candidate.symbol, candidate.priority)

    def _collect_slots(self, node: ast.Assign | ast.AnnAssign, class_name: str) -> None:
        """
        把字面量 ``__slots__`` 中声明的名称纳入成员变量。

        Args:
            node: 类体中的赋值节点。
            class_name: 所属类的限定名称。

        Returns:
            None。
        """
        targets = (
            _flatten_targets(node.target)
            if isinstance(node, ast.AnnAssign)
            else [item for target in node.targets for item in _flatten_targets(target)]
        )
        if not any(isinstance(item, ast.Name) and item.id == "__slots__" for item in targets):
            return
        value = node.value
        if not isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            return
        for item in value.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                continue
            symbol = InterfaceSymbol(
                kind="member_variable",
                path=self.path,
                qualname=f"{class_name}.{item.value}",
                line=node.lineno,
                value="<slot>",
                storage="instance",
                code_lines=1,
            )
            self._upsert_variable(symbol, priority=(0, 2, -node.lineno))

    def _upsert_variable(
        self,
        symbol: InterfaceSymbol,
        priority: tuple[int, int, int],
    ) -> None:
        """
        按声明质量归并同名变量候选。

        Args:
            symbol: 待保存的变量接口符号。
            priority: 注解、声明来源和源码位置组成的比较优先级。

        Returns:
            None。
        """
        key = (symbol.kind, symbol.qualname)
        current = self._variables.get(key)
        candidate = _VariableCandidate(symbol=symbol, priority=priority)
        if current is None or candidate.priority > current.priority:
            self._variables[key] = candidate

    def _add(self, symbol: InterfaceSymbol) -> None:
        """
        保存接口符号并为重载或重复定义分配稳定序号。

        Args:
            symbol: 待保存的接口符号。

        Returns:
            None。
        """
        counter_key = (symbol.kind, symbol.qualname)
        variant = self._variants[counter_key]
        self._variants[counter_key] += 1
        if variant:
            symbol = InterfaceSymbol(
                kind=symbol.kind,
                path=symbol.path,
                qualname=symbol.qualname,
                line=symbol.line,
                variant=variant,
                parameters=symbol.parameters,
                return_type=symbol.return_type,
                annotation=symbol.annotation,
                value=symbol.value,
                storage=symbol.storage,
                decorators=symbol.decorators,
                bases=symbol.bases,
                is_async=symbol.is_async,
                exports=symbol.exports,
                code_lines=symbol.code_lines,
                exposure=symbol.exposure,
            )
        self.symbols[symbol.key] = symbol


class GitWorktreeInterfaceComparator:
    """
    比较指定 Git 提交与当前工作区中的 Python 接口。

    基准源码直接从指定提交读取，目标源码直接从当前工作区读取，因此未暂存
    修改、新增未跟踪 Python 文件和工作区删除都会进入接口报告。默认报告全部
    函数、方法和变量；只有显式开启公开契约模式才收窄范围。
    """

    def __init__(
        self,
        root: Path,
        config: GuardConfig,
        base_revision: str = "HEAD",
        include_private: bool = True,
        target: str = "worktree",
        base_analysis: RepositoryAnalysisSnapshot | None = None,
        target_analysis: RepositoryAnalysisSnapshot | None = None,
        profile: ProjectProfile | None = None,
    ) -> None:
        """
        初始化 Git 工作区接口比较器。

        Args:
            root: Git 仓库根目录或其子目录。
            config: 用于排除生成目录和测试目录的质量检查配置。
            base_revision: 用作旧接口快照的 Git commit-ish。
            include_private: 是否把全部私有符号也纳入接口比较；默认全量。
            target: 目标快照，worktree 表示当前工作区，staged 表示 Git 暂存区。
            base_analysis: 可选的 Git 基线统一源码/AST 快照。
            target_analysis: 可选的目标统一源码/AST 快照。
            profile: 已由 CLI 解析完成的项目 Profile；不得在 comparator 内按名称重载。

        Returns:
            None。
        """
        self.root = root.resolve()
        self.config = config
        self.base_revision = base_revision
        self.include_private = include_private
        self.base_analysis = base_analysis
        self.target_analysis = target_analysis
        self.profile = profile
        if target not in {"worktree", "staged"}:
            raise ValueError("target 必须是 worktree 或 staged")
        self.target = target

    def compare(self) -> InterfaceDiffReport:
        """
        比较指定提交和当前工作区并生成接口差异报告。

        Returns:
            包含新增、删除、修改接口和解析错误的差异报告。
        """
        root_result = _run_git(self.root, ["rev-parse", "--show-toplevel"])
        if root_result.returncode != 0:
            message = root_result.stderr.decode(errors="replace").strip()
            raise RuntimeError(message or "目标目录不是 Git 仓库")
        repository_root = Path(root_result.stdout.decode().strip()).resolve()
        scope = self.root.relative_to(repository_root).as_posix()
        scope_prefix = "" if scope == "." else scope
        verified_base = _run_git(
            repository_root,
            ["rev-parse", "--verify", f"{self.base_revision}^{{commit}}"],
        )
        if verified_base.returncode != 0:
            if self.base_revision == "HEAD":
                base_sources: dict[str, str] = {}
                base_label = "EMPTY_TREE"
            else:
                message = verified_base.stderr.decode(errors="replace").strip()
                raise RuntimeError(message or f"无法解析 Git 基准提交: {self.base_revision}")
        else:
            base_sources = (
                self.base_analysis.sources()
                if self.base_analysis is not None
                else self._read_snapshot(repository_root, self.base_revision, scope_prefix)
            )
            base_label = self.base_revision
        target_sources = (
            self.target_analysis.sources()
            if self.target_analysis is not None
            else (
                self._read_index(repository_root, scope_prefix)
                if self.target == "staged"
                else self._read_worktree(repository_root, scope_prefix)
            )
        )
        base_trees = self.base_analysis.trees() if self.base_analysis is not None else None
        target_trees = (
            self.target_analysis.trees() if self.target_analysis is not None else None
        )
        base_symbols, base_errors = self._extract_snapshot(base_sources, base_trees)
        target_symbols, target_errors = self._extract_snapshot(target_sources, target_trees)
        changes = _compare_symbols(base_symbols, target_symbols)
        contract_findings = validate_project_contracts(
            self.profile,
            base_sources,
            target_sources,
            target_symbols,
            base_trees=base_trees,
            target_trees=target_trees,
        )
        return InterfaceDiffReport(
            base=base_label,
            target="INDEX" if self.target == "staged" else "WORKTREE",
            changes=tuple(changes),
            base_files=len(base_sources),
            target_files=len(target_sources),
            errors=(*base_errors, *target_errors),
            contract_findings=contract_findings,
            api_scope="all" if self.include_private else "public",
            private_min_lines=self.config.api_private_min_lines,
        )

    def _read_snapshot(
        self,
        repository_root: Path,
        snapshot: str,
        scope_prefix: str,
    ) -> dict[str, str]:
        """
        从 Git 提交读取全部候选 Python 源码。

        Args:
            repository_root: Git 仓库根目录。
            snapshot: Git commit-ish。
            scope_prefix: 目标扫描目录相对 Git 仓库根目录的路径前缀。

        Returns:
            相对路径到 UTF-8 源码文本的映射。
        """
        listed = _run_git(repository_root, ["ls-tree", "-r", "-z", "--name-only", snapshot])
        if listed.returncode != 0:
            raise RuntimeError(
                listed.stderr.decode(errors="replace").strip() or "无法列出 Git 文件"
            )
        paths = sorted(
            path
            for path in listed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
            if path.endswith(".py")
        )
        result: dict[str, str] = {}
        for path in paths:
            if scope_prefix and not path.startswith(f"{scope_prefix}/"):
                continue
            if any(fnmatch(path, pattern) for pattern in self.config.exclude):
                continue
            spec = f"{snapshot}:{path}"
            blob = _run_git(repository_root, ["show", spec])
            if blob.returncode != 0:
                message = blob.stderr.decode(errors="replace").strip()
                raise RuntimeError(message or f"无法读取 Git 对象: {spec}")
            try:
                result[path] = blob.stdout.decode("utf-8")
            except UnicodeDecodeError as error:
                raise RuntimeError(f"Python 源码不是 UTF-8: {path}") from error
        return result

    def _read_index(
        self,
        repository_root: Path,
        scope_prefix: str,
    ) -> dict[str, str]:
        """
        从 Git 暂存区读取全部候选 Python 源码。

        Args:
            repository_root: Git 仓库根目录。
            scope_prefix: 目标扫描目录相对 Git 仓库根目录的路径前缀。

        Returns:
            相对路径到 UTF-8 源码文本的映射。
        """
        listed = _run_git(repository_root, ["ls-files", "--cached", "-z"])
        if listed.returncode != 0:
            raise RuntimeError(
                listed.stderr.decode(errors="replace").strip() or "无法列出 Git 暂存区文件"
            )
        paths = sorted(
            path
            for path in listed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
            if path.endswith(".py")
        )
        result: dict[str, str] = {}
        for path in paths:
            if scope_prefix and not path.startswith(f"{scope_prefix}/"):
                continue
            if any(fnmatch(path, pattern) for pattern in self.config.exclude):
                continue
            blob = _run_git(repository_root, ["show", f":{path}"])
            if blob.returncode != 0:
                continue
            try:
                result[path] = blob.stdout.decode("utf-8")
            except UnicodeDecodeError as error:
                raise RuntimeError(f"Python 源码不是 UTF-8: {path}") from error
        return result

    def _read_worktree(
        self,
        repository_root: Path,
        scope_prefix: str,
    ) -> dict[str, str]:
        """
        从当前工作区读取全部候选 Python 源码。

        Args:
            repository_root: Git 仓库根目录。
            scope_prefix: 目标扫描目录相对 Git 仓库根目录的路径前缀。

        Returns:
            相对路径到 UTF-8 源码文本的映射。
        """
        paths = self._list_worktree_paths(repository_root)
        result: dict[str, str] = {}
        for path in paths:
            if scope_prefix and not path.startswith(f"{scope_prefix}/"):
                continue
            if any(fnmatch(path, pattern) for pattern in self.config.exclude):
                continue
            file_path = repository_root / path
            if not file_path.is_file():
                continue
            try:
                result[path] = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise RuntimeError(f"Python 源码不是 UTF-8: {path}") from error
        return result

    def _list_worktree_paths(self, repository_root: Path) -> list[str]:
        """
        列出当前工作区中 Git 跟踪和未忽略的未跟踪 Python 文件路径。

        Args:
            repository_root: Git 仓库根目录。

        Returns:
            排序后的仓库相对 Python 文件路径列表。
        """
        result = _run_git(
            repository_root,
            ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.decode(errors="replace").strip() or "无法列出 Git 工作区文件"
            )
        paths = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        return sorted({path for path in paths if path.endswith(".py")})

    def _extract_snapshot(
        self,
        sources: dict[str, str],
        trees: dict[str, ast.Module] | None = None,
    ) -> tuple[dict[str, InterfaceSymbol], list[str]]:
        """
        从一个 Git 快照的源码集合提取全部接口符号。

        Args:
            sources: 文件路径到源码文本的映射。

        Returns:
            合并后的接口符号索引和语法解析错误列表。
        """
        symbols: dict[str, InterfaceSymbol] = {}
        errors: list[str] = []
        import_aliases: list[ImportAlias] = []
        for path, source in sorted(sources.items()):
            extractor = PythonInterfaceExtractor(
                path,
                source,
                tree=trees[path] if trees is not None and path in trees else None,
            )
            file_symbols, file_errors = extractor.extract()
            symbols.update(file_symbols)
            import_aliases.extend(extractor.import_aliases)
            errors.extend(file_errors)
        symbols = resolve_import_aliases(symbols, import_aliases, tuple(sources))
        symbols = filter_interface_symbols(
            symbols,
            sources,
            import_aliases,
            forced_symbols=set(self.config.forced_interface_symbols),
            include_private=self.include_private,
            private_min_lines=self.config.api_private_min_lines,
        )
        return symbols, errors


def _scope_statements(statements: list[ast.stmt]) -> list[ast.stmt]:
    """
    展开模块或类作用域中的条件和异常控制流语句。

    Args:
        statements: 当前模块或类体的直接语句列表。

    Returns:
        保留源码顺序的作用域级声明候选列表。
    """
    result: list[ast.stmt] = []
    pending = list(reversed(statements))
    while pending:
        statement = pending.pop()
        result.append(statement)
        if isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            pending.extend(reversed(statement.orelse))
            pending.extend(reversed(statement.body))
            continue
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            pending.extend(reversed(statement.body))
            continue
        if isinstance(statement, (ast.Try, ast.TryStar)):
            pending.extend(reversed(statement.finalbody))
            pending.extend(reversed(statement.orelse))
            for handler in reversed(statement.handlers):
                pending.extend(reversed(handler.body))
            pending.extend(reversed(statement.body))
            continue
        if isinstance(statement, ast.Match):
            for case in reversed(statement.cases):
                pending.extend(reversed(case.body))
    return result


def _run_git(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    """
    在指定目录执行 Git 命令并返回二进制结果。

    Args:
        root: Git 命令工作目录。
        arguments: 不含 git 可执行文件名的参数列表。

    Returns:
        未自动抛出异常的 CompletedProcess。
    """
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        check=False,
    )


def _render(node: ast.AST | None) -> str:
    """
    把 AST 节点归一化为稳定源码文本。

    Args:
        node: 待渲染节点；为空表示没有声明。

    Returns:
        使用 ast.unparse 生成的声明文本，或空字符串。
    """
    if node is None:
        return ""
    text = ast.unparse(node)
    if len(text) <= _MAX_DECLARATION_TEXT:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    preview = text[:120].replace("\n", "\\n")
    return f"<sha256:{digest}; chars={len(text)}; preview={preview!r}>"


def _flatten_targets(node: ast.expr) -> list[ast.expr]:
    """
    展开元组、列表和星号赋值目标。

    Args:
        node: 赋值目标节点。

    Returns:
        最终名称或属性节点列表。
    """
    if isinstance(node, (ast.Tuple, ast.List)):
        return [item for element in node.elts for item in _flatten_targets(element)]
    if isinstance(node, ast.Starred):
        return _flatten_targets(node.value)
    return [node]


def _parameters(arguments: ast.arguments) -> tuple[InterfaceParameter, ...]:
    """
    把 AST 参数对象转换为有序接口参数列表。

    Args:
        arguments: Python 函数参数节点。

    Returns:
        保留位置种类、类型注解和默认值的参数元组。
    """
    positional = [*arguments.posonlyargs, *arguments.args]
    default_offset = len(positional) - len(arguments.defaults)
    result: list[InterfaceParameter] = []
    for index, item in enumerate(positional):
        has_default = index >= default_offset
        default = arguments.defaults[index - default_offset] if has_default else None
        kind = "positional_only" if index < len(arguments.posonlyargs) else "positional_or_keyword"
        result.append(
            InterfaceParameter(
                name=item.arg,
                kind=kind,
                annotation=_render(item.annotation),
                has_default=has_default,
                default=_render(default),
            )
        )
    if arguments.vararg is not None:
        result.append(
            InterfaceParameter(
                name=arguments.vararg.arg,
                kind="var_positional",
                annotation=_render(arguments.vararg.annotation),
            )
        )
    for item, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
        result.append(
            InterfaceParameter(
                name=item.arg,
                kind="keyword_only",
                annotation=_render(item.annotation),
                has_default=default is not None,
                default=_render(default),
            )
        )
    if arguments.kwarg is not None:
        result.append(
            InterfaceParameter(
                name=arguments.kwarg.arg,
                kind="var_keyword",
                annotation=_render(arguments.kwarg.annotation),
            )
        )
    return tuple(result)


def _compare_symbols(
    before: dict[str, InterfaceSymbol],
    after: dict[str, InterfaceSymbol],
) -> list[InterfaceChange]:
    """
    比较两个接口符号快照。

    Args:
        before: Git 基线接口符号索引。
        after: 当前工作区接口符号索引。

    Returns:
        已按破坏性、文件和符号排序的接口变化列表。
    """
    changes: list[InterfaceChange] = []
    before_keys = set(before)
    after_keys = set(after)
    for key in before_keys - after_keys:
        symbol = before[key]
        changes.append(
            InterfaceChange(
                change="removed",
                kind=symbol.kind,
                path=symbol.path,
                symbol=symbol.qualname,
                before=symbol,
            )
        )
    for key in after_keys - before_keys:
        symbol = after[key]
        changes.append(
            InterfaceChange(
                change="added",
                kind=symbol.kind,
                path=symbol.path,
                symbol=symbol.qualname,
                after=symbol,
            )
        )
    for key in before_keys & after_keys:
        old = before[key]
        new = after[key]
        if old.comparable() == new.comparable():
            continue
        changes.append(
            InterfaceChange(
                change="modified",
                kind=new.kind,
                path=new.path,
                symbol=new.qualname,
                before=old,
                after=new,
                details=_change_details(old, new),
            )
        )
    return sorted(
        changes,
        key=lambda item: (
            _CHANGE_ORDER[item.change],
            item.path,
            _KIND_ORDER[item.kind],
            item.symbol,
            (item.after or item.before).variant if (item.after or item.before) is not None else 0,
        ),
    )


def _change_details(before: InterfaceSymbol, after: InterfaceSymbol) -> dict[str, Any]:
    """
    生成两个同名接口符号之间的字段级差异。

    Args:
        before: Git 基线中的接口声明。
        after: 当前工作区中的接口声明。

    Returns:
        仅包含实际变化字段的结构化差异字典。
    """
    details: dict[str, Any] = {}
    if before.parameters != after.parameters:
        before_by_name = {item.name: item for item in before.parameters}
        after_by_name = {item.name: item for item in after.parameters}
        before_names = [item.name for item in before.parameters]
        after_names = [item.name for item in after.parameters]
        details["parameters"] = {
            "before": [item.to_dict() for item in before.parameters],
            "after": [item.to_dict() for item in after.parameters],
            "added": [
                after_by_name[name].to_dict() for name in after_names if name not in before_by_name
            ],
            "removed": [
                before_by_name[name].to_dict() for name in before_names if name not in after_by_name
            ],
            "modified": [
                {
                    "name": name,
                    "before": before_by_name[name].to_dict(),
                    "after": after_by_name[name].to_dict(),
                }
                for name in before_names
                if name in after_by_name and before_by_name[name] != after_by_name[name]
            ],
            "order_changed": (
                before_names != after_names and set(before_names) == set(after_names)
            ),
        }
    comparable_fields = (
        ("return_type", before.return_type, after.return_type),
        ("annotation", before.annotation, after.annotation),
        ("value", before.value, after.value),
        ("storage", before.storage, after.storage),
        ("decorators", before.decorators, after.decorators),
        ("bases", before.bases, after.bases),
        ("is_async", before.is_async, after.is_async),
        ("exports", before.exports, after.exports),
    )
    for field_name, old_value, new_value in comparable_fields:
        if old_value != new_value:
            details[field_name] = {
                "before": list(old_value) if isinstance(old_value, tuple) else old_value,
                "after": list(new_value) if isinstance(new_value, tuple) else new_value,
            }
    return details
