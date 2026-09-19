from __future__ import annotations

import ast
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .config import GuardConfig, is_test_path
from .model import Finding

_BRANCH_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match)


@dataclass(slots=True, frozen=True)
class _FunctionInfo:
    """保存单个函数或方法的当前工作区结构事实。"""

    path: str
    symbol: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    owner: str
    private: bool
    decorators: tuple[str, ...]
    lines: int
    branches: int


class _DefinitionCollector(ast.NodeVisitor):
    """按接口差分使用的限定名称收集函数和方法。"""

    def __init__(self, path: str) -> None:
        """初始化定义收集器。

        Args:
            path: 当前 Python 文件的仓库相对路径。

        Returns:
            None。
        """
        self.path = path
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.functions: dict[str, _FunctionInfo] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """进入类作用域并收集其直接定义。

        Args:
            node: 当前类定义节点。

        Returns:
            None。
        """
        self.class_stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """收集同步函数。

        Args:
            node: 当前同步函数节点。

        Returns:
            None。
        """
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """收集异步函数。

        Args:
            node: 当前异步函数节点。

        Returns:
            None。
        """
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """把函数结构转换为稳定限定名称事实。"""
        qualname = ".".join([*self.class_stack, *self.function_stack, node.name])
        owner = qualname.rsplit(".", 1)[0] if "." in qualname else ""
        decorators = tuple(_dotted_name(item) for item in node.decorator_list if _dotted_name(item))
        branches = sum(isinstance(item, _BRANCH_NODES) for item in ast.walk(node))
        self.functions[qualname] = _FunctionInfo(
            path=self.path,
            symbol=qualname,
            node=node,
            owner=owner,
            private=node.name.startswith("_")
            and not (node.name.startswith("__") and node.name.endswith("__")),
            decorators=decorators,
            lines=max(1, (node.end_lineno or node.lineno) - node.lineno + 1),
            branches=branches,
        )
        self.function_stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.function_stack.pop()


class _LocalReferenceCollector(ast.NodeVisitor):
    """收集函数体中指向同文件定义的调用和非调用引用。"""

    def __init__(self, current: _FunctionInfo, known: set[str]) -> None:
        """初始化当前函数的局部引用收集器。

        Args:
            current: 当前待分析函数。
            known: 同文件内可解析的限定符号集合。

        Returns:
            None。
        """
        self.current = current
        self.known = known
        self.calls: set[str] = set()
        self.references: set[str] = set()
        self._call_function_nodes: set[int] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """只遍历当前同步函数，跳过嵌套定义。

        Args:
            node: 当前同步函数节点。

        Returns:
            None。
        """
        if node is self.current.node:
            for child in node.body:
                self.visit(child)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """只遍历当前异步函数，跳过嵌套定义。

        Args:
            node: 当前异步函数节点。

        Returns:
            None。
        """
        if node is self.current.node:
            for child in node.body:
                self.visit(child)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """跳过函数内部类，避免混入另一作用域。

        Args:
            node: 当前类定义节点。

        Returns:
            None。
        """
        return

    def visit_Call(self, node: ast.Call) -> None:
        """记录可解析到同文件定义的调用。

        Args:
            node: 当前调用表达式。

        Returns:
            None。
        """
        target = _resolve_target(node.func, self.current, self.known)
        if target:
            self.calls.add(target)
            self._call_function_nodes.add(id(node.func))
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            self.visit(argument)

    def visit_Name(self, node: ast.Name) -> None:
        """记录非调用位置的函数名称引用。

        Args:
            node: 当前名称节点。

        Returns:
            None。
        """
        if id(node) in self._call_function_nodes:
            return
        target = _resolve_target(node, self.current, self.known)
        if target:
            self.references.add(target)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """记录非调用位置的方法属性引用。

        Args:
            node: 当前属性节点。

        Returns:
            None。
        """
        if id(node) in self._call_function_nodes:
            return
        target = _resolve_target(node, self.current, self.known)
        if target:
            self.references.add(target)
        self.visit(node.value)


