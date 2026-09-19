from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from . import report_schema
from .config import is_test_path
from .gate_status import code_status
from .git_utils import run_readonly_git
from .model import Finding, InterfaceChange, ScanReport
from .test_audit import TestChangeAudit, build_test_change_audits

# Preserve the public report-contract constants exposed by v0.14.5.
REPORT_SCHEMA = report_schema.REPORT_SCHEMA
REPORT_FILENAME = report_schema.REPORT_FILENAME
REPORT_TITLE = report_schema.REPORT_TITLE
REQUIRED_SECTIONS = report_schema.REQUIRED_SECTIONS
COMMON_QUESTION_TITLES = report_schema.COMMON_QUESTION_TITLES
AGENT_SEMANTIC_HEURISTIC_QUESTION = report_schema.AGENT_SEMANTIC_HEURISTIC_QUESTION
VALID_ITEM_STATUSES = report_schema.VALID_ITEM_STATUSES
VALID_FINAL_STATUSES = report_schema.VALID_FINAL_STATUSES


@dataclass(slots=True, frozen=True)
class ChangedFile:
    """
    表示 Git 基线到工作区的一项文件变化。

    该事实用于强制回答为什么编辑每个文件，并与最终 Markdown 中的
    FILE 编号一一对应。
    """

    item_id: str
    path: str
    status: str
    added: int
    removed: int


@dataclass(slots=True, frozen=True)
class AddedInterface:
    """
    表示必须在最终 Markdown 中逐项披露的新增接口。

    新增文件、类、函数、方法、字段、全局变量和嵌套定义都来自接口
    快照差分，模型不能自行删减清单。
    """

    item_id: str
    change: InterfaceChange


@dataclass(slots=True, frozen=True)
class QualityDelta:
    """
    表示相对 Git 基线新增或升级的一项三档质量问题。

    该对象把稳定增量编号、当前发现、变化类型和基线级别绑定在一起，
    供报告生成器与最终门禁执行集合一致性校验。
    """

    item_id: str
    finding: Finding
    change: str
    baseline_severity: str


@dataclass(slots=True, frozen=True)
class ReportFacts:
    """
    保存严格报告自动生成并通过摘要锁定的事实。

    Attributes:
        revision: Git 比较基线。
        changed_files: 全部变更文件。
        additions: 全部新增接口。
        architecture: 父类、子类和公共所有者差分。
        quality_deltas: 相对基线新增或升级的三档问题。
        legacy_debt_total: Git 基线中的三档问题数量。
        legacy_debt_reduced: 已删除或降级的存量问题数量。
        digest: 自动事实的稳定摘要。
    """

    revision: str
    changed_files: tuple[ChangedFile, ...]
    test_changed_files: tuple[ChangedFile, ...]
    artifact_changed_files: tuple[ChangedFile, ...]
    additions: tuple[AddedInterface, ...]
    added_function_count: int
    added_variable_count: int
    added_class_count: int
    test_interface_changes: tuple[InterfaceChange, ...]
    parameter_interface_changes: tuple[InterfaceChange, ...]
    interface_added_definitions: int
    interface_removed_definitions: int
    interface_definition_net: int
    protocol_findings: tuple[Finding, ...]
    deletion_only_paths: frozenset[str]
    architecture: tuple[Finding, ...]
    quality_deltas: tuple[QualityDelta, ...]
    test_quality_deltas: tuple[QualityDelta, ...]
    test_audits: tuple[TestChangeAudit, ...]
    legacy_debt_total: int
    legacy_debt_reduced: int
    digest: str


