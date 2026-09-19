from __future__ import annotations

import ast
from dataclasses import dataclass

from .facts import ModuleFacts
from .model import Finding

LEGACY_NAMES = {
    "cfg": "config",
    "file_name": "filename",
    "file_names": "filenames",
    "top_k": "topk",
}
MATHEMATICAL_NORMALIZE_SUFFIXES = {
    "distribution",
    "distributions",
    "embedding",
    "embeddings",
    "feature",
    "features",
    "logit",
    "logits",
    "matrix",
    "matrices",
    "norm",
    "norms",
    "probabilities",
    "probability",
    "quaternion",
    "quaternions",
    "score",
    "scores",
    "tensor",
    "tensors",
    "vector",
    "vectors",
    "weight",
    "weights",
}


@dataclass(slots=True, frozen=True)
class _NameLocation:
    """保存待检查名称、稳定作用域和其源码位置。"""

    name: str
    line: int
    column: int
    context: str
    symbol: str


class _NamingVisitor(ast.NodeVisitor):
    """收集声明、参数、属性和关键字中的仓库命名候选。"""

    def __init__(self) -> None:
        """初始化命名候选收集器。

        Returns:
            None。
        """
        self.names: list[_NameLocation] = []
        self.normalize_functions: list[_NameLocation] = []
        self._seen: set[tuple[str, str, str]] = set()
        self._scope: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """检查同步函数名称和参数。

        Args:
            node: 同步函数定义节点。

        Returns:
            None。
        """
        self._scope.append(node.name)
        self._collect_function(node)
        self.generic_visit(node)
        self._scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """检查异步函数名称和参数。

        Args:
            node: 异步函数定义节点。

        Returns:
            None。
        """
        self._scope.append(node.name)
        self._collect_function(node)
        self.generic_visit(node)
        self._scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """在类作用域内检查方法和属性名称。

        Args:
            node: 类定义节点。

        Returns:
            None。
        """
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        """检查普通赋值声明名称。

        Args:
            node: 普通赋值节点。

        Returns:
            None。
        """
        for target in node.targets:
            self._collect_target(target, "assignment")
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """检查带注解赋值声明名称。

        Args:
            node: 带注解赋值节点。

        Returns:
            None。
        """
        self._collect_target(node.target, "annotation")
        if node.value is not None:
            self.generic_visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        """检查海象表达式声明名称。

        Args:
            node: 海象表达式节点。

        Returns:
            None。
        """
        self._collect_target(node.target, "named expression")
        self.generic_visit(node.value)

    def _collect_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """收集函数名和全部参数名。"""
        normalized = node.name.removeprefix("_")
        if normalized == "normalize" or normalized.startswith("normalize_"):
            suffix = normalized.removeprefix("normalize").removeprefix("_")
            if suffix not in MATHEMATICAL_NORMALIZE_SUFFIXES:
                self.normalize_functions.append(
                    _NameLocation(
                        node.name,
                        node.lineno,
                        node.col_offset,
                        "function",
                        ".".join(self._scope),
                    )
                )
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        for argument in arguments:
            self._add(argument.arg, argument.lineno, argument.col_offset, "parameter")
        if node.args.vararg is not None:
            argument = node.args.vararg
            self._add(argument.arg, argument.lineno, argument.col_offset, "parameter")
        if node.args.kwarg is not None:
            argument = node.args.kwarg
            self._add(argument.arg, argument.lineno, argument.col_offset, "parameter")

    def _collect_target(self, target: ast.expr, context: str) -> None:
        """递归收集赋值目标中的名称。"""
        if isinstance(target, ast.Name):
            self._add(target.id, target.lineno, target.col_offset, context)
            return
        if isinstance(target, ast.Attribute):
            self._add(target.attr, target.lineno, target.col_offset, "attribute")
            return
        if isinstance(target, (ast.List, ast.Tuple)):
            for item in target.elts:
                self._collect_target(item, context)

    def _add(self, name: str, line: int, column: int, context: str) -> None:
        """保存去重后的命名候选。"""
        if name not in LEGACY_NAMES:
            return
        scope = ".".join(self._scope) or "<module>"
        symbol = f"{scope}:{context}:{name}"
        key = (name, context, symbol)
        if key in self._seen:
            return
        self._seen.add(key)
        self.names.append(_NameLocation(name, line, column, context, symbol))


def check_naming_rules(facts: ModuleFacts, tree: ast.Module) -> list[Finding]:
    """检查仓库统一词汇和模糊 normalize 命名。

    Args:
        facts: 当前模块源码事实。
        tree: 当前模块 AST。

    Returns:
        命名规范发现列表。
    """
    visitor = _NamingVisitor()
    visitor.visit(tree)
    findings = [
        Finding(
            code="QG159",
            severity="critical",
            confidence="high",
            path=str(facts.path),
            line=item.line,
            column=item.column,
            message=(
                f"{item.context} 名称 `{item.name}` 偏离仓库统一词汇，"
                f"新代码应使用 `{LEGACY_NAMES[item.name]}`。"
            ),
            suggestion=(
                "在本次触达范围内统一命名并同步上下游；公共接口迁移必须作为显式接口变更，"
                "不能只改一侧或保留长期同义别名。"
            ),
            symbol=item.symbol,
            evidence={
                "name": item.name,
                "preferred": LEGACY_NAMES[item.name],
                "scope": item.symbol,
                "exact_match_only": True,
            },
        )
        for item in visitor.names
    ]
    findings.extend(
        Finding(
            code="QG160",
            severity="critical",
            confidence="high",
            path=str(facts.path),
            line=item.line,
            column=item.column,
            message=f"函数 `{item.name}` 滥用 normalize，名称无法证明其执行数学归一化。",
            suggestion=(
                "按真实职责改用 parse/validate/coerce/canonicalize/sanitize/resolve/build/"
                "serialize/extract 等精确动词；只有向量、矩阵、概率、分数、张量等"
                "数学归一化才保留 normalize。"
            ),
            symbol=item.name,
            evidence={
                "name": item.name,
                "allowed_mathematical_suffixes": sorted(MATHEMATICAL_NORMALIZE_SUFFIXES),
            },
        )
        for item in visitor.normalize_functions
    )
    return findings
