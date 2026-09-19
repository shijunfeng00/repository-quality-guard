from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .git_utils import run_readonly_git
from .model import Finding, InterfaceChange


class ChangedFileLike(Protocol):
    """声明测试审计所需的最小文件变化字段。"""

    path: str
    status: str
    added: int
    removed: int


@dataclass(slots=True, frozen=True)
class TestCaseMetrics:
    """保存单个测试用例的稳定结构指纹、断言契约与实现耦合候选信号。"""

    qualname: str
    fingerprint: str
    assertions: int = 0
    assertion_contracts: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class TestMetrics:
    """保存单个测试文件在一个 Git 快照中的可比较结构指标。"""

    test_cases: tuple[str, ...] = ()
    cases: tuple[TestCaseMetrics, ...] = ()
    assertions: int = 0
    skips: int = 0
    mocks: int = 0
    parse_error: str = ""


@dataclass(slots=True, frozen=True)
class TestCaseChange:
    """表示需要人工说明的一项稳定测试契约变化。"""

    item_id: str
    path: str
    case: str
    change: str
    before_assertions: int
    after_assertions: int
    removed_assertions: tuple[str, ...] = ()
    added_assertions: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class TestChangeAudit:
    """表示测试文件的变更、行为指标、用例变化和静态风险。"""

    item_id: str
    path: str
    status: str
    added: int
    removed: int
    before: TestMetrics
    after: TestMetrics
    added_cases: tuple[str, ...]
    modified_cases: tuple[str, ...]
    removed_cases: tuple[str, ...]
    case_changes: tuple[TestCaseChange, ...]
    interface_changes: tuple[InterfaceChange, ...]
    findings: tuple[Finding, ...]


_SOURCE_INTROSPECTION_CALLS = frozenset(
    {
        "inspect.getsource",
        "inspect.getsourcelines",
        "inspect.signature",
        "ast.parse",
    }
)
_TOMBSTONE_MARKERS = (
    "removed",
    "deleted",
    "no_longer",
    "legacy",
    "must_not_exist",
    "has_no",
)
_SOURCEISH_NAMES = frozenset(
    {"source", "source_text", "code", "code_text", "module_source"}
)


def _call_name(node: ast.Call) -> str:
    """返回调用表达式的点分名称。"""
    current: ast.expr = node.func
    parts: list[str] = []
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _is_assertion_call(node: ast.Call) -> bool:
    """判断调用是否承担测试断言语义。"""
    name = _call_name(node).lower()
    leaf = name.rsplit(".", 1)[-1]
    return leaf.startswith("assert") or name in {
        "pytest.raises",
        "pytest.warns",
        "pytest.fail",
    }


def _assertion_metrics(node: ast.AST) -> tuple[int, tuple[str, ...]]:
    """统计断言并提取可观察断言表达式，供测试契约差分使用。"""
    count = 0
    contracts: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            count += 1
            contracts.append(f"assert {ast.unparse(child.test)}")
        elif isinstance(child, ast.Call) and _is_assertion_call(child):
            count += 1
            contracts.append(ast.unparse(child))
    return count, tuple(sorted(set(contracts)))


def _without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """去掉函数首行 docstring，避免文档文字变化伪装成测试契约变化。"""
    if not body:
        return body
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return body[1:]
    return body