def _changed_files(root: Path, revision: str) -> tuple[ChangedFile, ...]:
    """读取含 rename/untracked 的完整 Git 文件变化，并排除工具产物。

    ``git diff --numstat`` 的非 ``-z`` rename 会把路径压成 ``{old => new}``，
    不能与 ``--name-status`` 的新路径直接 join。这里统一消费 NUL 协议，rename/copy
    都以目标路径作为唯一事实，避免报告把一次移动重复统计成两项。
    """
    numstat = run_readonly_git(root, "diff", "--numstat", "-z", revision, "--")
    name_status = run_readonly_git(root, "diff", "--name-status", "-z", revision, "--")
    numbers: dict[str, tuple[int, int]] = {}
    statuses: dict[str, str] = {}
    if numstat.returncode == 0:
        tokens = numstat.stdout.split("\0")
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if not token:
                continue
            parts = token.split("\t")
            if len(parts) < report_schema.NUMSTAT_FIELDS:
                continue
            added_raw, removed_raw, path = parts[0], parts[1], parts[2]
            if path == "" and index + 1 < len(tokens):
                index += 1  # old path
                path = tokens[index]
                index += 1
            numbers[path] = (
                int(added_raw) if added_raw.isdigit() else 0,
                int(removed_raw) if removed_raw.isdigit() else 0,
            )
    if name_status.returncode == 0:
        tokens = name_status.stdout.split("\0")
        index = 0
        while index < len(tokens):
            status = tokens[index]
            index += 1
            if not status or index >= len(tokens):
                continue
            path = tokens[index]
            index += 1
            if status.startswith(("R", "C")) and index < len(tokens):
                path = tokens[index]
                index += 1
            if path:
                statuses[path] = status
    untracked = run_readonly_git(root, "ls-files", "--others", "--exclude-standard")
    if untracked.returncode == 0:
        for path in untracked.stdout.splitlines():
            if path:
                statuses[path] = "A?"
                numbers[path] = (report_schema.line_count(root / path), 0)
    excluded = {report_schema.REPORT_FILENAME, "修改说明报告.md"}
    generated_parts = {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
    paths = sorted(
        path
        for path in set(numbers) | set(statuses)
        if path not in excluded
        and not generated_parts.intersection(Path(path).parts)
        and not path.endswith((".pyc", ".pyo"))
    )
    for path in paths:
        if path not in statuses:
            statuses[path] = "M"
        if path not in numbers:
            numbers[path] = (0, 0)
    return tuple(
        ChangedFile(
            item_id=f"FILE-{index:03d}",
            path=path,
            status=statuses[path],
            added=numbers[path][0],
            removed=numbers[path][1],
        )
        for index, path in enumerate(paths, 1)
    )


def _is_delivery_artifact(path: str) -> bool:
    """判断文件是否是补丁、日志或归档等交付附件。"""
    lowered = path.lower()
    return any(lowered.endswith(suffix) for suffix in report_schema.DELIVERY_ARTIFACT_SUFFIXES)


def _added_interfaces(report: ScanReport) -> tuple[AddedInterface, ...]:
    """为全部新增文件、类、函数、方法和字段分配稳定 ADD 编号。"""
    changes = () if report.interface_diff is None else report.interface_diff.changes
    added = sorted(
        (
            item
            for item in changes
            if item.change == "added" and not is_test_path(item.path, report.project_name)
        ),
        key=lambda item: (item.path, item.kind, item.symbol),
    )
    return tuple(
        AddedInterface(item_id=f"ADD-{index:03d}", change=item)
        for index, item in enumerate(added, 1)
    )


def _addition_kind_counts(additions: tuple[AddedInterface, ...]) -> tuple[int, int, int]:
    """统计生产新增函数、变量和类数量。

    Args:
        additions: 自动接口差分生成的生产 ADD 清单。

    Returns:
        依次返回函数（含方法）、变量（含成员字段）和类数量。
    """
    function_count = sum(item.change.kind in {"function", "method"} for item in additions)
    variable_count = sum(
        item.change.kind in {"global_variable", "member_variable"} for item in additions
    )
    class_count = sum(item.change.kind == "class" for item in additions)
    return function_count, variable_count, class_count


def _current_quality_findings(report: ScanReport) -> dict[str, Finding]:
    """按稳定指纹返回生产代码三档问题。"""
    return {
        finding.fingerprint: finding
        for finding in report.findings
        if finding.severity in report_schema.SEVERITY_RANK
        and not finding.code.startswith("QG98")
        and finding.code not in report_schema.BASELINE_GATE_EXEMPT_CODES
        and not ("qg179_exempt" in finding.evidence and finding.evidence["qg179_exempt"] is True)
    }


def _current_test_quality_findings(report: ScanReport) -> dict[str, Finding]:
    """按稳定指纹返回测试/Tracing 非阻断域三档问题。"""
    return {
        finding.fingerprint: finding
        for finding in report.test_findings
        if finding.severity in report_schema.SEVERITY_RANK
        and not finding.code.startswith("QG98")
        and finding.code not in report_schema.BASELINE_GATE_EXEMPT_CODES
        and not ("qg179_exempt" in finding.evidence and finding.evidence["qg179_exempt"] is True)
    }


def _quality_delta_items(report: ScanReport, *, tests: bool = False) -> tuple[QualityDelta, ...]:
    """生成生产或测试范围相对 Git 基线新增、升级的问题清单。"""
    baseline_report = report.test_baseline if tests else report.baseline
    if baseline_report is None:
        return ()
    baseline = baseline_report.finding_severities
    current = _current_test_quality_findings(report) if tests else _current_quality_findings(report)
    deltas: list[tuple[Finding, str, str]] = []
    for fingerprint, finding in current.items():
        previous = baseline.get(fingerprint)
        if previous is None:
            deltas.append((finding, "INTRODUCED", "NONE"))
        elif report_schema.SEVERITY_RANK[finding.severity] > report_schema.SEVERITY_RANK[previous]:
            deltas.append((finding, "WORSENED", previous.upper()))
    deltas.sort(
        key=lambda item: (
            -report_schema.SEVERITY_RANK[item[0].severity],
            item[0].path,
            item[0].line,
            item[0].code,
            item[0].symbol,
        )
    )
    prefix = "TEST-DELTA" if tests else "DELTA"
    return tuple(
        QualityDelta(
            item_id=f"{prefix}-{index:03d}",
            finding=finding,
            change=change,
            baseline_severity=baseline_severity,
        )
        for index, (finding, change, baseline_severity) in enumerate(deltas, 1)
    )


def _legacy_debt_reduced_count(report: ScanReport) -> int:
    """统计相对基线已经删除或降级的存量三档问题。"""
    if report.baseline is None:
        return 0
    current = _current_quality_findings(report)
    reduced = 0
    for fingerprint, previous in report.baseline.finding_severities.items():
        finding = current.get(fingerprint)
        if (
            finding is None
            or report_schema.SEVERITY_RANK[finding.severity] < report_schema.SEVERITY_RANK[previous]
        ):
            reduced += 1
    return reduced


def build_report_facts(report: ScanReport, revision: str = "HEAD") -> ReportFacts:
    """根据生产扫描、测试扫描和 Git 差分生成严格报告事实。

    Args:
        report: 包含生产、测试、接口和基线结果的完整扫描报告。
        revision: 用于比较工作区变化的 Git 基线。

    Returns:
        已分离生产与测试事实并计算稳定摘要的报告事实。
    """
    architecture_codes = {"QG162", "QG163", "QG164", "QG165", "QG166", "QG167", "QG168"}
    architecture = tuple(
        sorted(
            (item for item in report.findings if item.code in architecture_codes),
            key=lambda item: (item.path, item.line, item.code, item.symbol),
        )
    )
    all_changed_files = _changed_files(report.root, revision)
    artifact_raw = tuple(item for item in all_changed_files if _is_delivery_artifact(item.path))
    production_raw = tuple(
        item
        for item in all_changed_files
        if not is_test_path(item.path, report.project_name) and not _is_delivery_artifact(item.path)
    )
    test_raw = tuple(
        item
        for item in all_changed_files
        if is_test_path(item.path, report.project_name) and not _is_delivery_artifact(item.path)
    )
    changed_files = tuple(
        ChangedFile(
            item_id=f"FILE-{index:03d}",
            path=item.path,
            status=item.status,
            added=item.added,
            removed=item.removed,
        )
        for index, item in enumerate(production_raw, 1)
    )
    artifact_changed_files = tuple(
        ChangedFile(
            item_id=f"ARTIFACT-{index:03d}",
            path=item.path,
            status=item.status,
            added=item.added,
            removed=item.removed,
        )
        for index, item in enumerate(artifact_raw, 1)
    )
    test_changed_files = tuple(
        ChangedFile(
            item_id=f"TEST-FILE-{index:03d}",
            path=item.path,
            status=item.status,
            added=item.added,
            removed=item.removed,
        )
        for index, item in enumerate(test_raw, 1)
    )
    changes = () if report.interface_diff is None else report.interface_diff.changes
    test_interface_changes = tuple(
        sorted(
            (item for item in changes if is_test_path(item.path, report.project_name)),
            key=lambda item: (item.path, item.kind, item.symbol, item.change),
        )
    )
    additions = _added_interfaces(report)
    added_function_count, added_variable_count, added_class_count = _addition_kind_counts(additions)
    production_interface_changes = tuple(
        item for item in changes if not is_test_path(item.path, report.project_name)
    )
    parameter_interface_changes = tuple(
        sorted(
            (
                item
                for item in production_interface_changes
                if item.change == "modified"
                and item.kind in report_schema.INTERFACE_DEFINITION_KINDS
            ),
            key=lambda item: (item.path, item.kind, item.symbol),
        )
    )
    interface_added_definitions = sum(
        item.change == "added" and item.kind in report_schema.INTERFACE_DEFINITION_KINDS
        for item in production_interface_changes
    )
    interface_removed_definitions = sum(
        item.change == "removed" and item.kind in report_schema.INTERFACE_DEFINITION_KINDS
        for item in production_interface_changes
    )
    interface_definition_net = interface_added_definitions - interface_removed_definitions
    protocol_findings = tuple(
        sorted(
            (item for item in report.findings if item.code == "QG182"),
            key=lambda item: (item.path, item.symbol, item.message),
        )
    )
    changes_by_path: dict[str, list[InterfaceChange]] = {}
    for item in production_interface_changes:
        changes_by_path.setdefault(item.path, []).append(item)
    deletion_only_paths = frozenset(
        item.path
        for item in changed_files
        if item.added == 0
        and item.path in changes_by_path
        and all(
            change.change == "removed" and change.kind in report_schema.INTERFACE_DEFINITION_KINDS
            for change in changes_by_path[item.path]
        )
    )
    quality_deltas = _quality_delta_items(report)
    test_quality_deltas = _quality_delta_items(report, tests=True)
    test_audits = build_test_change_audits(
        report.root,
        test_changed_files,
        test_interface_changes,
        revision,
    )
    legacy_debt_total = (
        len(report.baseline.finding_severities) if report.baseline is not None else 0
    )
    legacy_debt_reduced = _legacy_debt_reduced_count(report)
    payload = {
        "revision": revision,
        "changed_files": [asdict(item) for item in changed_files],
        "test_changed_files": [asdict(item) for item in test_changed_files],
        "artifact_changed_files": [asdict(item) for item in artifact_changed_files],
        "additions": [
            {
                "id": item.item_id,
                "path": item.change.path,
                "kind": item.change.kind,
                "symbol": item.change.symbol,
                "after": item.change.after.to_dict() if item.change.after is not None else None,
            }
            for item in additions
        ],
        "added_counts": {
            "functions": added_function_count,
            "variables": added_variable_count,
            "classes": added_class_count,
        },
        "test_interface_changes": [item.to_dict() for item in test_interface_changes],
        "parameter_interface_changes": [item.to_dict() for item in parameter_interface_changes],
        "interface_definition_balance": {
            "added": interface_added_definitions,
            "removed": interface_removed_definitions,
            "net": interface_definition_net,
        },
        "protocol_findings": [item.to_dict() for item in protocol_findings],
        "deletion_only_paths": sorted(deletion_only_paths),
        "architecture": [item.to_dict() for item in architecture],
        "quality_deltas": [
            {
                "id": item.item_id,
                "change": item.change,
                "baseline_severity": item.baseline_severity,
                "finding": item.finding.to_dict(),
            }
            for item in quality_deltas
        ],
        "test_quality_deltas": [
            {
                "id": item.item_id,
                "change": item.change,
                "baseline_severity": item.baseline_severity,
                "finding": item.finding.to_dict(),
            }
            for item in test_quality_deltas
        ],
        "test_audits": [
            {
                "id": item.item_id,
                "path": item.path,
                "status": item.status,
                "before": asdict(item.before),
                "after": asdict(item.after),
                "added_cases": list(item.added_cases),
                "modified_cases": list(item.modified_cases),
                "removed_cases": list(item.removed_cases),
                "case_changes": [asdict(change) for change in item.case_changes],
                "findings": [finding.to_dict() for finding in item.findings],
            }
            for item in test_audits
        ],
        "legacy_debt_total": legacy_debt_total,
        "legacy_debt_reduced": legacy_debt_reduced,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return ReportFacts(
        revision=revision,
        changed_files=changed_files,
        test_changed_files=test_changed_files,
        artifact_changed_files=artifact_changed_files,
        additions=additions,
        added_function_count=added_function_count,
        added_variable_count=added_variable_count,
        added_class_count=added_class_count,
        test_interface_changes=test_interface_changes,
        parameter_interface_changes=parameter_interface_changes,
        interface_added_definitions=interface_added_definitions,
        interface_removed_definitions=interface_removed_definitions,
        interface_definition_net=interface_definition_net,
        protocol_findings=protocol_findings,
        deletion_only_paths=deletion_only_paths,
        architecture=architecture,
        quality_deltas=quality_deltas,
        test_quality_deltas=test_quality_deltas,
        test_audits=test_audits,
        legacy_debt_total=legacy_debt_total,
        legacy_debt_reduced=legacy_debt_reduced,
        digest=digest,
    )


def _counts(report: ScanReport) -> dict[str, int]:
    """统计生产代码扫描中的 Critical、Error 和 Warning。"""
    result = {"critical": 0, "error": 0, "warning": 0}
    for item in report.findings:
        if item.code.startswith("QG98"):
            continue
        if item.severity in result:
            result[item.severity] += 1
    return result


def _test_counts(report: ScanReport) -> dict[str, int]:
    """统计测试/Tracing 非阻断域独立扫描中的 Critical、Error 和 Warning。"""
    result = {"critical": 0, "error": 0, "warning": 0}
    for item in report.test_findings:
        if item.code.startswith("QG98"):
            continue
        if item.severity in result:
            result[item.severity] += 1
    return result


def _interface_declaration(change: InterfaceChange) -> str:
    """渲染新增接口的紧凑声明。"""
    symbol = change.after
    if symbol is None:
        return "<不存在>"
    if change.kind in {"function", "method"}:
        parameters = ", ".join(parameter.name for parameter in symbol.parameters)
        returns = f" -> {symbol.return_type}" if symbol.return_type else ""
        return f"def {change.symbol}({parameters}){returns}"
    if change.kind == "class":
        bases = f"({', '.join(symbol.bases)})" if symbol.bases else ""
        return f"class {change.symbol}{bases}"
    annotation = f": {symbol.annotation}" if symbol.annotation else ""
    return f"{change.symbol}{annotation}"


def _auto(name: str, lines: Iterable[str]) -> list[str]:
    """包装不可由模型改写的自动事实区块。"""
    return [f"<!-- RQG:AUTO:BEGIN {name} -->", *lines, f"<!-- RQG:AUTO:END {name} -->"]


def _metadata_lines(report: ScanReport, facts: ReportFacts, counts: dict[str, int]) -> list[str]:
    """构造工具事实状态、人工语义结论和行为优先范围。"""
    status = code_status(report)
    test_counts = _test_counts(report)
    return [
        "---",
        f"report_schema: {report_schema.REPORT_SCHEMA}",
        f"change_digest: {facts.digest}",
        f"tool_status: {status}",
        "final_status: PENDING",
        "---",
        "",
        report_schema.REPORT_TITLE,
        "",
        report_schema.REQUIRED_SECTIONS[0],
        "",
        *_auto(
            "metadata",
            [
                f"- 工具静态事实结论：**{status}**",
                f"- Git 基线：`{facts.revision}`",
                f"- 目标：`{report.comparison_target}`",
                f"- 自动比较模式：{report.comparison_mode or '未记录'}",
                f"- Profile：`{report.project_name or '通用'}`",
                f"- 生产代码 Critical / Error / Warning：**{counts['critical']} / {counts['error']} / {counts['warning']}**",
                f"- 测试/Tracing 非阻断域 Critical / Error / Warning：**{test_counts['critical']} / {test_counts['error']} / {test_counts['warning']}**（独立审计，不计入生产预算）",
            ],
        ),
        "",
        "- 行为需求（WHAT/WHY）：待填写",
        "- 可验证场景（GIVEN/WHEN/THEN）：待填写",
        "- 设计边界与唯一所有者：待填写",
        "- 本轮非目标：待填写",
        "",
    ]


def _inventory_lines(facts: ReportFacts) -> list[str]:
    """构造生产文件事实，并把测试变更单独审计。"""
    inventory = [
        "| 文件ID | 状态 | 生产文件 | +行/-行 |",
        "|---|---|---|---:|",
    ]
    inventory.extend(
        f"| {item.item_id} | `{report_schema.escape(item.status)}` | `{report_schema.escape(item.path)}` | +{item.added}/-{item.removed} |"
        for item in facts.changed_files
    )
    if not facts.changed_files:
        inventory.append("| — | — | 无生产文件变化 | 0 |")
    lines = [report_schema.REQUIRED_SECTIONS[1], "", *_auto("inventory", inventory), ""]
    lines.extend(
        [
            "### 生产文件变更必要性",
            "",
            "| 文件ID | 文件 | 为什么编辑及事实依据 | 状态 |",
            "|---|---|---|---|",
        ]
    )
    lines.extend(
        (
            f"| {item.item_id} | `{report_schema.escape(item.path)}` | "
            "NOT_APPLICABLE（纯接口删除；删除清单即完整说明） | NOT_APPLICABLE |"
            if item.path in facts.deletion_only_paths
            else f"| {item.item_id} | `{report_schema.escape(item.path)}` | 事实=待填写；范围=待填写 | PENDING |"
        )
        for item in facts.changed_files
    )
    if not facts.changed_files:
        lines.append("| — | 无生产文件变化 | NOT_APPLICABLE | NOT_APPLICABLE |")

    artifact_rows = [
        "| 附件ID | 状态 | 交付/审计附件 | +行/-行 |",
        "|---|---|---|---:|",
    ]
    artifact_rows.extend(
        f"| {item.item_id} | `{report_schema.escape(item.status)}` | `{report_schema.escape(item.path)}` | +{item.added}/-{item.removed} |"
        for item in facts.artifact_changed_files
    )
    if not facts.artifact_changed_files:
        artifact_rows.append("| — | — | 无补丁、日志或归档附件变化 | 0 |")
    lines.extend(["", "### 交付与审计附件", "", *_auto("artifacts", artifact_rows), ""])
    lines.extend(
        [
            "> 补丁、diff、日志和归档不计入生产 FILE/ADD/ARCH/DELTA，但必须说明用途，避免把生成物冒充运行时代码。",
            "",
            "| 附件ID | 文件 | 用途与来源 | 状态 |",
            "|---|---|---|---|",
        ]
    )
    lines.extend(
        f"| {item.item_id} | `{report_schema.escape(item.path)}` | 用途=待填写；来源=待填写 | PENDING |"
        for item in facts.artifact_changed_files
    )
    if not facts.artifact_changed_files:
        lines.append("| — | 无交付附件变化 | NOT_APPLICABLE | NOT_APPLICABLE |")

    test_rows = [
        "| 测试文件ID | 状态 | 测试文件 | +行/-行 | 用例 Before→After | 断言 Before→After | Skip Before→After | Mock Before→After |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for audit in facts.test_audits:
        test_rows.append(
            f"| {audit.item_id} | `{report_schema.escape(audit.status)}` | `{report_schema.escape(audit.path)}` | +{audit.added}/-{audit.removed} | "
            f"{len(audit.before.test_cases)}→{len(audit.after.test_cases)} | "
            f"{audit.before.assertions}→{audit.after.assertions} | "
            f"{audit.before.skips}→{audit.after.skips} | {audit.before.mocks}→{audit.after.mocks} |"
        )
    if not facts.test_audits:
        test_rows.append("| — | — | 无测试文件变化 | 0 | 0→0 | 0→0 | 0→0 | 0→0 |")
    lines.extend(["", "### 测试文件独立审计", "", *_auto("test_inventory", test_rows), ""])
    lines.extend(
        [
            "> 测试仍必须审计，但不进入生产 FILE/ADD/ARCH/DELTA 预算。每个 TEST-FILE 只按文件级解释测试目标、断言强度、隔离方式和风险。",
            "",
            "| 测试文件ID | 文件 | 测试目的与覆盖行为 | 断言变化与是否弱化 | Mock/Stub 与真实生产路径 | 风险与结论 | 状态 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    lines.extend(
        f"| {audit.item_id} | `{report_schema.escape(audit.path)}` | 目的=待填写；覆盖=待填写 | "
        "断言=待填写；弱化=待填写 | Mock=待填写；生产路径=待填写 | "
        "风险=待填写；结论=待填写 | PENDING |"
        for audit in facts.test_audits
    )
    if not facts.test_audits:
        lines.append(
            "| — | 无测试文件变化 | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE |"
        )
    lines.append("")
    return lines


def _addition_lines(facts: ReportFacts) -> list[str]:
    """构造生产新增接口全量披露、数量抄写和测试符号摘要。"""
    disclosure = [
        "| 新增ID | 种类 | 生产文件 | 符号 | 声明 |",
        "|---|---|---|---|---|",
    ]
    for item in facts.additions:
        change = item.change
        disclosure.append(
            f"| {item.item_id} | `{change.kind}` | `{report_schema.escape(change.path)}` | "
            f"`{report_schema.escape(change.symbol)}` | `{report_schema.escape(_interface_declaration(change))}` |"
        )
    if not facts.additions:
        disclosure.append("| — | — | — | 无生产新增接口 | — |")
    count_lines = [
        f"- 工具统计新增函数（含方法与嵌套函数）：{facts.added_function_count}",
        f"- 工具统计新增变量（含全局变量与成员字段）：{facts.added_variable_count}",
        f"- 工具统计新增类（含嵌套类）：{facts.added_class_count}",
    ]
    lines = [
        report_schema.REQUIRED_SECTIONS[2],
        "",
        *_auto("added_counts", count_lines),
        "",
        "- 模型抄写新增数量：函数=待填写；变量=待填写；类=待填写",
        "",
        *_auto("added_interfaces", disclosure),
        "",
    ]
    lines.extend(
        [
            "> ADD 仅表示生产代码新增接口。工具负责锁定符号、数量、所有者和调用事实；模型负责语义裁决职责是否正确。Validator 只检查逐项覆盖、结构、证据引用和结论一致性，不用关键词正则冒充架构理解。",
            "",
            "| 新增ID | 符号 | 可观察失败、唯一职责与相邻层边界 | API 目录/BM25 与 HEAD/继承/公共能力检索 | 删除/内联/合并/复用减法实验 | 调用链、验证与语义裁决 | 状态 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    lines.extend(
        f"| {item.item_id} | `{report_schema.escape(item.change.symbol)}` | "
        "失败=待填写；职责=待填写；边界=待填写；绝对必要性=待填写；不可替代=待填写 | "
        "接口目录=待填写；查询=待填写；候选=待填写；源码=待填写；HEAD=待填写；父类=待填写；MRO=待填写；兄弟类=待填写；公共能力=待填写 | "
        "删除=待填写；内联=待填写；合并=待填写；复用=待填写 | "
        "调用链=待填写；验证=待填写；语义裁决=待填写 | PENDING |"
        for item in facts.additions
    )
    if not facts.additions:
        lines.append(
            "| — | 无生产新增接口 | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE |"
        )
    lines.extend(
        [
            "",
            "### 全量二次减法复审",
            "",
            "- 复审范围：ADD=待填写；存量低价值 helper=待填写",
            "- 减法结果：删除=待填写；内联=待填写；合并=待填写；保留=待填写",
            "- 最终结论：待填写",
        ]
    )

    grouped: dict[str, dict[str, int]] = {}
    for change in facts.test_interface_changes:
        row = grouped.get(change.path)
        if row is None:
            row = {"test_case": 0, "helper": 0, "class": 0, "field": 0, "removed": 0}
            grouped[change.path] = row
        if change.change == "removed":
            row["removed"] += 1
        leaf = change.symbol.rsplit(".", 1)[-1]
        if change.kind in {"function", "method"} and leaf.startswith("test_"):
            row["test_case"] += 1
        elif change.kind == "class":
            row["class"] += 1
        elif change.kind in {"global_variable", "member_variable"}:
            row["field"] += 1
        else:
            row["helper"] += 1
    test_summary = [
        "| 测试文件 | test_* 变化 | helper 变化 | class 变化 | field 变化 | 删除符号 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for path, row in sorted(grouped.items()):
        test_summary.append(
            f"| `{report_schema.escape(path)}` | {row['test_case']} | {row['helper']} | {row['class']} | {row['field']} | {row['removed']} |"
        )
    if not grouped:
        test_summary.append("| — | 0 | 0 | 0 | 0 | 0 |")
    lines.extend(
        [
            "",
            "### 测试新增符号摘要（不逐 helper 展开）",
            "",
            *_auto("test_interface_summary", test_summary),
            "",
        ]
    )
    return lines


def _architecture_lines(facts: ReportFacts) -> list[str]:
    """构造父类、子类和公共所有者差分章节。"""
    automatic = [
        "| 架构ID | 规则 | 级别 | 位置 | 静态事实 |",
        "|---|---|---|---|---|",
    ]
    for index, finding in enumerate(facts.architecture, 1):
        automatic.append(
            f"| ARCH-{index:03d} | `{finding.code}` | `{finding.severity.upper()}` | "
            f"`{report_schema.escape(finding.path)}:{finding.line}` | {report_schema.escape(finding.message)} |"
        )
    if not facts.architecture:
        automatic.append("| — | — | — | — | 未发现继承与公共所有者差分 |")
    lines = [report_schema.REQUIRED_SECTIONS[3], "", *_auto("architecture", automatic), ""]
    lines.extend(
        [
            "| 架构ID | 修复或保留的事实依据 | 验证证据 | 状态 |",
            "|---|---|---|---|",
        ]
    )
    lines.extend(
        f"| ARCH-{index:03d} | 处置=待填写 | 证据=待填写 | PENDING |"
        for index, _finding in enumerate(facts.architecture, 1)
    )
    if not facts.architecture:
        lines.append("| — | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE |")
    lines.append("")
    return lines


def _declaration_for_side(change: InterfaceChange, before: bool) -> str:
    """渲染接口变化指定一侧的声明。"""
    symbol = change.before if before else change.after
    if symbol is None:
        return "<不存在>"
    return _interface_declaration(
        InterfaceChange("added", change.kind, change.path, change.symbol, after=symbol)
    )


def _interface_lines(report: ScanReport, facts: ReportFacts) -> list[str]:
    """构造接口事实、定义净额门禁和存量接口声明修改审查。"""
    rows = [
        "| 生产文件 | 符号 | 变化 | Before | After |",
        "|---|---|---|---|---|",
    ]
    changes = () if report.interface_diff is None else report.interface_diff.changes
    production_changes = tuple(
        change for change in changes if not is_test_path(change.path, report.project_name)
    )
    for change in production_changes:
        rows.append(
            f"| `{report_schema.escape(change.path)}` | `{report_schema.escape(change.symbol)}` | `{change.change}` | "
            f"`{report_schema.escape(_declaration_for_side(change, before=True))}` | "
            f"`{report_schema.escape(_declaration_for_side(change, before=False))}` |"
        )
    if not production_changes:
        rows.append("| — | — | unchanged | — | — |")

    balance_decision = (
        "REVIEW_REQUIRED（QG181，需人工确认）" if facts.interface_definition_net > 0 else "PASS"
    )
    balance_rows = [
        "| 新增函数/方法/类 | 删除函数/方法/类 | 净额 | 结论 |",
        "|---:|---:|---:|---|",
        f"| {facts.interface_added_definitions} | {facts.interface_removed_definitions} | "
        f"{facts.interface_definition_net:+d} | {balance_decision} |",
    ]

    test_changes = tuple(
        change for change in changes if is_test_path(change.path, report.project_name)
    )
    test_rows = [
        "| 测试文件 | 新增 | 删除 | 修改 | 代表变化 |",
        "|---|---:|---:|---:|---|",
    ]
    grouped: dict[str, list[InterfaceChange]] = {}
    for change in test_changes:
        items = grouped.get(change.path)
        if items is None:
            items = []
            grouped[change.path] = items
        items.append(change)
    for path, items in sorted(grouped.items()):
        counts = {
            kind: sum(item.change == kind for item in items)
            for kind in ("added", "removed", "modified")
        }
        representative = ", ".join(
            f"{item.change}:{item.symbol}" for item in items[: report_schema.TEST_INTERFACE_PREVIEW]
        )
        if len(items) > report_schema.TEST_INTERFACE_PREVIEW:
            representative += f", 其余 {len(items) - report_schema.TEST_INTERFACE_PREVIEW} 项"
        test_rows.append(
            f"| `{report_schema.escape(path)}` | {counts['added']} | {counts['removed']} | "
            f"{counts['modified']} | {report_schema.escape(representative)} |"
        )
    if not grouped:
        test_rows.append("| — | 0 | 0 | 0 | 无测试接口变化 |")

    deletion_only = bool(production_changes) and all(
        change.change == "removed" and change.kind in report_schema.INTERFACE_DEFINITION_KINDS
        for change in production_changes
    )
    sync_line = (
        "- 调用方、README、Prompt、配置与测试同步结论："
        "NOT_APPLICABLE（本轮仅删除函数/方法/类；删除清单即完整说明，不要求解释删除理由）"
        if deletion_only
        else "- 调用方、README、Prompt、配置与测试同步结论：待填写"
    )
    lines = [
        report_schema.REQUIRED_SECTIONS[4],
        "",
        "### 生产接口",
        "",
        *_auto("interfaces", rows),
        "",
        "### 函数、方法与类净额硬门禁",
        "",
        "> 新增函数/方法/类记为 QG180 Warning；删除同类定义受鼓励且可抵消数量。"
        "净额大于 0 时由 QG181 标记为 REVIEW_REQUIRED，必须人工确认；删除项只需保留自动清单，不要求逐项解释原因。",
        "",
        *_auto("interface_definition_balance", balance_rows),
        "",
        "### 存量参数、property 与接口形态修改审查",
        "",
        "> 参数列表、property 装饰器和接口形态变化记为 QG180 Warning，不参与定义数量抵消。"
        "必须证明绝对必要性、不可替代性、兼容性、调用方同步与新鲜验证；"
        "仍存在的变化只能 JUSTIFIED 或 BLOCKING。",
        "",
        "| 接口审查ID | 符号 | 绝对必要性与不可替代 | 兼容与同步 | 调用方与验证证据 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| INTERFACE-{index:03d} | `{report_schema.escape(change.symbol)}` | 绝对必要性=待填写；不可替代=待填写；最小方案=待填写 | "
        "兼容=待填写；同步=待填写 | 调用方=待填写；验证=待填写 | PENDING |"
        for index, change in enumerate(facts.parameter_interface_changes, 1)
    )
    if not facts.parameter_interface_changes:
        lines.append(
            "| — | 无存量接口声明修改 | NOT_APPLICABLE | NOT_APPLICABLE | "
            "NOT_APPLICABLE | NOT_APPLICABLE |"
        )
    lines.extend(
        [
            "",
            "### Profile 核心协议字段审查",
            "",
            "> 仅在当前 Profile 声明了核心协议契约且检测到协议字段变化时生成。"
            "每一项都是 QG182 Warning，必须说明绝对必要性、为何不能复用现有协议、"
            "上下游兼容和新鲜验证；报告解释通过后仍保持 REVIEW_REQUIRED。",
            "",
            "| 协议审查ID | 协议符号 | 自动变化事实 | 绝对必要性与不可复用 | 上下游与兼容 | 验证与回滚 | 状态 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    lines.extend(
        f"| PROTOCOL-{index:03d} | `{report_schema.escape(finding.symbol or finding.path)}` | "
        f"{report_schema.escape(finding.message)} | 必要性=待填写；不可绕过=待填写；不可复用=待填写 | "
        "上下游=待填写；兼容=待填写 | 验证=待填写；回滚=待填写 | PENDING |"
        for index, finding in enumerate(facts.protocol_findings, 1)
    )
    if not facts.protocol_findings:
        lines.append(
            "| — | 无核心协议变化 | NOT_APPLICABLE | NOT_APPLICABLE | "
            "NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE |"
        )
    lines.extend(
        [
            "",
            "### 测试接口变化摘要",
            "",
            *_auto("test_interfaces", test_rows),
            "",
            sync_line,
            "",
        ]
    )
    return lines


def _test_quality_lines(report: ScanReport, facts: ReportFacts) -> list[str]:
    """渲染与生产预算分离的测试质量、增量和防弱化风险。"""
    test_counts = _test_counts(report)
    test_baseline_counts = (
        report.test_baseline.summary
        if report.test_baseline is not None
        else {"critical": 0, "error": 0, "warning": 0}
    )
    test_budget = [
        "| 级别 | 测试基线 | 当前测试 | 净变化 |",
        "|---|---:|---:|---:|",
    ]
    for severity in ("critical", "error", "warning"):
        before = test_baseline_counts[severity] if report.test_baseline is not None else 0
        after = test_counts[severity]
        delta = after - before
        baseline_text = str(before) if report.test_baseline is not None else "N/A"
        delta_text = f"{delta:+d}" if report.test_baseline is not None else "N/A"
        test_budget.append(f"| {severity.upper()} | {baseline_text} | {after} | {delta_text} |")
    test_delta_rows = [
        "| 测试增量 | 类型 | 规则 | 位置 | 静态事实 |",
        "|---|---|---|---|---|",
    ]
    for item in facts.test_quality_deltas:
        finding = item.finding
        test_delta_rows.append(
            f"| {item.item_id} | `{item.change}` | `{finding.code}` | "
            f"`{report_schema.escape(finding.path)}:{finding.line}` | {report_schema.escape(finding.message)} |"
        )
    if not facts.test_quality_deltas:
        test_delta_rows.append("| — | — | — | — | 未发现测试/Tracing 非阻断域新增或升级质量问题 |")
    test_risks = [
        (f"TEST-RISK-{index:03d}", finding)
        for index, finding in enumerate(
            (finding for audit in facts.test_audits for finding in audit.findings),
            1,
        )
    ]
    risk_rows = [
        "| 测试风险ID | 规则 | 级别 | 文件 | 静态事实 |",
        "|---|---|---|---|---|",
    ]
    risk_rows.extend(
        f"| {item_id} | `{finding.code}` | `{finding.severity.upper()}` | "
        f"`{report_schema.escape(finding.path)}` | {report_schema.escape(finding.message)} |"
        for item_id, finding in test_risks
    )
    if not test_risks:
        risk_rows.append("| — | — | — | — | 未发现删除用例、减少断言或增加 skip 等测试弱化风险 |")
    lines = [
        "### 测试/Tracing 非阻断域独立质量审计",
        "",
        "> 测试问题不计入生产代码 0/0/0，也不生成生产 ADD/DELTA；但测试删除、用例减少、断言减少、skip 增加和无有效断言会单独阻塞或要求说明。",
        "",
        *_auto("test_quality", test_budget),
        "",
        *_auto("test_quality_deltas", test_delta_rows),
        "",
        *_auto("test_risks", risk_rows),
        "",
        "| 测试风险ID | 处置事实 | 验证证据 | 状态 |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {item_id} | 处置=待填写 | 证据=待填写 | PENDING |" for item_id, _finding in test_risks
    )
    if not test_risks:
        lines.append("| — | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE |")
    lines.append("")
    return lines


def _quality_lines(
    report: ScanReport,
    facts: ReportFacts,
    counts: dict[str, int],
) -> list[str]:
    """构造生产零回归预算和测试独立审计预算。"""
    baseline_counts = (
        report.baseline.summary
        if report.baseline is not None
        else {"critical": 0, "error": 0, "warning": 0}
    )
    raw_current = {
        severity: baseline_counts[severity] + report.quality_count_deltas.get(severity, 0)
        for severity in ("critical", "error", "warning")
    }
    regression_rows = [
        "| 级别 | Git 基线 | 当前（门禁前） | 净变化 | 结论 |",
        "|---|---:|---:|---:|---|",
    ]
    for severity in ("critical", "error", "warning"):
        before = baseline_counts[severity] if report.baseline is not None else 0
        after = raw_current[severity] if report.baseline is not None else 0
        delta = report.quality_count_deltas.get(severity, 0)
        fingerprint_regressions = [
            item for item in facts.quality_deltas if item.finding.severity == severity
        ]
        decision = (
            f"REJECT（{len(fingerprint_regressions)} 项新增/升级，不可抵消）"
            if fingerprint_regressions
            else "PASS"
        )
        regression_rows.append(
            f"| {severity.upper()} | {before if report.baseline is not None else 'N/A'} | "
            f"{after if report.baseline is not None else 'N/A'} | "
            f"{f'{delta:+d}' if report.baseline is not None else 'N/A'} | {decision} |"
        )
    rows = [
        "| 级别 | Git 基线 | 当前数量 | 净变化 | 处理要求 |",
        "|---|---:|---:|---:|---|",
    ]
    for severity in ("critical", "error", "warning"):
        before = baseline_counts[severity] if report.baseline is not None else 0
        after = counts[severity]
        delta = after - before
        requirement = (
            "本轮存在新增或升级项时不可解释豁免，必须修复或回退"
            if delta > 0
            else "已削减存量，继续在不扩大范围的前提下优化"
            if delta < 0
            else "未净增加；存量不强制清零，但未削减时必须说明范围理由"
        )
        baseline_text = str(before) if report.baseline is not None else "N/A"
        delta_text = f"{delta:+d}" if report.baseline is not None else "N/A"
        rows.append(
            f"| {severity.upper()} | {baseline_text} | {after} | {delta_text} | {requirement} |"
        )
    rows.extend(["", "| 规则 | 级别 | 数量 | 代表位置 | 代表问题 |", "|---|---|---:|---|---|"])
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for finding in report.findings:
        if finding.severity not in report_schema.SEVERITY_RANK or finding.code.startswith("QG98"):
            continue
        key = (finding.code, finding.severity)
        findings = grouped.get(key)
        if findings is None:
            findings = []
            grouped[key] = findings
        findings.append(finding)
    for (code, severity), findings in sorted(
        grouped.items(),
        key=lambda item: (-report_schema.SEVERITY_RANK[item[0][1]], item[0][0]),
    ):
        representative = sorted(
            findings,
            key=lambda item: (item.path, item.line, item.column, item.symbol),
        )[0]
        rows.append(
            f"| `{code}` | `{severity.upper()}` | {len(findings)} | "
            f"`{report_schema.escape(representative.path)}:{representative.line}` | "
            f"{report_schema.escape(representative.message)} |"
        )
    if not grouped:
        rows.append("| — | — | 0 | — | 未发现生产代码三档问题 |")

    delta_rows = [
        "| 增量ID | 类型 | 基线→当前 | 规则 | 位置 | 静态事实 |",
        "|---|---|---|---|---|---|",
    ]
    for item in facts.quality_deltas:
        finding = item.finding
        delta_rows.append(
            f"| {item.item_id} | `{item.change}` | "
            f"`{item.baseline_severity}→{finding.severity.upper()}` | `{finding.code}` | "
            f"`{report_schema.escape(finding.path)}:{finding.line}` | {report_schema.escape(finding.message)} |"
        )
    if not facts.quality_deltas:
        delta_rows.append("| — | — | — | — | — | 未发现生产代码新增或升级问题 |")

    lines = [
        report_schema.REQUIRED_SECTIONS[5],
        "",
        "### Git 变更三档逐项新增/升级门禁",
        "",
        f"- 比较模式：{report.comparison_mode or '未记录'}",
        f"- 比较目标：`{report.comparison_target}`",
        "- 规则：任一普通 Critical、Error、Warning 指纹新增或升级即由 QG179 直接拒绝；历史债务减少不得抵消。QG180/QG181/QG182 仅由接口专用 REVIEW_REQUIRED 账本裁决。",
        "",
        *_auto("quality_regression_gate", regression_rows),
        "",
        "### 生产代码 Critical / Error / Warning 独立预算",
        "",
        *_auto("quality", rows),
        "",
        "### 生产代码新增或恶化问题逐项阻断",
        "",
        "> DELTA 是相对 Git 基线新增或升级的普通三档问题。修改说明只能复核事实与给出处置方向，不能豁免；所有仍存在的 DELTA 必须标记 BLOCKING，最终结论必须 REJECT。",
        "",
        *_auto("quality_deltas", delta_rows),
        "",
        "| 增量ID | 静态事实复核与语义裁决 | 替代与处置方向 | 风险与关闭条件 | 状态 |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {item.item_id} | 事实=待填写；语义裁决=待填写 | "
        "替代=待填写；处置=待填写 | 风险=待填写；关闭=待填写 | PENDING |"
        for item in facts.quality_deltas
    )
    if not facts.quality_deltas:
        lines.append("| — | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE |")
    lines.extend(
        [
            "",
            f"- 自动存量债务事实：基线生产三档共 {facts.legacy_debt_total} 项；本轮已删除或降级 {facts.legacy_debt_reduced} 项。",
            "- 每一档的修复、阻塞或暂缓事实及剩余数量：待填写",
            "- 存量债务削减结论：待填写",
            "- 存量债务未削减说明：待填写",
            "",
        ]
    )

    lines.extend(_test_quality_lines(report, facts))
    return lines


def _verification_and_question_lines(report: ScanReport) -> list[str]:
    """构造验证表与固定十项架构审判。"""
    lines = [
        report_schema.REQUIRED_SECTIONS[6],
        "",
        "| 命令 | 目的 | 退出码 | 结果摘要 |",
        "|---|---|---:|---|",
        "| `待填写` | 待填写 | 0 | 待填写 |",
        "",
        report_schema.REQUIRED_SECTIONS[7],
        "",
        "每题状态只能使用 `FIXED / JUSTIFIED / BLOCKING / NOT_APPLICABLE`。静态工具只提供事实，模型必须引用 FILE/ADD/ARCH/DELTA、测试 TEST-FILE/TEST-RISK 或 QG 编号完成语义裁决；发现职责越界或未经授权的模糊语义启发式时必须标记 BLOCKING，而不是为自动状态辩护。",
        "",
    ]
    for title in report_schema.question_titles(report):
        fact = "证据=待填写"
        if (
            title == report_schema.AGENT_SEMANTIC_HEURISTIC_QUESTION
            and "strict-semantic-question" in report.profile_capabilities
        ):
            fact = "证据=待填写；候选=待填写；语义任务=待填写；规则机制=待填写；授权证据=NONE"
        lines.extend(
            [
                f"### {title}",
                "",
                "- 状态：PENDING",
                f"- 事实依据：{fact}",
                "- 处理与结论：处理=待填写；结论=待填写",
                "",
            ]
        )
    return lines


def _closing_lines() -> list[str]:
    """构造剩余风险、人工结论和多行中文 commit。"""
    return [
        report_schema.REQUIRED_SECTIONS[8],
        "",
        "| Finding/对象 | 级别 | 未解决原因 | 独立提交建议 |",
        "|---|---|---|---|",
        "| 待填写 | 待填写 | 待填写 | 待填写 |",
        "",
        "- 最终人工结论：PENDING",
        "",
        report_schema.REQUIRED_SECTIONS[9],
        "",
        "```bash",
        'git commit -m "refactor: 完成仓库质量门禁与架构审计',
        "",
        "- 强制披露全部新增接口及其事实依据",
        "- 校验父类子类边界、验证证据与修改说明完整性",
        '"',
        "```",
        "",
    ]


def _fresh_template(report: ScanReport, revision: str) -> str:
    """生成首次使用的固定十节修改说明模板。"""
    facts = build_report_facts(report, revision)
    counts = _counts(report)
    lines = [
        *_metadata_lines(report, facts, counts),
        *_inventory_lines(facts),
        *_addition_lines(facts),
        *_architecture_lines(facts),
        *_interface_lines(report, facts),
        *_quality_lines(report, facts, counts),
        *_verification_and_question_lines(report),
        *_closing_lines(),
    ]
    return "\n".join(lines)


def _status_can_be_downgraded(tool_status: str, final_status: str) -> bool:
    """判断人工语义结论是否没有抬高静态工具结论。"""
    return (
        tool_status in report_schema.STATUS_RANK
        and final_status in report_schema.STATUS_RANK
        and report_schema.STATUS_RANK[final_status] <= report_schema.STATUS_RANK[tool_status]
    )


def _replace_front_matter(text: str, facts: ReportFacts, tool_status: str) -> str:
    """刷新静态事实字段，同时保留合法的人工降级结论。"""
    current = report_schema.front_matter(text)
    final_status = current.get("final_status", "PENDING")
    if not _status_can_be_downgraded(tool_status, final_status):
        final_status = "PENDING"
    replacement = (
        "---\n"
        f"report_schema: {report_schema.REPORT_SCHEMA}\n"
        f"change_digest: {facts.digest}\n"
        f"tool_status: {tool_status}\n"
        f"final_status: {final_status}\n"
        "---\n"
    )
    if report_schema.FRONT_MATTER.match(text):
        return report_schema.FRONT_MATTER.sub(replacement, text, count=1)
    return replacement + "\n" + text


def _refresh_auto_blocks(existing: str, fresh: str) -> str:
    """只替换自动事实块，避免模型完成的人工说明被下一轮覆盖。"""
    fresh_blocks = {
        match.group("name"): match.group(0) for match in report_schema.AUTO_BLOCK.finditer(fresh)
    }
    result = existing
    for name, block in fresh_blocks.items():
        pattern = re.compile(
            rf"<!-- RQG:AUTO:BEGIN {re.escape(name)} -->.*?"
            rf"<!-- RQG:AUTO:END {re.escape(name)} -->",
            re.DOTALL,
        )
        if pattern.search(result) is None:
            return fresh
        result = pattern.sub(lambda _match, value=block: value, result, count=1)
    return result


def render_strict_modification_report(
    report: ScanReport,
    existing: str | None = None,
    revision: str = "HEAD",
) -> str:
    """
    生成或刷新固定十节、全量接口披露的修改说明。

    Args:
        report: 完整代码扫描结果。
        existing: 已由模型填写的同 schema 报告；自动事实会刷新，人工内容保留。
        revision: Git 比较基线。

    Returns:
        可继续填写或直接交给静态 validator 的 Markdown 文本。
    """
    fresh = _fresh_template(report, revision)
    if (
        not existing
        or report_schema.front_matter(existing).get("report_schema") != report_schema.REPORT_SCHEMA
    ):
        return fresh
    facts = build_report_facts(report, revision)
    refreshed = _replace_front_matter(existing, facts, code_status(report))
    return _refresh_auto_blocks(refreshed, fresh)


def report_final_status(text: str) -> str:
    """读取模型在 front matter 中提交的最终语义结论。

    Args:
        text: 完整修改说明 Markdown。

    Returns:
        模型填写的 `ACCEPT`、`REVIEW_REQUIRED` 或 `REJECT`；缺失时返回空字符串。
    """
    return report_schema.front_matter(text).get("final_status", "")


def _coverage_finding(
    expected: set[str],
    actual: list[str],
    message: str,
) -> list[Finding]:
    """验证人工表格 ID 与自动事实集合完全相等且没有重复。"""
    if set(actual) == expected and len(actual) == len(set(actual)):
        return []
    return [_report_finding("QG982", message)]


def _duplicate_text_findings(
    values: list[tuple[str, str]],
    label: str,
) -> list[Finding]:
    """拒绝把同一段空泛说明复制到多个披露项。"""
    grouped: dict[str, list[str]] = {}
    for item_id, value in values:
        normalized = re.sub(r"\s+", "", value)
        if normalized not in grouped:
            grouped[normalized] = []
        grouped[normalized].append(item_id)
    findings: list[Finding] = []
    for item_ids in grouped.values():
        if len(item_ids) > 1:
            findings.append(
                _report_finding(
                    "QG982",
                    f"{label}重复复用同一说明：{', '.join(sorted(item_ids))}。必须逐对象给出事实。",
                )
            )
    return findings


def _artifact_row_findings(
    matches: list[re.Match[str]],
    expected: dict[str, str],
) -> list[Finding]:
    """验证交付附件独立披露且不混入生产代码预算。"""
    findings = _coverage_finding(
        set(expected),
        [match.group("id") for match in matches],
        "交付附件表未逐项且唯一覆盖全部 ARTIFACT ID。",
    )
    for match in matches:
        item_id = match.group("id")
        purpose = match.group("purpose")
        if expected.get(item_id) != match.group("path"):
            findings.append(_report_finding("QG982", f"{item_id} 的附件路径与自动事实不一致。"))
        if (
            report_schema.contains_placeholder(purpose)
            or len(purpose.strip()) < report_schema.MIN_MANUAL_TEXT
            or "用途=" not in purpose
            or "来源=" not in purpose
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须分别填写 `用途=` 与 `来源=`，说明附件为何存在。",
                )
            )
        if match.group("status") not in report_schema.VALID_ITEM_STATUSES:
            findings.append(_report_finding("QG982", f"{item_id} 使用了非法状态。"))
    return findings


def _file_row_findings(
    matches: list[re.Match[str]],
    expected: dict[str, str],
    deletion_only_ids: frozenset[str],
) -> list[Finding]:
    """验证全部变更文件都有唯一且具体的编辑事实。"""
    findings = _coverage_finding(
        set(expected),
        [match.group("id") for match in matches],
        "文件必要性表未逐项且唯一覆盖全部 FILE ID。",
    )
    explanations: list[tuple[str, str]] = []
    for match in matches:
        item_id = match.group("id")
        reason = match.group("reason")
        status = match.group("status")
        expected_path = expected.get(item_id)
        if expected_path != match.group("path"):
            findings.append(_report_finding("QG982", f"{item_id} 的文件路径与自动事实不一致。"))
        deletion_only = item_id in deletion_only_ids
        if deletion_only:
            if (
                reason != "NOT_APPLICABLE（纯接口删除；删除清单即完整说明）"
                or status != "NOT_APPLICABLE"
            ):
                findings.append(
                    _report_finding(
                        "QG982",
                        f"{item_id} 是纯接口删除文件，只需保留自动生成的删除清单，不要求也不允许伪造删除理由。",
                    )
                )
            continue
        if (
            report_schema.contains_placeholder(reason)
            or len(reason.strip()) < report_schema.MIN_MANUAL_TEXT
            or "事实=" not in reason
            or "范围=" not in reason
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须分别填写 `事实=` 与 `范围=`，说明为什么编辑以及为何没有扩大修改。",
                )
            )
        if status not in report_schema.VALID_ITEM_STATUSES:
            findings.append(_report_finding("QG982", f"{item_id} 使用了非法状态 `{status}`。"))
        explanations.append((item_id, reason))
    findings.extend(_duplicate_text_findings(explanations, "文件编辑事实"))
    return findings


def _test_file_row_findings(
    matches: list[re.Match[str]],
    expected: dict[str, str],
) -> list[Finding]:
    """验证测试文件以独立文件级事实审计且不弱化回归。"""
    findings = _coverage_finding(
        set(expected),
        [match.group("id") for match in matches],
        "测试独立审计表未逐项且唯一覆盖全部 TEST-FILE ID。",
    )
    explanations: list[tuple[str, str]] = []
    for match in matches:
        item_id = match.group("id")
        if expected.get(item_id) != match.group("path"):
            findings.append(_report_finding("QG982", f"{item_id} 的测试文件路径与自动事实不一致。"))
        fields = (
            (
                match.group("purpose"),
                report_schema.REQUIRED_TEST_PURPOSE_MARKERS,
                "测试目的与覆盖行为",
            ),
            (
                match.group("assertion"),
                report_schema.REQUIRED_TEST_ASSERTION_MARKERS,
                "断言变化与弱化判断",
            ),
            (
                match.group("isolation"),
                report_schema.REQUIRED_TEST_ISOLATION_MARKERS,
                "Mock/Stub 与生产路径",
            ),
            (match.group("risk"), report_schema.REQUIRED_TEST_RISK_MARKERS, "风险与结论"),
        )
        for value, markers, label in fields:
            if (
                report_schema.contains_placeholder(value)
                or len(value.strip()) < report_schema.MIN_MANUAL_TEXT
                or any(marker not in value for marker in markers)
            ):
                findings.append(
                    _report_finding(
                        "QG982",
                        f"{item_id} 必须完整填写{label}：{', '.join(markers)}。",
                    )
                )
        status = match.group("status")
        if status not in report_schema.VALID_ITEM_STATUSES:
            findings.append(_report_finding("QG982", f"{item_id} 使用了非法状态 `{status}`。"))
        explanations.append(
            (
                item_id,
                "|".join(
                    (
                        match.group("purpose"),
                        match.group("assertion"),
                        match.group("isolation"),
                        match.group("risk"),
                    )
                ),
            )
        )
    findings.extend(_duplicate_text_findings(explanations, "测试文件审计说明"))
    return findings


def _test_risk_row_findings(
    matches: list[re.Match[str]],
    expected: set[str],
) -> list[Finding]:
    """验证测试弱化风险均有处置和真实验证证据。"""
    findings = _coverage_finding(
        set(expected),
        [match.group("id") for match in matches],
        "测试风险表未逐项且唯一覆盖全部 TEST-RISK ID。",
    )
    for match in matches:
        reason = match.group("reason")
        verification = match.group("verification")
        status = match.group("status")
        if (
            report_schema.contains_placeholder(reason)
            or "处置=" not in reason
            or len(reason) < report_schema.MIN_MANUAL_TEXT
        ):
            findings.append(_report_finding("QG982", f"{match.group('id')} 必须填写 `处置=`。"))
        if (
            report_schema.contains_placeholder(verification)
            or "证据=" not in verification
            or len(verification) < report_schema.MIN_MANUAL_TEXT
        ):
            findings.append(_report_finding("QG982", f"{match.group('id')} 必须填写 `证据=`。"))
        if status not in report_schema.VALID_ITEM_STATUSES:
            findings.append(
                _report_finding("QG982", f"{match.group('id')} 使用了非法状态 `{status}`。")
            )
    return findings


def _addition_row_findings(
    matches: list[re.Match[str]],
    expected: dict[str, str],
) -> list[Finding]:
    """验证每个新增对象都完成删除审判和证据闭环。

    静态门禁只验证事实结构、覆盖集合和可追溯证据是否存在；职责归属是否
    真正合理仍由模型结合代码上下文裁决，避免用关键词正则冒充语义理解。
    """
    findings = _coverage_finding(
        set(expected),
        [match.group("id") for match in matches],
        "新增接口表未逐项且唯一覆盖全部 ADD ID。",
    )
    explanations: list[tuple[str, str]] = []
    for match in matches:
        item_id = match.group("id")
        symbol = match.group("symbol").strip("`")
        reason = match.group("reason")
        existing_check = match.group("existing")
        alternative = match.group("alternative")
        evidence = match.group("evidence")
        status = match.group("status")
        if expected.get(item_id) != symbol:
            findings.append(_report_finding("QG982", f"{item_id} 的符号与自动事实不一致。"))
        if (
            report_schema.contains_placeholder(reason)
            or len(reason.strip()) < report_schema.MIN_ADDITION_FACT_TEXT
            or any(
                marker not in reason for marker in report_schema.REQUIRED_ADDITION_REASON_MARKERS
            )
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须分别填写 `失败=`、`职责=`、`边界=`、`绝对必要性=` 与 `不可替代=`：给出不新增时的可观察失败、唯一职责所有者、相邻层边界，以及为何绝对不能复用或保持原接口。",
                )
            )
        if (
            report_schema.contains_placeholder(existing_check)
            or len(existing_check.strip()) < report_schema.MIN_ADDITION_FACT_TEXT
            or any(
                marker not in existing_check for marker in report_schema.REQUIRED_EXISTING_MARKERS
            )
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须逐项披露 `接口目录=`、`查询=`、`候选=`、`源码=`、`HEAD=`、`父类=`、`MRO=`、`兄弟类=`、`公共能力=`；BM25 只召回候选，必须继续阅读候选源码后才能认定不可复用。",
                )
            )
        if (
            report_schema.contains_placeholder(alternative)
            or len(alternative.strip()) < report_schema.MIN_ADDITION_ALTERNATIVE_TEXT
            or any(
                marker not in alternative for marker in report_schema.REQUIRED_ALTERNATIVE_MARKERS
            )
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须分别完成 `删除=`、`内联=`、`合并=`、`复用=` 四种减法实验，不能只给新增方案找优点。",
                )
            )
        call_chain = evidence.split("调用链=", 1)[-1].split("；验证=", 1)[0].strip()
        verification = (
            evidence.split("验证=", 1)[-1].split("；语义裁决=", 1)[0].strip()
            if "验证=" in evidence
            else ""
        )
        semantic_decision = (
            evidence.split("语义裁决=", 1)[-1].strip() if "语义裁决=" in evidence else ""
        )
        has_chain_shape = (
            "→" in call_chain
            or "->" in call_chain
            or call_chain
            in {
                "无调用方",
                "入口协议直接调用",
                "模块常量无调用链",
            }
        )
        if (
            report_schema.contains_placeholder(evidence)
            or len(evidence.strip()) < report_schema.MIN_ADDITION_FACT_TEXT
            or any(
                marker not in evidence
                for marker in report_schema.REQUIRED_ADDITION_EVIDENCE_MARKERS
            )
            or not has_chain_shape
            or len(verification) < report_schema.MIN_ADDITION_VERIFICATION_TEXT
            or len(semantic_decision) < report_schema.MIN_ADDITION_VERIFICATION_TEXT
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须填写真实 `调用链=`、本轮 `验证=` 和独立 `语义裁决=`；validator 只检查结构，不替模型决定职责是否合理。",
                )
            )
        if status not in {"JUSTIFIED", "BLOCKING"}:
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 对应的新增接口仍存在，只能标记 JUSTIFIED 或 BLOCKING。",
                )
            )
        explanations.append((item_id, "|".join((reason, existing_check, alternative, evidence))))
    findings.extend(_duplicate_text_findings(explanations, "新增对象删除审判"))
    return findings


