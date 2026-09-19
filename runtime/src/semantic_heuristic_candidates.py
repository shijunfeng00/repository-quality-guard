from __future__ import annotations

import ast
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .config import GuardConfig
from .git_utils import run_readonly_git
from .model import Finding

_HUNK_HEADER = re.compile(r"@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")
_REGEX_APIS = {
    "re.compile",
    "re.findall",
    "re.finditer",
    "re.fullmatch",
    "re.match",
    "re.search",
    "re.split",
    "re.sub",
    "re.subn",
}
_MIN_LITERAL_RULE_SIZE = 2
_SEMANTIC_TEXT_MARKERS = frozenset(
    {
        "query",
        "question",
        "message",
        "messages",
        "text",
        "content",
        "prompt",
        "instruction",
        "instructions",
        "user_input",
        "user_message",
        "answer",
    }
)
_FUZZY_APIS = {
    "difflib.SequenceMatcher",
    "fuzzywuzzy.fuzz.ratio",
    "fuzzywuzzy.fuzz.partial_ratio",
    "rapidfuzz.fuzz.ratio",
    "rapidfuzz.fuzz.partial_ratio",
    "rapidfuzz.process.extract",
    "rapidfuzz.process.extractOne",
}


@dataclass(slots=True, frozen=True)
class _Candidate:
    """保存一个可定位、可生成精确指纹的文本启发式候选。"""

    line: int
    column: int
    owner: str
    target: str
    mechanism: str
    detail: str


def _call_name(node: ast.AST) -> str:
    """返回调用或属性节点的点分名称。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _string_collection_size(node: ast.AST) -> int:
    """统计字面量字符串集合大小；非纯字符串集合返回零。"""
    if not isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return 0
    if not node.elts or not all(
        isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.elts
    ):
        return 0
    return len(node.elts)


def _assignment_name(node: ast.AST) -> str:
    """返回可静态定位的赋值目标；复杂解包目标不建立豁免指纹。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _call_name(node)
    return "<expression>"


def _expression_names(node: ast.AST) -> frozenset[str]:
    """提取表达式中可定位的名称，供文本来源判定使用。"""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id.casefold())
        elif isinstance(child, ast.Attribute):
            names.add(child.attr.casefold())
    return frozenset(names)


def _is_semantic_text_expression(node: ast.AST) -> bool:
    """判断表达式是否显式引用 query/message/content 等语义文本来源。"""
    for name in _expression_names(node):
        normalized = name.replace("-", "_")
        if normalized in _SEMANTIC_TEXT_MARKERS:
            return True
        parts = tuple(part for part in normalized.split("_") if part)
        if any(part in _SEMANTIC_TEXT_MARKERS for part in parts):
            return True
    return False


def _generator_literal_keyword_membership(node: ast.Call) -> tuple[int, str] | None:
    """识别 ``any/all(keyword in query for keyword in literals)`` 形式的词表判断。"""
    name = _call_name(node.func)
    if name not in {"any", "all"} or len(node.args) != 1:
        return None
    generator = node.args[0]
    if not isinstance(generator, ast.GeneratorExp) or len(generator.generators) != 1:
        return None
    comprehension = generator.generators[0]
    element = generator.elt
    valid_shape = (
        not comprehension.ifs
        and not comprehension.is_async
        and isinstance(comprehension.target, ast.Name)
        and isinstance(element, ast.Compare)
        and len(element.ops) == 1
        and len(element.comparators) == 1
        and isinstance(element.ops[0], (ast.In, ast.NotIn))
        and isinstance(element.left, ast.Name)
        and element.left.id == comprehension.target.id
    )
    if not valid_shape:
        return None
    size = _string_collection_size(comprehension.iter)
    if size < _MIN_LITERAL_RULE_SIZE or not _is_semantic_text_expression(element.comparators[0]):
        return None
    return size, name


