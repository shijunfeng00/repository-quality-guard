from __future__ import annotations

import ast
import inspect
import re
from dataclasses import dataclass

SECTION_PATTERN = re.compile(r"^(Args|Arguments|Parameters|Returns|Yields|Raises):\s*$")
PARAMETER_PATTERN = re.compile(r"^(?P<name>\*{0,2}[A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:")


@dataclass(slots=True, frozen=True)
class DocstringInfo:
    """
    保存一个定义的 docstring 结构化信息。

    该对象只描述可静态验证的格式事实，不判断说明文字是否准确。
    """

    text: str
    content_lines: int
    sections: tuple[str, ...]
    documented_parameters: tuple[str, ...]

    @property
    def exists(self) -> bool:
        """
        判断定义是否存在 docstring。

        Returns:
            存在非空 docstring 时返回 True，否则返回 False。
        """
        return bool(self.text)

    @property
    def is_single_line(self) -> bool:
        """
        判断 docstring 是否只有一行有效内容。

        Returns:
            只有一行非空内容时返回 True，否则返回 False。
        """
        return self.content_lines == 1

    @property
    def has_returns(self) -> bool:
        """
        判断 docstring 是否声明返回值或生成值。

        Returns:
            包含 Returns 或 Yields 章节时返回 True，否则返回 False。
        """
        return "Returns" in self.sections or "Yields" in self.sections


def definition_code_lines(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """
    计算定义的物理跨度，并排除首部 docstring 所占行数。

    Args:
        node: 类、同步函数或异步函数 AST 节点。

    Returns:
        不包含 docstring 的代码行数，最小为 1。
    """
    end_line = node.end_lineno or node.lineno
    total_lines = end_line - node.lineno + 1
    if not node.body:
        return total_lines
    first = node.body[0]
    if not (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return total_lines
    doc_end = first.end_lineno or first.lineno
    return max(1, total_lines - (doc_end - first.lineno + 1))


def function_parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """
    提取函数需要在 Args 章节中说明的参数名称。

    Args:
        node: 同步函数或异步函数 AST 节点。

    Returns:
        排除 self 和 cls 后的参数名称元组；vararg/kwarg 使用裸参数名。
    """
    names = [
        argument.arg
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if argument.arg not in {"self", "cls"}
    ]
    if node.args.vararg is not None:
        names.append(node.args.vararg.arg)
    if node.args.kwarg is not None:
        names.append(node.args.kwarg.arg)
    return tuple(names)


def parse_docstring(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> DocstringInfo:
    """
    解析定义的 docstring，并识别 Google 风格章节与参数名称。

    Args:
        node: 类、同步函数或异步函数 AST 节点。

    Returns:
        包含有效行数、章节名称和已说明参数的结构化结果。
    """
    raw = ast.get_docstring(node, clean=False)
    if raw is None:
        return DocstringInfo(text="", content_lines=0, sections=(), documented_parameters=())
    text = inspect.cleandoc(raw).strip()
    if not text:
        return DocstringInfo(text="", content_lines=0, sections=(), documented_parameters=())
    lines = text.splitlines()
    content_lines = sum(bool(line.strip()) for line in lines)
    sections: list[str] = []
    documented_parameters: list[str] = []
    active_section = ""
    for line in lines:
        stripped = line.strip()
        section_match = SECTION_PATTERN.match(stripped)
        if section_match is not None:
            active_section = section_match.group(1)
            sections.append(active_section)
            continue
        if active_section not in {"Args", "Arguments", "Parameters"}:
            continue
        parameter_match = PARAMETER_PATTERN.match(stripped)
        if parameter_match is not None:
            documented_parameters.append(parameter_match.group("name"))
    return DocstringInfo(
        text=text,
        content_lines=content_lines,
        sections=tuple(sections),
        documented_parameters=tuple(documented_parameters),
    )