def _added_count_copy_findings(text: str, facts: ReportFacts) -> list[Finding]:
    """校验模型抄写的新增函数、变量和类数量与工具事实一致。"""
    matches = list(report_schema.ADDED_COUNT_COPY.finditer(text))
    if len(matches) != 1:
        return [
            _report_finding(
                "QG985",
                "必须恰好抄写一次工具给出的新增函数、变量和类数量。",
            )
        ]
    match = matches[0]
    actual = (
        int(match.group("functions")),
        int(match.group("variables")),
        int(match.group("classes")),
    )
    expected = (
        facts.added_function_count,
        facts.added_variable_count,
        facts.added_class_count,
    )
    if actual == expected:
        return []
    return [
        _report_finding(
            "QG985",
            "模型抄写的新增函数、变量或类数量与 Python 接口差分不一致；请直接按自动事实区抄写。",
        )
    ]


def _subtraction_review_findings(text: str, facts: ReportFacts) -> list[Finding]:
    """验证新增对象完成一次全量二次减法复审。"""
    section = _section_body(
        text, report_schema.REQUIRED_SECTIONS[2], report_schema.REQUIRED_SECTIONS[3]
    )
    required_prefixes = ("- 复审范围：", "- 减法结果：", "- 最终结论：")
    lines = [line.strip() for line in section.splitlines()]
    selected = [
        next((line for line in lines if line.startswith(prefix)), "")
        for prefix in required_prefixes
    ]
    if any(
        not line
        or report_schema.contains_placeholder(line)
        or len(line) < report_schema.MIN_SUBTRACTION_REVIEW_TEXT
        for line in selected
    ):
        return [
            _report_finding(
                "QG982",
                "必须完成‘全量二次减法复审’，汇总 ADD 数量、存量 helper、删除/内联/合并结果和最终结论。",
            )
        ]
    scope_line = selected[0]
    if f"ADD={len(facts.additions)}" not in scope_line:
        return [
            _report_finding(
                "QG985",
                "二次减法复审中的 ADD 数量必须与自动接口清单一致。",
            )
        ]
    return []