class _CandidateVisitor(ast.NodeVisitor):
    """收集新增行上的文本启发式结构，不直接裁决其合理性。"""

    def __init__(self, changed_lines: set[int]) -> None:
        """初始化仅关注 Git 新增行的 AST 访问器。

        Args:
            changed_lines: 当前文件在 Git diff 中新增或改写后的行号集合。

        Returns:
            None。
        """
        self.changed_lines = changed_lines
        self.owner_stack: list[str] = []
        self.assignment_target = "<expression>"
        self.candidates: list[_Candidate] = []

    @property
    def owner(self) -> str:
        """返回当前定义限定名。

        Returns:
            类和函数嵌套形成的限定名；模块级表达式返回 ``<module>``。
        """
        return ".".join(self.owner_stack) or "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """在类作用域中继续收集候选。

        Args:
            node: 当前类定义节点。

        Returns:
            None。
        """
        self.owner_stack.append(node.name)
        self.generic_visit(node)
        self.owner_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """在同步函数作用域中继续收集候选。

        Args:
            node: 当前同步函数定义节点。

        Returns:
            None。
        """
        self.owner_stack.append(node.name)
        self.generic_visit(node)
        self.owner_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """复用同步函数的作用域跟踪逻辑。

        Args:
            node: 当前异步函数定义节点。

        Returns:
            None。
        """
        self.visit_FunctionDef(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """记录普通赋值的精确目标名称。

        Args:
            node: 当前赋值节点。

        Returns:
            None。
        """
        previous = self.assignment_target
        self.assignment_target = (
            _assignment_name(node.targets[0]) if len(node.targets) == 1 else "<expression>"
        )
        self.visit(node.value)
        self.assignment_target = previous

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """记录带类型注解赋值的精确目标名称。

        Args:
            node: 当前带注解赋值节点。

        Returns:
            None。
        """
        if node.value is None:
            return
        previous = self.assignment_target
        self.assignment_target = _assignment_name(node.target)
        self.visit(node.value)
        self.assignment_target = previous

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        """记录海象表达式的精确目标名称。

        Args:
            node: 当前海象表达式节点。

        Returns:
            None。
        """
        previous = self.assignment_target
        self.assignment_target = _assignment_name(node.target)
        self.visit(node.value)
        self.assignment_target = previous

    def visit_Call(self, node: ast.Call) -> None:
        """收集正则、模糊相似度和多字面量前后缀调用。

        Args:
            node: 当前调用节点。

        Returns:
            None。
        """
        if node.lineno in self.changed_lines:
            name = _call_name(node.func)
            if name in _REGEX_APIS:
                self._append(node, "regex", name)
            elif name in _FUZZY_APIS:
                self._append(node, "fuzzy_similarity", name)
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {
                "startswith",
                "endswith",
            }:
                size = _string_collection_size(node.args[0]) if node.args else 0
                if size >= _MIN_LITERAL_RULE_SIZE:
                    self._append(
                        node,
                        "literal_prefix_suffix_table",
                        f"{node.func.attr}({size} literals)",
                    )
            generator_membership = _generator_literal_keyword_membership(node)
            if generator_membership is not None:
                size, function_name = generator_membership
                self._append(
                    node,
                    "semantic_keyword_table",
                    f"{function_name} over {size} literal keywords against semantic text",
                )
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        """收集新增行中手工拼装的 OpenAI 标准 tool_call 协议对象。

        Args:
            node: 当前字典字面量。

        Returns:
            None。
        """
        if node.lineno in self.changed_lines:
            pairs = {
                key.value: value
                for key, value in zip(node.keys, node.values, strict=True)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            function_value = pairs.get("function")
            type_value = pairs.get("type")
            if (
                isinstance(type_value, ast.Constant)
                and type_value.value == "function"
                and isinstance(function_value, ast.Dict)
            ):
                function_keys = {
                    key.value
                    for key in function_value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                if {"name", "arguments"} <= function_keys:
                    self._append(
                        node,
                        "openai_tool_call_protocol",
                        "manual OpenAI tool_call dict(type=function,function{name,arguments})",
                    )
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        """收集针对多个字符串字面量的 membership 规则。

        Args:
            node: 当前比较表达式节点。

        Returns:
            None。
        """
        if node.lineno in self.changed_lines:
            for operator, comparator in zip(node.ops, node.comparators, strict=True):
                if not isinstance(operator, (ast.In, ast.NotIn)):
                    continue
                size = _string_collection_size(comparator)
                if size >= _MIN_LITERAL_RULE_SIZE:
                    self._append(
                        node,
                        "literal_membership_table",
                        f"membership against {size} string literals",
                    )
                    continue
                if (
                    isinstance(node.left, ast.Constant)
                    and isinstance(node.left.value, str)
                    and _is_semantic_text_expression(comparator)
                ):
                    self._append(
                        node,
                        "semantic_keyword_membership",
                        "literal keyword membership against semantic text",
                    )
        self.generic_visit(node)

    def _append(self, node: ast.AST, mechanism: str, detail: str) -> None:
        """追加去重前候选。"""
        self.candidates.append(
            _Candidate(
                line=node.lineno,
                column=node.col_offset + 1,
                owner=self.owner,
                target=self.assignment_target,
                mechanism=mechanism,
                detail=detail,
            )
        )


def _changed_line_map(root: Path, revision: str, staged: bool) -> dict[str, set[int]]:
    """读取 Git diff 中每个 Python 文件的新增行号。"""
    args = ["diff", "--unified=0"]
    if staged:
        args.append("--cached")
    args.extend([revision, "--", "*.py"])
    result = run_readonly_git(root, *args)
    if result.returncode != 0:
        return {}
    changed: defaultdict[str, set[int]] = defaultdict(set)
    current_path = ""
    for raw_line in result.stdout.splitlines():
        if raw_line.startswith("+++ b/"):
            current_path = raw_line[6:]
            changed[current_path]
            continue
        if not current_path or not raw_line.startswith("@@"):
            continue
        match = _HUNK_HEADER.search(raw_line)
        if match is None:
            continue
        start = int(match.group("start"))
        count = int(match.group("count") or "1")
        changed[current_path].update(range(start, start + count))
    return dict(changed)


def _baseline_authorizations(root: Path, revision: str, relative_path: str) -> frozenset[str]:
    """读取 Git 基线中预先存在的精确授权指纹。"""
    if not relative_path:
        return frozenset()
    result = run_readonly_git(root, "show", f"{revision}:{relative_path}")
    if result.returncode != 0:
        return frozenset()
    try:
        payload = tomllib.loads(result.stdout)
    except tomllib.TOMLDecodeError:
        return frozenset()
    if payload.get("version") != 1:
        return frozenset()
    authorized = payload.get("authorized", [])
    if not isinstance(authorized, list) or not all(isinstance(item, str) for item in authorized):
        return frozenset()
    return frozenset(authorized)


def _fingerprint(relative: str, candidate: _Candidate) -> str:
    """构造只能由 profile 或基线授权文件匹配的精确候选指纹。"""
    return ":".join((relative, candidate.owner, candidate.target, candidate.mechanism))


def semantic_heuristic_candidate_findings(
    root: Path,
    config: GuardConfig,
    revision: str,
    target_analysis: RepositoryAnalysisSnapshot | None = None,
    *,
    staged: bool = False,
    static_exemptions: tuple[str, ...] = (),
    authorization_path: str = "",
) -> list[Finding]:
    """列出非静态豁免的文本启发式候选，并附带基线授权事实。

    Args:
        root: 待审计 Git 仓库根目录。
        config: 当前项目的扫描配置。
        revision: 作为授权和新增行比较基线的 Git revision。
        staged: 是否审计暂存区而不是普通工作区。
        static_exemptions: 由 Guard profile 固定发布的精确候选指纹。
        authorization_path: 只从 Git 基线读取的授权账本相对路径。
        target_analysis: 可选的 after 统一源码/AST 快照。

    Returns:
        QG178 启发式候选与 QG191 手工协议构造候选；QG178 携带基线授权事实。
    """
    findings: list[Finding] = []
    exemptions = frozenset(static_exemptions)
    authorizations = _baseline_authorizations(root, revision, authorization_path)
    for relative, changed_lines in sorted(_changed_line_map(root, revision, staged).items()):
        if any(fnmatch(relative, pattern) for pattern in config.exclude):
            continue
        path = root / relative
        if not path.is_file():
            continue
        unit = target_analysis.unit(relative) if target_analysis is not None else None
        if unit is not None:
            source = unit.source
            tree = unit.tree
            if tree is None:
                continue
        else:
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=relative, type_comments=True)
            except (OSError, UnicodeError, SyntaxError):
                continue
        visitor = _CandidateVisitor(changed_lines)
        visitor.visit(tree)
        seen: set[str] = set()
        for candidate in visitor.candidates:
            fingerprint = _fingerprint(relative, candidate)
            if fingerprint in exemptions or fingerprint in seen:
                continue
            seen.add(fingerprint)
            if candidate.mechanism == "openai_tool_call_protocol":
                findings.append(
                    Finding(
                        code="QG191",
                        severity="info",
                        confidence="high",
                        path=relative,
                        line=candidate.line,
                        column=candidate.column,
                        symbol=candidate.owner,
                        message=(
                            "本轮代码手工拼装 OpenAI tool_call 协议对象；必须先检索并阅读项目既有 "
                            "Tool schema/validator/调用账本能力，确认这里确实应成为新的协议所有者。"
                        ),
                        suggestion=(
                            "执行 doc-search 检索标准 tool_call 生成、参数校验和 tool result/history 能力；"
                            "优先复用唯一协议 owner，避免调用方复制标准 JSON 结构。"
                        ),
                        evidence={
                            "mechanism": candidate.mechanism,
                            "detail": candidate.detail,
                            "changed_line": candidate.line,
                            "target": candidate.target,
                            "fingerprint": fingerprint,
                        },
                        source="protocol-construction-candidate",
                    )
                )
                continue
            authorized = fingerprint in authorizations
            findings.append(
                Finding(
                    code="QG178",
                    severity="info",
                    confidence="medium",
                    path=relative,
                    line=candidate.line,
                    column=candidate.column,
                    symbol=candidate.owner,
                    message=(
                        f"新增文本启发式候选 `{candidate.detail}`。该位置未命中 profile "
                        "精确静态豁免；是否获得授权由 Git 基线授权账本决定，报告模型无权自行豁免。"
                    ),
                    suggestion=(
                        "未获基线授权时删除该启发式，把判断交回 Agent/LLM/正式分类模型，"
                        "并在 Q10 标记 BLOCKING。确定性协议例外只能修改质量守卫 profile 的精确指纹。"
                    ),
                    evidence={
                        "mechanism": candidate.mechanism,
                        "detail": candidate.detail,
                        "changed_line": candidate.line,
                        "target": candidate.target,
                        "fingerprint": fingerprint,
                        "authorized_by_baseline": authorized,
                        "authorization_path": authorization_path,
                    },
                    source="semantic-heuristic-candidate",
                )
            )
    return findings