def _dotted_name(node: ast.AST) -> str:
    """返回名称或属性表达式的点分名称。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _resolve_target(node: ast.AST, current: _FunctionInfo, known: set[str]) -> str:
    """把局部调用表达式解析为同文件限定符号。"""
    if isinstance(node, ast.Name):
        candidates = [
            f"{current.owner}.{node.id}" if current.owner else node.id,
            node.id,
        ]
    elif isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        if base in {"self", "cls"}:
            candidates = [f"{current.owner}.{node.attr}" if current.owner else node.attr]
        elif current.owner and base == current.owner.rsplit(".", 1)[-1]:
            candidates = [f"{current.owner}.{node.attr}"]
        else:
            candidates = [f"{base}.{node.attr}" if base else node.attr]
    else:
        return ""
    return next((candidate for candidate in candidates if candidate in known), "")


def _load_functions(
    root: Path,
    paths: set[str],
    analysis_snapshot: RepositoryAnalysisSnapshot | None = None,
) -> dict[tuple[str, str], _FunctionInfo]:
    """解析发生生产接口变化的 Python 文件。"""
    result: dict[tuple[str, str], _FunctionInfo] = {}
    for relative in sorted(paths):
        path = root / relative
        if not path.is_file() or path.suffix != ".py":
            continue
        unit = analysis_snapshot.unit(relative) if analysis_snapshot is not None else None
        if unit is not None:
            tree = unit.tree
            if tree is None:
                continue
        else:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
        collector = _DefinitionCollector(relative)
        collector.visit(tree)
        result.update(((relative, symbol), item) for symbol, item in collector.functions.items())
    return result


def _call_graph(
    functions: dict[tuple[str, str], _FunctionInfo],
) -> tuple[
    dict[tuple[str, str], set[tuple[str, str]]],
    dict[tuple[str, str], set[tuple[str, str]]],
    dict[tuple[str, str], int],
]:
    """构造同文件函数调用边、反向调用边和非调用引用数量。"""
    outgoing: dict[tuple[str, str], set[tuple[str, str]]] = {key: set() for key in functions}
    incoming: dict[tuple[str, str], set[tuple[str, str]]] = {key: set() for key in functions}
    references: dict[tuple[str, str], int] = dict.fromkeys(functions, 0)
    by_path: dict[str, set[str]] = {}
    for path, symbol in functions:
        symbols = by_path.get(path)
        if symbols is None:
            symbols = set()
            by_path[path] = symbols
        symbols.add(symbol)
    for key, info in functions.items():
        known = by_path[info.path]
        collector = _LocalReferenceCollector(info, known)
        collector.visit(info.node)
        for target in collector.calls:
            target_key = (info.path, target)
            if target_key not in functions or target_key == key:
                continue
            outgoing[key].add(target_key)
            incoming[target_key].add(key)
        for target in collector.references:
            target_key = (info.path, target)
            if target_key in functions and target_key != key:
                references[target_key] += 1
    return outgoing, incoming, references


def _eligible_helper(
    key: tuple[str, str],
    info: _FunctionInfo,
    incoming: dict[tuple[str, str], set[tuple[str, str]]],
    references: dict[tuple[str, str], int],
) -> bool:
    """判断当前定义是否属于连续单调用私有 helper 候选。

    Args:
        key: 当前函数的文件与限定名称键。
        info: 当前函数结构事实。
        incoming: 反向调用边。
        references: 非调用引用数量。

    Returns:
        私有 helper 只有一个静态调用方、没有其他引用且没有语义装饰器时返回 True。
    """
    allowed_decorators = {"staticmethod", "classmethod"}
    return (
        info.private
        and set(info.decorators) <= allowed_decorators
        and len(incoming[key]) == 1
        and references[key] == 0
    )


def _production_python_paths(root: Path, config: GuardConfig) -> set[str]:
    """返回应纳入单调用链分析的生产 Python 文件。

    Args:
        root: 当前代码快照根目录。
        config: 文件排除与测试范围配置。

    Returns:
        仓库相对路径集合。
    """
    result: set[str] = set()
    for path in root.rglob("*.py"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if is_test_path(relative):
            continue
        if any(fnmatch(relative, pattern) for pattern in config.exclude):
            continue
        result.add(relative)
    return result


def single_use_chain_findings(
    root: Path,
    config: GuardConfig,
    paths: set[str] | None = None,
    analysis_snapshot: RepositoryAnalysisSnapshot | None = None,
) -> list[Finding]:
    """检查当前生产代码中的连续单调用私有函数链。

    该规则不再只看本轮新增定义。连续三层以上 helper 即使来自历史代码，
    仍然会持续增加职责跳转、调试成本和接口噪声。静态规则只证明调用拓扑，
    最终报告仍需结合事务、并发、生命周期、递归和真实替换边界进行语义裁决。

    Args:
        root: 当前代码快照根目录。
        config: 单调用链最小长度与文件排除配置。
        paths: 可选的仓库相对 Python 文件集合；为空时扫描全部生产 Python 文件。
        analysis_snapshot: 可选的统一工作树源码/AST 快照。

    Returns:
        每条高置信连续单调用链对应一条 QG168 Critical。
    """
    selected_paths = (
        paths
        if paths is not None
        else (
            {
                path
                for path in analysis_snapshot.paths
                if not is_test_path(path)
                and not any(fnmatch(path, pattern) for pattern in config.exclude)
            }
            if analysis_snapshot is not None
            else _production_python_paths(root, config)
        )
    )
    functions = _load_functions(root, selected_paths, analysis_snapshot)
    if not functions:
        return []
    outgoing, incoming, references = _call_graph(functions)
    eligible = {
        key for key, info in functions.items() if _eligible_helper(key, info, incoming, references)
    }
    findings: list[Finding] = []
    emitted: set[tuple[str, ...]] = set()
    for key in sorted(eligible):
        caller = next(iter(incoming[key]))
        if caller in eligible:
            continue
        chain = [key]
        current = key
        while True:
            next_helpers = [
                target
                for target in outgoing[current]
                if target in eligible
                and functions[target].path == functions[current].path
                and functions[target].owner == functions[current].owner
            ]
            if len(next_helpers) != 1:
                break
            nxt = next_helpers[0]
            if nxt in chain:
                break
            chain.append(nxt)
            current = nxt
        if len(chain) < config.single_use_chain_min_helpers:
            continue
        signature = tuple(symbol for _path, symbol in chain)
        if signature in emitted:
            continue
        emitted.add(signature)
        first = functions[chain[0]]
        caller_symbol = caller[1]
        chain_text = " → ".join([caller_symbol, *signature])
        findings.append(
            Finding(
                code="QG168",
                severity="critical",
                confidence="high",
                path=first.path,
                line=first.node.lineno,
                column=first.node.col_offset + 1,
                symbol=first.symbol,
                message=(
                    "检测到连续单调用私有函数链："
                    f"`{chain_text}`。链中 {len(chain)} 个 helper 均只有一个静态调用方，"
                    "且没有其他引用。"
                ),
                suggestion=(
                    "默认从最下游开始内联并删除中间 helper，直到剩余函数拥有独立职责、"
                    "真实复用或不可合并的事务/并发/生命周期边界。不能仅以可读性、封装或"
                    "未来复用为理由保留整条链。"
                ),
                evidence={
                    "chain": [caller_symbol, *signature],
                    "helper_count": len(chain),
                    "helper_lines": [functions[item].lines for item in chain],
                    "helper_branches": [functions[item].branches for item in chain],
                    "total_helper_lines": sum(functions[item].lines for item in chain),
                    "single_callers": {
                        item[1]: sorted(source[1] for source in incoming[item]) for item in chain
                    },
                    "non_call_references": {item[1]: references[item] for item in chain},
                },
            )
        )
    return findings