def _architecture_row_findings(
    matches: list[re.Match[str]],
    expected: dict[str, Finding],
) -> list[Finding]:
    """验证每项继承和公共能力差分都有处置与证据。"""
    findings = _coverage_finding(
        set(expected),
        [match.group("id") for match in matches],
        "父子类与公共抽象表未逐项且唯一覆盖全部 ARCH ID。",
    )
    explanations: list[tuple[str, str]] = []
    for match in matches:
        item_id = match.group("id")
        reason = match.group("reason")
        verification = match.group("verification")
        status = match.group("status")
        if (
            report_schema.contains_placeholder(reason)
            or len(reason.strip()) < report_schema.MIN_MANUAL_TEXT
            or "处置=" not in reason
        ):
            findings.append(_report_finding("QG982", f"{item_id} 必须填写 `处置=` 及其架构事实。"))
        if (
            report_schema.contains_placeholder(verification)
            or len(verification.strip()) < report_schema.MIN_MANUAL_TEXT
            or "证据=" not in verification
        ):
            findings.append(
                _report_finding("QG982", f"{item_id} 必须填写 `证据=` 并指向调用链或验证结果。")
            )
        if status not in report_schema.VALID_ITEM_STATUSES:
            findings.append(_report_finding("QG982", f"{item_id} 使用了非法状态 `{status}`。"))
        architecture_finding = expected.get(item_id)
        if (
            architecture_finding is not None
            and architecture_finding.code == "QG168"
            and status != "BLOCKING"
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 是 QG168 连续单调用链，任何情况下都只能标记 BLOCKING；"
                    "必须压缩调用链并删除中间 helper。",
                )
            )
        explanations.append((item_id, "|".join((reason, verification))))
    findings.extend(_duplicate_text_findings(explanations, "架构处置说明"))
    return findings