def _case_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """生成忽略行号、函数名和 docstring 的测试逻辑稳定指纹。"""
    payload = "\n".join(
        [
            ast.dump(node.args, include_attributes=False),
            *(ast.dump(item, include_attributes=False) for item in node.decorator_list),
            *(
                ast.dump(item, include_attributes=False)
                for item in _without_docstring(node.body)
            ),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _name_mentions_source(node: ast.AST) -> bool:
    """判断表达式是否明显指向源码/代码文本变量。"""
    return any(
        isinstance(child, ast.Name) and child.id.lower() in _SOURCEISH_NAMES
        for child in ast.walk(node)
    )


def _literal_identifier(node: ast.AST) -> str:
    """从表达式中提取适合作为源码结构探测对象的字符串字面量。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value.strip()
        if value and (value.startswith("_") or value.isidentifier() or "def " in value):
            return value
    return ""


def _implementation_signals(
    qualname: str, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> tuple[str, ...]:
    """提取 change-detector / implementation-coupled 测试候选信号。"""
    signals: set[str] = set()
    lower_name = qualname.lower()
    if any(marker in lower_name for marker in _TOMBSTONE_MARKERS):
        signals.add("tombstone_test_name")
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _call_name(child).lower()
            if name in _SOURCE_INTROSPECTION_CALLS:
                signals.add("source_or_signature_introspection")
        elif isinstance(child, ast.Name) and child.id.upper().startswith("REMOVED_"):
            signals.add("historical_removed_symbol_list")
        elif isinstance(child, ast.Assert) and isinstance(child.test, ast.Compare):
            compare = child.test
            if len(compare.ops) != 1 or len(compare.comparators) != 1:
                continue
            operator = compare.ops[0]
            if not isinstance(operator, (ast.In, ast.NotIn)):
                continue
            left = _literal_identifier(compare.left)
            right = compare.comparators[0]
            if left and _name_mentions_source(right):
                signals.add(
                    "source_text_absence_assertion"
                    if isinstance(operator, ast.NotIn)
                    else "source_text_presence_assertion"
                )
    return tuple(sorted(signals))


def _qualified_case_nodes(
    tree: ast.AST,
) -> tuple[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef], ...]:
    """提取模块级与 Test* 类中的测试用例 AST。"""
    result: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            result.append((node.name, node))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for child in node.body:
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and child.name.startswith("test_"):
                    result.append((f"{node.name}.{child.name}", child))
    return tuple(sorted(result, key=lambda item: item[0]))


def _metrics(source: str) -> TestMetrics:
    """解析测试源码并统计用例、断言、跳过、mock 与用例稳定指纹。"""
    if not source:
        return TestMetrics()
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return TestMetrics(parse_error=f"{error.msg}:{error.lineno or 1}")
    assertions = 0
    skips = 0
    mocks = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            assertions += 1
            continue
        if isinstance(node, ast.Call):
            name = _call_name(node).lower()
            leaf = name.rsplit(".", 1)[-1]
            if _is_assertion_call(node):
                assertions += 1
            if name in {"pytest.skip", "pytest.xfail"}:
                skips += 1
            if leaf in {
                "mock",
                "magicmock",
                "patch",
                "patch.object",
                "monkeypatch",
            } or any(
                marker in name
                for marker in ("unittest.mock", "monkeypatch.", "mocker.")
            ):
                mocks += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                text = ast.unparse(decorator).lower()
                if "skip" in text or "xfail" in text:
                    skips += 1
    case_metrics: list[TestCaseMetrics] = []
    for qualname, node in _qualified_case_nodes(tree):
        case_assertions, assertion_contracts = _assertion_metrics(node)
        case_metrics.append(
            TestCaseMetrics(
                qualname=qualname,
                fingerprint=_case_fingerprint(node),
                assertions=case_assertions,
                assertion_contracts=assertion_contracts,
                signals=_implementation_signals(qualname, node),
            )
        )
    cases = tuple(case_metrics)
    return TestMetrics(
        test_cases=tuple(item.qualname for item in cases),
        cases=cases,
        assertions=assertions,
        skips=skips,
        mocks=mocks,
    )


def _git_source(root: Path, revision: str, path: str) -> str:
    """读取 Git 基线中的测试源码，不存在时返回空字符串。"""
    result = run_readonly_git(root, "show", f"{revision}:{path}")
    return result.stdout if result.returncode == 0 else ""


def _current_source(root: Path, path: str) -> str:
    """读取工作区测试源码，不存在或不可解码时返回空字符串。"""
    candidate = root / path
    if not candidate.is_file():
        return ""
    try:
        return candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _finding(
    code: str,
    severity: str,
    path: str,
    message: str,
    evidence: dict[str, object],
) -> Finding:
    """构造测试变化风险发现。"""
    suggestions = {
        "QG170": "恢复被删除的回归文件，或证明稳定行为覆盖已由等价测试迁移。",
        "QG171": "恢复被删除的测试用例，或证明真实需求/Bug 变化使旧断言失效且覆盖已迁移。",
        "QG172": "检查断言是否被弱化；恢复关键断言或说明稳定行为的等价验证路径。",
        "QG173": "检查新增 skip/xfail 是否掩盖失败；优先修复被跳过路径。",
        "QG174": "新增测试文件应包含可执行测试用例和有效行为断言。",
        "QG175": "修复测试语法，禁止用无法执行的测试文件作为验证证据。",
        "QG192": "优先通过公开行为/契约验证；历史删除、源码字符串、private helper 或签名形态若只是实现快照，不得作为稳定回归契约；确属长期架构不变量时由架构门禁表达。",
    }
    return Finding(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        confidence="high",
        path=path,
        line=1,
        column=1,
        message=message,
        suggestion=suggestions[code],
        evidence=evidence,
        source="quality-guard-test-audit",
    )


def _case_lookup(metrics: TestMetrics) -> dict[str, TestCaseMetrics]:
    """按限定测试名建立用例指标索引。"""
    return {item.qualname: item for item in metrics.cases}


def _test_change_id(path: str, case: str, change: str) -> str:
    """生成不受表格排序影响的稳定 TEST-CHANGE ID。"""
    digest = hashlib.sha256(f"{path}\0{case}\0{change}".encode("utf-8")).hexdigest()[
        :10
    ]
    return f"TEST-CHANGE-{digest}"


def _case_changes(
    path: str, before: TestMetrics, after: TestMetrics
) -> tuple[TestCaseChange, ...]:
    """找出既有测试修改/删除以及高风险新增测试。"""
    previous = _case_lookup(before)
    current = _case_lookup(after)
    changes: list[TestCaseChange] = []
    for case in sorted(set(previous) | set(current)):
        old = previous.get(case)
        new = current.get(case)
        if old is None and new is not None:
            if not new.signals:
                continue
            change = "ADDED_RISK"
        elif old is not None and new is None:
            change = "REMOVED"
        elif old is not None and new is not None and old.fingerprint != new.fingerprint:
            change = "MODIFIED"
        else:
            continue
        signals = () if new is None else new.signals
        before_contracts = set(() if old is None else old.assertion_contracts)
        after_contracts = set(() if new is None else new.assertion_contracts)
        changes.append(
            TestCaseChange(
                item_id=_test_change_id(path, case, change),
                path=path,
                case=case,
                change=change,
                before_assertions=0 if old is None else old.assertions,
                after_assertions=0 if new is None else new.assertions,
                removed_assertions=tuple(sorted(before_contracts - after_contracts)),
                added_assertions=tuple(sorted(after_contracts - before_contracts)),
                signals=signals,
            )
        )
    return tuple(changes)


def _file_risk_change(
    path: str, code: str, before: TestMetrics, after: TestMetrics
) -> TestCaseChange:
    """把无法归因到具体 test_* 的文件级弱化风险提升为稳定语义审计对象。"""
    case = f"<file-risk:{code}>"
    return TestCaseChange(
        item_id=_test_change_id(path, case, "FILE_RISK"),
        path=path,
        case=case,
        change="FILE_RISK",
        before_assertions=before.assertions,
        after_assertions=after.assertions,
    )


def _audit_findings(
    path: str,
    status: str,
    before: TestMetrics,
    after: TestMetrics,
    case_changes: tuple[TestCaseChange, ...],
) -> tuple[Finding, ...]:
    """根据测试前后指标生成防弱化风险和实现耦合候选。"""
    findings: list[Finding] = []
    removed_cases = tuple(sorted(set(before.test_cases) - set(after.test_cases)))
    if status.startswith("D") and before.test_cases:
        findings.append(
            _finding(
                "QG170",
                "critical",
                path,
                f"测试文件被删除，基线包含 {len(before.test_cases)} 个测试用例。",
                {"removed_test_cases": list(before.test_cases)},
            )
        )
    elif removed_cases:
        findings.append(
            _finding(
                "QG171",
                "error",
                path,
                f"测试变更删除了 {len(removed_cases)} 个既有测试用例。",
                {"removed_test_cases": list(removed_cases)},
            )
        )
    if before.assertions > after.assertions and not status.startswith("D"):
        findings.append(
            _finding(
                "QG172",
                "warning",
                path,
                f"测试断言数量从 {before.assertions} 降至 {after.assertions}，需确认没有弱化回归门禁。",
                {
                    "before_assertions": before.assertions,
                    "after_assertions": after.assertions,
                },
            )
        )
    if after.skips > before.skips:
        findings.append(
            _finding(
                "QG173",
                "warning",
                path,
                f"skip/xfail 数量从 {before.skips} 增至 {after.skips}。",
                {"before_skips": before.skips, "after_skips": after.skips},
            )
        )
    test_name = Path(path).name.lower()
    is_new_test_case_file = all(
        (
            status.startswith("A"),
            any((test_name.startswith("test_"), test_name.endswith("_test.py"))),
        )
    )
    if (
        is_new_test_case_file
        and after
        and (not after.test_cases or after.assertions == 0)
    ):
        findings.append(
            _finding(
                "QG174",
                "warning",
                path,
                "新增测试文件缺少可识别的 test_* 用例或有效断言。",
                {"test_cases": list(after.test_cases), "assertions": after.assertions},
            )
        )
    if after.parse_error:
        findings.append(
            _finding(
                "QG175",
                "error",
                path,
                f"测试源码无法解析：{after.parse_error}。",
                {"parse_error": after.parse_error},
            )
        )
    risky = [item for item in case_changes if item.signals]
    for item in risky:
        findings.append(
            _finding(
                "QG192",
                "info",
                path,
                f"{item.case} 命中实现耦合/change-detector 测试候选：{', '.join(item.signals)}。",
                {
                    "test_change_id": item.item_id,
                    "case": item.case,
                    "change": item.change,
                    "signals": list(item.signals),
                },
            )
        )
    return tuple(findings)


def build_test_change_audits(
    root: Path,
    changed_files: tuple[ChangedFileLike, ...],
    interface_changes: tuple[InterfaceChange, ...],
    revision: str,
) -> tuple[TestChangeAudit, ...]:
    """为变更测试文件建立独立审计事实；只解析发生变化的 tests。"""
    by_path: dict[str, list[InterfaceChange]] = {}
    for change in interface_changes:
        by_path.setdefault(change.path, []).append(change)
    audits: list[TestChangeAudit] = []
    for index, item in enumerate(changed_files, 1):
        path = item.path
        status = item.status
        before = _metrics(_git_source(root, revision, path))
        after = _metrics(_current_source(root, path))
        previous = _case_lookup(before)
        current = _case_lookup(after)
        added_cases = tuple(sorted(set(current) - set(previous)))
        removed_cases = tuple(sorted(set(previous) - set(current)))
        modified_cases = tuple(
            sorted(
                case
                for case in set(previous) & set(current)
                if previous[case].fingerprint != current[case].fingerprint
            )
        )
        case_changes = _case_changes(path, before, after)
        findings = _audit_findings(path, status, before, after, case_changes)
        covered_risk_codes = {
            code
            for change in case_changes
            for code in (("QG170", "QG171") if change.change == "REMOVED" else ())
        }
        file_risk_codes = {
            finding.code
            for finding in findings
            if finding.code in {"QG170", "QG171", "QG172", "QG173"}
            and finding.code not in covered_risk_codes
        }
        if file_risk_codes and not case_changes:
            case_changes = tuple(
                _file_risk_change(path, code, before, after)
                for code in sorted(file_risk_codes)
            )
        audits.append(
            TestChangeAudit(
                item_id=f"TEST-FILE-{index:03d}",
                path=path,
                status=status,
                added=item.added,
                removed=item.removed,
                before=before,
                after=after,
                added_cases=added_cases,
                modified_cases=modified_cases,
                removed_cases=removed_cases,
                case_changes=case_changes,
                interface_changes=tuple(
                    sorted(
                        by_path.get(path, ()),
                        key=lambda value: (value.kind, value.symbol),
                    )
                ),
                findings=findings,
            )
        )
    return tuple(audits)