def _quality_delta_row_findings(
    matches: list[re.Match[str]],
    expected: dict[str, QualityDelta],
) -> list[Finding]:
    """验证每个新增或恶化问题都被明确标记为不可豁免阻断。"""
    findings = _coverage_finding(
        set(expected),
        [match.group("id") for match in matches],
        "新增或恶化问题表未逐项且唯一覆盖全部 DELTA ID。",
    )
    explanations: list[tuple[str, str]] = []
    for match in matches:
        item_id = match.group("id")
        item = expected.get(item_id)
        reason = match.group("reason")
        alternative = match.group("alternative")
        risk = match.group("risk")
        status = match.group("status")
        minimum = 28 if item and item.finding.severity in {"critical", "error"} else 18
        if (
            report_schema.contains_placeholder(reason)
            or len(reason.strip()) < minimum
            or any(marker not in reason for marker in report_schema.REQUIRED_DELTA_REASON_MARKERS)
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须分别填写 `事实=` 与 `语义裁决=`；静态工具提供事实，模型决定该问题可接受还是阻塞。",
                )
            )
        if (
            report_schema.contains_placeholder(alternative)
            or len(alternative.strip()) < minimum
            or any(
                marker not in alternative
                for marker in report_schema.REQUIRED_DELTA_ALTERNATIVE_MARKERS
            )
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须分别填写 `替代=` 与 `处置=`，说明保留、修复或撤销方向。",
                )
            )
        if (
            report_schema.contains_placeholder(risk)
            or len(risk.strip()) < minimum
            or any(marker not in risk for marker in report_schema.REQUIRED_DELTA_RISK_MARKERS)
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须分别填写 `风险=` 与 `关闭=`，给出后续消除条件。",
                )
            )
        if status != "BLOCKING":
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 是相对 Git 基线新增或升级的问题，QG179 禁止解释豁免；"
                    "只能标记 BLOCKING，并在代码中删除、修复或回退后重新扫描。",
                )
            )
        explanations.append((item_id, "|".join((reason, alternative, risk))))
    findings.extend(_duplicate_text_findings(explanations, "新增或恶化问题阻断说明"))
    return findings


def _debt_summary_findings(text: str, facts: ReportFacts) -> list[Finding]:
    """验证存量债务已削减，或在零削减时给出范围级事实。"""
    findings: list[Finding] = []
    lines = text.splitlines()
    summary_marker = "存量债务削减结论："
    reason_marker = "存量债务未削减说明："
    summary_line = next((line for line in lines if summary_marker in line), "")
    reason_line = next((line for line in lines if reason_marker in line), "")
    summary = summary_line.split(summary_marker, 1)[-1].strip() if summary_line else ""
    reason = reason_line.split(reason_marker, 1)[-1].strip() if reason_line else ""
    if (
        not summary
        or report_schema.contains_placeholder(summary)
        or len(summary) < report_schema.MIN_SUMMARY_TEXT
    ):
        findings.append(_report_finding("QG982", "必须填写存量债务削减结论。"))
    requires_reason = facts.legacy_debt_total > 0 and facts.legacy_debt_reduced == 0
    if requires_reason:
        if (
            report_schema.contains_placeholder(reason)
            or len(reason) < report_schema.MIN_DEBT_REASON_TEXT
            or any(marker not in reason for marker in report_schema.REQUIRED_DEBT_REASON_MARKERS)
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    "本轮未删除或降级任何存量三档问题，必须填写 `原因=`、`范围=`、`最小方案=` 与 `关闭条件=`。",
                )
            )
    elif reason != "NOT_APPLICABLE":
        findings.append(
            _report_finding(
                "QG982",
                "不存在存量债务或本轮已实际削减时，`存量债务未削减说明` 应填写 NOT_APPLICABLE。",
            )
        )
    return findings


def _protocol_row_findings(
    matches: list[re.Match[str]],
    expected: dict[str, Finding],
) -> list[Finding]:
    """验证核心协议字段变化的必要性、兼容性和验证闭环。"""
    findings = _coverage_finding(
        set(expected),
        [match.group("id") for match in matches],
        "核心协议字段审查表未逐项且唯一覆盖全部 PROTOCOL ID。",
    )
    explanations: list[tuple[str, str]] = []
    for match in matches:
        item_id = match.group("id")
        finding = expected.get(item_id)
        symbol = match.group("symbol").strip("`")
        if finding is None or (finding.symbol or finding.path) != symbol:
            findings.append(_report_finding("QG982", f"{item_id} 的协议符号与自动事实不一致。"))
        if finding is None or match.group("fact") != report_schema.escape(finding.message):
            findings.append(_report_finding("QG981", f"{item_id} 的自动协议变化事实被改写。"))
        fields = (
            (
                match.group("reason"),
                ("必要性=", "不可绕过=", "不可复用="),
                "绝对必要性、不可绕过与不可复用说明",
            ),
            (match.group("impact"), ("上下游=", "兼容="), "上下游与兼容说明"),
            (match.group("evidence"), ("验证=", "回滚="), "验证与回滚证据"),
        )
        for value, markers, label in fields:
            if (
                report_schema.contains_placeholder(value)
                or len(value.strip()) < report_schema.MIN_ADDITION_FACT_TEXT
                or any(marker not in value for marker in markers)
            ):
                findings.append(
                    _report_finding(
                        "QG982",
                        f"{item_id} 必须完整填写{label}：{', '.join(markers)}。",
                    )
                )
        status = match.group("status")
        if status not in {"JUSTIFIED", "BLOCKING"}:
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 对应的核心协议变化仍存在，只能标记 JUSTIFIED 或 BLOCKING。",
                )
            )
        explanations.append(
            (
                item_id,
                "|".join((match.group("reason"), match.group("impact"), match.group("evidence"))),
            )
        )
    findings.extend(_duplicate_text_findings(explanations, "核心协议字段审查"))
    return findings


def _manual_row_findings(
    text: str,
    facts: ReportFacts,
) -> list[Finding]:
    """验证生产 FILE/ADD/ARCH/DELTA 与测试独立审计表。"""
    file_matches = list(report_schema.FILE_MANUAL_ROW.finditer(text))
    artifact_matches = list(report_schema.ARTIFACT_MANUAL_ROW.finditer(text))
    test_file_matches = list(report_schema.TEST_FILE_MANUAL_ROW.finditer(text))
    addition_matches = list(report_schema.ADDITION_MANUAL_ROW.finditer(text))
    architecture_matches = list(report_schema.ARCH_MANUAL_ROW.finditer(text))
    delta_matches = list(report_schema.QUALITY_DELTA_ROW.finditer(text))
    interface_matches = list(report_schema.INTERFACE_REVIEW_ROW.finditer(text))
    protocol_matches = list(report_schema.PROTOCOL_REVIEW_ROW.finditer(text))
    test_risk_matches = list(report_schema.TEST_RISK_MANUAL_ROW.finditer(text))
    expected_files = {item.item_id: item.path for item in facts.changed_files}
    deletion_only_ids = frozenset(
        item.item_id for item in facts.changed_files if item.path in facts.deletion_only_paths
    )
    expected_artifacts = {item.item_id: item.path for item in facts.artifact_changed_files}
    expected_test_files = {item.item_id: item.path for item in facts.test_audits}
    expected_additions = {item.item_id: item.change.symbol for item in facts.additions}
    expected_architecture = {
        f"ARCH-{index:03d}": finding for index, finding in enumerate(facts.architecture, 1)
    }
    expected_deltas = {item.item_id: item for item in facts.quality_deltas}
    expected_interfaces = {
        f"INTERFACE-{index:03d}": change
        for index, change in enumerate(facts.parameter_interface_changes, 1)
    }
    expected_protocols = {
        f"PROTOCOL-{index:03d}": finding for index, finding in enumerate(facts.protocol_findings, 1)
    }
    test_risk_count = sum(len(item.findings) for item in facts.test_audits)
    expected_test_risks = {f"TEST-RISK-{index:03d}" for index in range(1, test_risk_count + 1)}
    findings = [
        *_file_row_findings(file_matches, expected_files, deletion_only_ids),
        *_artifact_row_findings(artifact_matches, expected_artifacts),
        *_test_file_row_findings(test_file_matches, expected_test_files),
        *_added_count_copy_findings(text, facts),
        *_addition_row_findings(addition_matches, expected_additions),
        *_subtraction_review_findings(text, facts),
        *_architecture_row_findings(architecture_matches, expected_architecture),
        *_quality_delta_row_findings(delta_matches, expected_deltas),
        *_test_risk_row_findings(test_risk_matches, expected_test_risks),
        *_debt_summary_findings(text, facts),
    ]
    findings.extend(
        _coverage_finding(
            set(expected_interfaces),
            [match.group("id") for match in interface_matches],
            "存量接口修改审查表未逐项且唯一覆盖全部 INTERFACE ID。",
        )
    )
    interface_explanations: list[tuple[str, str]] = []
    for match in interface_matches:
        item_id = match.group("id")
        change = expected_interfaces.get(item_id)
        if change is None or change.symbol != match.group("symbol").strip("`"):
            findings.append(_report_finding("QG982", f"{item_id} 的存量接口符号与自动事实不一致。"))
        fields = (
            (
                match.group("reason"),
                ("绝对必要性=", "不可替代=", "最小方案="),
                "绝对必要性与不可替代",
            ),
            (match.group("compatibility"), ("兼容=", "同步="), "兼容与同步"),
            (match.group("evidence"), ("调用方=", "验证="), "调用方与验证证据"),
        )
        for value, markers, label in fields:
            if (
                report_schema.contains_placeholder(value)
                or len(value.strip()) < report_schema.MIN_ADDITION_FACT_TEXT
                or any(marker not in value for marker in markers)
            ):
                findings.append(
                    _report_finding(
                        "QG982",
                        f"{item_id} 必须完整填写{label}：{', '.join(markers)}。",
                    )
                )
        if match.group("status") not in {"JUSTIFIED", "BLOCKING"}:
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 对应的存量接口修改仍存在，只能标记 JUSTIFIED 或 BLOCKING。",
                )
            )
        interface_explanations.append(
            (
                item_id,
                "|".join(
                    (
                        match.group("reason"),
                        match.group("compatibility"),
                        match.group("evidence"),
                    )
                ),
            )
        )
    findings.extend(_duplicate_text_findings(interface_explanations, "存量接口修改审查"))

    findings.extend(_protocol_row_findings(protocol_matches, expected_protocols))

    if facts.interface_definition_net > 0:
        addition_statuses = {match.group("id"): match.group("status") for match in addition_matches}
        relevant_ids = [
            item.item_id
            for item in facts.additions
            if item.change.kind in report_schema.INTERFACE_DEFINITION_KINDS
        ]
        if any(
            addition_statuses.get(item_id) not in {"JUSTIFIED", "BLOCKING"}
            for item_id in relevant_ids
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    "函数/方法/类净额大于 0 时，每个对应 ADD 都必须完成必要性审判并标记 "
                    "JUSTIFIED 或 BLOCKING；静态结论保持 REVIEW_REQUIRED，不能抬高为 ACCEPT。",
                )
            )
    return findings


def _semantic_heuristic_question_findings(
    status: str,
    fact: str,
    report: ScanReport,
) -> list[Finding]:
    """验证 Agent 文本启发式只依赖静态豁免或 Git 基线授权账本。"""
    required_markers = ("候选=", "语义任务=", "规则机制=", "授权证据=")
    findings: list[Finding] = []
    if any(marker not in fact for marker in required_markers):
        findings.append(
            _report_finding(
                "QG982",
                "Q10 必须分别填写 `候选=`、`语义任务=`、`规则机制=` 与 `授权证据=`。",
            )
        )
        return findings

    candidates = [item for item in report.findings if item.code == "QG178"]
    candidate_locations = [f"{item.path}:{item.line}" for item in candidates]
    missing_locations = [location for location in candidate_locations if location not in fact]
    if missing_locations:
        findings.append(
            _report_finding(
                "QG982",
                "Q10 未逐项覆盖全部 QG178 候选位置：" + ", ".join(missing_locations),
            )
        )

    forbidden_claims = (
        "NOT_REQUIRED_DETERMINISTIC_CONTRACT",
        "用户原文=",
        "授权文件=",
    )
    if any(claim in fact for claim in forbidden_claims):
        findings.append(
            _report_finding(
                "QG982",
                "Q10 不得由模型自行声明确定性协议豁免、用户原文或授权文件；"
                "静态例外只能来自 profile 精确指纹，授权只能来自 Git 基线中预先存在的授权账本。",
            )
        )

    unauthorized = [
        item for item in candidates if not bool(item.evidence.get("authorized_by_baseline", False))
    ]
    if candidates and status == "NOT_APPLICABLE":
        findings.append(
            _report_finding("QG982", "存在 QG178 候选时，Q10 不得标记 NOT_APPLICABLE。")
        )
    if candidates and status == "FIXED":
        findings.append(
            _report_finding(
                "QG982",
                "当前仍存在 QG178 候选时，Q10 不得标记 FIXED；必须删除对应代码后重新扫描。",
            )
        )
    if unauthorized and status != "BLOCKING":
        locations = ", ".join(f"{item.path}:{item.line}" for item in unauthorized)
        findings.append(
            _report_finding(
                "QG982",
                "以下 QG178 候选未获 Git 基线授权，只能 BLOCKING：" + locations,
            )
        )
    if (
        candidates
        and not unauthorized
        and status == "JUSTIFIED"
        and "授权证据=BASELINE_LEDGER" not in fact
    ):
        findings.append(
            _report_finding(
                "QG982",
                "全部候选虽已由 Git 基线账本授权，但 Q10 标记 JUSTIFIED 时必须填写 "
                "`授权证据=BASELINE_LEDGER`。",
            )
        )
    if not candidates and status == "NOT_APPLICABLE" and "候选=NONE" not in fact:
        findings.append(
            _report_finding("QG982", "Q10 标记 NOT_APPLICABLE 时必须明确填写 `候选=NONE`。")
        )
    return findings


def _question_findings(text: str, report: ScanReport) -> list[Finding]:
    """验证通用审判及 profile 专项审判均有事实和独立结论。"""
    findings: list[Finding] = []
    matches = {match.group("title"): match for match in report_schema.QUESTION_BLOCK.finditer(text)}
    fact_values: list[tuple[str, str]] = []
    conclusion_values: list[tuple[str, str]] = []
    for title in report_schema.question_titles(report):
        match = matches.get(title)
        if match is None:
            findings.append(_report_finding("QG980", f"架构与必要性审判缺失或格式错误：{title}"))
            continue
        status = match.group("status")
        fact = match.group("fact").strip()
        conclusion = match.group("conclusion").strip()
        if status not in report_schema.VALID_ITEM_STATUSES:
            findings.append(_report_finding("QG982", f"{title} 使用了非法状态 `{status}`。"))
        if (
            report_schema.contains_placeholder(fact)
            or len(fact) < report_schema.MIN_MANUAL_TEXT
            or report_schema.REQUIRED_QUESTION_FACT_MARKER not in fact
            or report_schema.EVIDENCE_REFERENCE.search(fact) is None
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{title} 必须以 `证据=` 引用 FILE/ADD/ARCH/DELTA/TEST-FILE/TEST-RISK/QG 编号；无对应项时明确写 `无对应自动事实：`。",
                )
            )
        if (
            report_schema.contains_placeholder(conclusion)
            or len(conclusion) < report_schema.MIN_MANUAL_TEXT
            or any(
                marker not in conclusion
                for marker in report_schema.REQUIRED_QUESTION_CONCLUSION_MARKERS
            )
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{title} 必须分别填写 `处理=` 与 `结论=`。",
                )
            )
        if (
            title == report_schema.AGENT_SEMANTIC_HEURISTIC_QUESTION
            and "strict-semantic-question" in report.profile_capabilities
        ):
            findings.extend(_semantic_heuristic_question_findings(status, fact, report))
        fact_values.append((title.split(".", 1)[0], fact))
        conclusion_values.append((title.split(".", 1)[0], conclusion))
    findings.extend(_duplicate_text_findings(fact_values, "架构审判事实依据"))
    findings.extend(_duplicate_text_findings(conclusion_values, "架构审判处理结论"))
    return findings


def _section_body(text: str, heading: str, next_heading: str) -> str:
    """返回两个固定二级标题之间的 Markdown 内容。"""
    start = text.find(heading)
    end = text.find(next_heading, start + len(heading)) if start >= 0 else -1
    if start < 0 or end < 0:
        return ""
    return text[start + len(heading) : end]


def _validation_findings(text: str) -> list[Finding]:
    """验证报告记录了完整且成功的最新验证命令。"""
    section = _section_body(
        text, report_schema.REQUIRED_SECTIONS[6], report_schema.REQUIRED_SECTIONS[7]
    )
    rows = list(report_schema.VALIDATION_ROW.finditer(section))
    if not rows:
        return [
            _report_finding(
                "QG983",
                "验证章节没有记录可解析的实际命令、目的、退出码和结果摘要。",
            )
        ]
    findings: list[Finding] = []
    commands = [row.group("command") for row in rows]
    required_categories = {
        "quality-guard": any(
            "quality_check.py" in command or "quality-guard" in command for command in commands
        ),
        "git diff --check": any("git diff --check" in command for command in commands),
        "项目验证": any(
            token in command
            for command in commands
            for token in ("pytest", "unittest", "compileall", " test", "test_")
        ),
    }
    for category, present in required_categories.items():
        if not present:
            findings.append(_report_finding("QG983", f"验证章节缺少 `{category}` 类命令。"))
    for row in rows:
        values = (row.group("command"), row.group("purpose"), row.group("result"))
        if any(
            report_schema.contains_placeholder(value)
            or len(value.strip()) < report_schema.MIN_VALIDATION_TEXT
            for value in values
        ):
            findings.append(_report_finding("QG983", "验证表仍包含占位或空泛结果。"))
            continue
        if int(row.group("exit")) != 0:
            findings.append(
                _report_finding(
                    "QG983",
                    f"验证命令 `{row.group('command')}` 退出码不是 0，不能声明完成。",
                )
            )
    return findings


def _risk_findings(
    text: str,
    final_status: str,
    report: ScanReport,
) -> list[Finding]:
    """验证剩余风险章节与人工语义结论一致。"""
    section = _section_body(
        text, report_schema.REQUIRED_SECTIONS[8], report_schema.REQUIRED_SECTIONS[9]
    )
    rows = list(report_schema.RISK_ROW.finditer(section))
    if not rows:
        return [_report_finding("QG982", "剩余风险章节缺少可解析的风险行。")]
    findings: list[Finding] = []
    severities: list[str] = []
    for row in rows:
        severity = row.group("severity")
        severities.append(severity)
        object_text = row.group("object")
        reason = row.group("reason")
        proposal = row.group("proposal")
        object_min = 4 if severity == "NONE" else report_schema.MIN_RISK_TEXT
        if (
            report_schema.contains_placeholder(object_text)
            or len(object_text.strip()) < object_min
            or report_schema.contains_placeholder(reason)
            or len(reason.strip()) < report_schema.MIN_RISK_TEXT
            or report_schema.contains_placeholder(proposal)
            or len(proposal.strip()) < report_schema.MIN_RISK_TEXT
        ):
            findings.append(_report_finding("QG982", "剩余风险表仍包含占位或空泛说明。"))
    has_open_quality = bool(
        _current_quality_findings(report) or _current_test_quality_findings(report)
    )
    has_declared_risk = any(item != "NONE" for item in severities)
    if final_status == "ACCEPT" and has_declared_risk:
        findings.append(
            _report_finding("QG982", "代码结论为 ACCEPT 时不得继续声明三档未解决问题。")
        )
    if final_status in {"REVIEW_REQUIRED", "REJECT"} and has_open_quality and not has_declared_risk:
        findings.append(
            _report_finding(
                "QG982",
                "当前仍有三档问题时，剩余风险表必须至少列出一项代表风险和关闭建议。",
            )
        )
    return findings


def _commit_findings(text: str) -> list[Finding]:
    """验证提交章节包含可直接执行的多行中文 commit 命令。"""
    match = report_schema.COMMIT_BLOCK.search(text)
    if match is None:
        return [_report_finding("QG984", "提交章节缺少多行中文 `git commit -m` 命令。")]
    command = match.group("command")
    lines = command.splitlines()
    subject = lines[0] if lines else ""
    bullets = [line for line in lines[1:] if line.startswith("- ")]
    subject_ok = bool(
        re.search(r'^git commit -m "[a-z]+(?:\([^)]+\))?!?:\s*.*[\u4e00-\u9fff]', subject)
    )
    valid = (
        subject_ok
        and len(bullets) >= report_schema.MIN_COMMIT_BULLETS
        and all(report_schema.CHINESE.search(line) for line in bullets)
        and command.endswith('"')
    )
    if valid:
        return []
    return [
        _report_finding(
            "QG984",
            "提交命令必须包含中文主题和至少两条中文多行摘要，并保持一个完整双引号参数。",
        )
    ]


def _report_finding(code: str, message: str) -> Finding:
    """构造定位到修改说明的高置信 Critical。"""
    suggestions = {
        "QG980": "按固定十节、十项完整审判模板重写，不得改标题或顺序。",
        "QG981": "重新运行 quality-guard 刷新自动事实，不得手改 AUTO 区块。",
        "QG982": "逐项填写生产 FILE/ADD/ARCH/DELTA、测试 TEST-FILE/TEST-RISK、存量削减结论、通用审判与 profile 专项审判；禁止遗漏、伪造授权或空泛论证。",
        "QG983": "执行最新验证命令并记录真实退出码和结果。",
        "QG984": "填写可直接执行的多行中文 git commit -m 命令。",
        "QG985": "直接抄写工具统计的新增函数、变量、类数量，并保证二次减法复审数量一致。",
    }
    return Finding(
        code=code,
        severity="critical",
        confidence="high",
        path=report_schema.REPORT_FILENAME,
        line=1,
        column=1,
        message=message,
        suggestion=suggestions[code],
    )


def validate_modification_report(
    text: str,
    report: ScanReport,
    revision: str = "HEAD",
) -> list[Finding]:
    """
    静态验证修改说明的章节、事实块、全量披露、验证和 commit。

    Args:
        text: 待验证 Markdown。
        report: 生成自动事实所依据的代码扫描结果。
        revision: Git 比较基线。

    Returns:
        报告契约违规列表；空列表表示 Markdown 本身通过门禁。
    """
    facts = build_report_facts(report, revision)
    findings: list[Finding] = []
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    if (
        text.count(report_schema.REPORT_TITLE) != 1
        or tuple(headings) != report_schema.REQUIRED_SECTIONS
    ):
        findings.append(
            _report_finding(
                "QG980",
                "修改说明必须且只能包含固定十个二级章节，并保持名称与顺序完全一致。",
            )
        )
    for title in report_schema.question_titles(report):
        if text.count(f"### {title}") != 1:
            findings.append(_report_finding("QG980", f"架构与必要性审判缺失或重复：{title}"))
    front = report_schema.front_matter(text)
    tool_status = code_status(report)
    final_status = front.get("final_status", "")
    if (
        front.get("report_schema") != report_schema.REPORT_SCHEMA
        or front.get("change_digest") != facts.digest
        or front.get("tool_status") != tool_status
    ):
        findings.append(
            _report_finding(
                "QG981",
                "schema、change_digest 或 tool_status 与当前 Git/接口/架构静态事实不一致。",
            )
        )
    if final_status not in report_schema.VALID_FINAL_STATUSES or not _status_can_be_downgraded(
        tool_status, final_status
    ):
        findings.append(
            _report_finding(
                "QG982",
                "final_status 只能等于或严于 tool_status：允许模型基于语义降级，禁止把静态 REJECT/REVIEW_REQUIRED 抬高。",
            )
        )
    expected_auto = {
        match.group("name"): match.group("body")
        for match in report_schema.AUTO_BLOCK.finditer(_fresh_template(report, revision))
    }
    actual_matches = list(report_schema.AUTO_BLOCK.finditer(text))
    actual_auto = {match.group("name"): match.group("body") for match in actual_matches}
    if len(actual_matches) != len(actual_auto) or actual_auto != expected_auto:
        findings.append(_report_finding("QG981", "自动事实区块缺失、重复或被改写。"))
    findings.extend(_manual_row_findings(text, facts))
    findings.extend(_question_findings(text, report))
    findings.extend(_validation_findings(text))
    findings.extend(_risk_findings(text, final_status, report))
    findings.extend(_commit_findings(text))

    manual_markers = (
        "行为需求（WHAT/WHY）：",
        "可验证场景（GIVEN/WHEN/THEN）：",
        "设计边界与唯一所有者：",
        "本轮非目标：",
        "调用方、README、Prompt、配置与测试同步结论：",
        "每一档的修复、阻塞或暂缓事实及剩余数量：",
    )
    for marker in manual_markers:
        line = next((item for item in text.splitlines() if marker in item), "")
        value = line.split(marker, 1)[-1].strip() if line else ""
        if (
            not value
            or report_schema.contains_placeholder(value)
            or len(value) < report_schema.MIN_SUMMARY_TEXT
        ):
            findings.append(_report_finding("QG982", f"必填结论缺失或仍是占位：{marker}"))
    final_line = next(
        (item for item in text.splitlines() if item.startswith("- 最终人工结论：")),
        "",
    )
    manual_final_status = final_line.split("：", 1)[-1].strip() if final_line else ""
    if (
        manual_final_status not in report_schema.VALID_FINAL_STATUSES
        or manual_final_status != final_status
    ):
        findings.append(
            _report_finding(
                "QG982",
                "正文最终人工结论必须与 front matter 的 final_status 一致。",
            )
        )
    item_statuses = [
        *(match.group("status") for match in report_schema.FILE_MANUAL_ROW.finditer(text)),
        *(match.group("status") for match in report_schema.ARTIFACT_MANUAL_ROW.finditer(text)),
        *(match.group("status") for match in report_schema.TEST_FILE_MANUAL_ROW.finditer(text)),
        *(match.group("status") for match in report_schema.ADDITION_MANUAL_ROW.finditer(text)),
        *(match.group("status") for match in report_schema.ARCH_MANUAL_ROW.finditer(text)),
        *(match.group("status") for match in report_schema.QUALITY_DELTA_ROW.finditer(text)),
        *(match.group("status") for match in report_schema.INTERFACE_REVIEW_ROW.finditer(text)),
        *(match.group("status") for match in report_schema.PROTOCOL_REVIEW_ROW.finditer(text)),
        *(match.group("status") for match in report_schema.TEST_RISK_MANUAL_ROW.finditer(text)),
        *(match.group("status") for match in report_schema.QUESTION_BLOCK.finditer(text)),
    ]
    has_blocking = "BLOCKING" in item_statuses
    if has_blocking and final_status != "REJECT":
        findings.append(
            _report_finding(
                "QG982",
                "存在 BLOCKING 项时 final_status 和最终人工结论必须为 REJECT。",
            )
        )
    if final_status == "REJECT" and not has_blocking:
        findings.append(
            _report_finding(
                "QG982",
                "人工结论为 REJECT 时，至少一个 ADD/ARCH/DELTA/TEST-RISK/审判项必须标记 BLOCKING 并给出证据。",
            )
        )

    unique: dict[str, Finding] = {}
    for finding in findings:
        unique[f"{finding.code}:{finding.message}"] = finding
    return sorted(unique.values(), key=lambda item: (item.code, item.message))
