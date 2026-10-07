"""Immutable Git, interface and debt facts consumed by report rendering and validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import report_schema
from .config import is_test_path, is_tool_generated_path
from .git_utils import run_readonly_git
from .model import Finding, InterfaceChange, ScanReport
from .test_audit import TestChangeAudit, build_test_change_audits


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
class HistoricalDebt:
    """
    表示一项仍存在的普通 C/E/W 存量债务及其本轮责任范围。

    Attributes:
        item_id: 由 finding 稳定指纹派生的 DEBT 标识。
        finding: 当前仍存在的质量问题。
        scope: `TOUCHED`、`UNTOUCHED` 或 `SELECTED_SCOPE` 责任分类。
    """

    item_id: str
    finding: Finding
    scope: str


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
        debt_mode: 存量债务责任模式。
        historical_debt: 当前仍存在的存量普通 C/E/W。
        legacy_debt_total: Git 基线中的三档问题数量。
        legacy_debt_reduced: 已删除或降级的存量问题数量。
        current_ordinary_debt_total: 当前普通 C/E/W 总数。
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
    debt_mode: str
    historical_debt: tuple[HistoricalDebt, ...]
    legacy_debt_total: int
    legacy_debt_reduced: int
    current_ordinary_debt_total: int
    digest: str


def _numstat_changes(root: Path, revision: str) -> dict[str, tuple[int, int]]:
    """Parse ``git diff --numstat -z`` into target-path line deltas."""
    result = run_readonly_git(root, ("diff", "--numstat", "-z", revision, "--"))
    if result.returncode != 0:
        return {}
    numbers: dict[str, tuple[int, int]] = {}
    tokens = result.stdout.split("\0")
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
    return numbers


def _name_status_changes(root: Path, revision: str) -> dict[str, str]:
    """Parse ``git diff --name-status -z`` into target-path status codes."""
    result = run_readonly_git(root, ("diff", "--name-status", "-z", revision, "--"))
    if result.returncode != 0:
        return {}
    statuses: dict[str, str] = {}
    tokens = result.stdout.split("\0")
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
    return statuses


def collect_changed_files(root: Path, revision: str) -> tuple[ChangedFile, ...]:
    """读取含 rename/untracked 的完整 Git 文件变化，并排除工具产物。

    ``git diff --numstat`` 的非 ``-z`` rename 会把路径压成 ``{old => new}``，
    不能与 ``--name-status`` 的新路径直接 join。这里统一消费 NUL 协议，rename/copy
    都以目标路径作为唯一事实，避免报告把一次移动重复统计成两项。

    Args:
        root: Git 工作树根目录。
        revision: 用于比较当前工作树的基线 revision。

    Returns:
        已规范化并排除工具产物的文件变化元组。
    """
    numbers = _numstat_changes(root, revision)
    statuses = _name_status_changes(root, revision)
    untracked = run_readonly_git(root, ("ls-files", "--others", "--exclude-standard"))
    if untracked.returncode == 0:
        for path in filter(None, untracked.stdout.splitlines()):
            statuses[path] = "A?"
            numbers[path] = (report_schema.line_count(root / path), 0)
    excluded = {report_schema.REPORT_FILENAME, "修改说明报告.md"}
    generated_parts = {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
    paths = sorted(
        path
        for path in set(numbers) | set(statuses)
        if path not in excluded
        and not is_tool_generated_path(path)
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
    return any(
        lowered.endswith(suffix) for suffix in report_schema.DELIVERY_ARTIFACT_SUFFIXES
    )


def _added_interfaces(report: ScanReport) -> tuple[AddedInterface, ...]:
    """为全部新增文件、类、函数、方法和字段分配稳定 ADD 编号。"""
    changes = () if report.interface_diff is None else report.interface_diff.changes
    added = sorted(
        (
            item
            for item in changes
            if item.change == "added"
            and not is_test_path(item.path, report.project_name)
        ),
        key=lambda item: (item.path, item.kind, item.symbol),
    )
    return tuple(
        AddedInterface(item_id=f"ADD-{index:03d}", change=item)
        for index, item in enumerate(added, 1)
    )


def _addition_kind_counts(
    additions: tuple[AddedInterface, ...],
) -> tuple[int, int, int]:
    """统计生产新增函数、变量和类数量。

    Args:
        additions: 自动接口差分生成的生产 ADD 清单。

    Returns:
        依次返回函数（含方法）、变量（含成员字段）和类数量。
    """
    function_count = sum(
        item.change.kind in {"function", "method"} for item in additions
    )
    variable_count = sum(
        item.change.kind in {"global_variable", "member_variable"} for item in additions
    )
    class_count = sum(item.change.kind == "class" for item in additions)
    return function_count, variable_count, class_count


def current_quality_findings(report: ScanReport) -> dict[str, Finding]:
    """Collect ordinary production debt by its stable finding fingerprint.

    Args:
        report: Full scan including ordinary and separately governed findings.

    Returns:
        C/E/W findings after the existing report/interface gate exclusions.
    """
    return {
        finding.fingerprint: finding
        for finding in report.findings
        if finding.severity in report_schema.SEVERITY_RANK
        and not finding.code.startswith("QG98")
        and finding.code not in report_schema.BASELINE_GATE_EXEMPT_CODES
    }


def current_test_quality_findings(report: ScanReport) -> dict[str, Finding]:
    """Collect ordinary debt in the separate test and tracing inventory.

    Args:
        report: Full scan with the nonblocking-domain findings attached.

    Returns:
        C/E/W test findings keyed by fingerprint using the production exclusions.
    """
    return {
        finding.fingerprint: finding
        for finding in report.test_findings
        if finding.severity in report_schema.SEVERITY_RANK
        and not finding.code.startswith("QG98")
        and finding.code not in report_schema.BASELINE_GATE_EXEMPT_CODES
    }


def _quality_delta_items(
    report: ScanReport, tests: bool = False
) -> tuple[QualityDelta, ...]:
    """生成生产或测试范围相对 Git 基线新增、升级的问题清单。"""
    baseline_report = report.test_baseline if tests else report.baseline
    if baseline_report is None:
        return ()
    baseline = baseline_report.finding_severities
    current = (
        current_test_quality_findings(report)
        if tests
        else current_quality_findings(report)
    )
    deltas: list[tuple[Finding, str, str]] = []
    for fingerprint, finding in current.items():
        previous = baseline.get(fingerprint)
        if previous is None:
            deltas.append((finding, "INTRODUCED", "NONE"))
        elif (
            report_schema.SEVERITY_RANK[finding.severity]
            > report_schema.SEVERITY_RANK[previous]
        ):
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


def _historical_debt_items(
    report: ScanReport, changed_paths: frozenset[str]
) -> tuple[HistoricalDebt, ...]:
    """Classify still-present ordinary debt without treating new/worsened debt as history."""
    current = current_quality_findings(report)
    if report.baseline is None:
        rows = [
            HistoricalDebt(
                item_id="DEBT-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:10],
                finding=finding,
                scope="SELECTED_SCOPE",
            )
            for fingerprint, finding in current.items()
        ]
    else:
        baseline = report.baseline.finding_severities
        rows = []
        for fingerprint, finding in current.items():
            previous = baseline.get(fingerprint)
            if previous is None:
                continue
            if (
                report_schema.SEVERITY_RANK[finding.severity]
                > report_schema.SEVERITY_RANK[previous]
            ):
                continue
            rows.append(
                HistoricalDebt(
                    item_id="DEBT-"
                    + hashlib.sha256(fingerprint.encode()).hexdigest()[:10],
                    finding=finding,
                    scope=("TOUCHED" if finding.path in changed_paths else "UNTOUCHED"),
                )
            )
    return tuple(
        sorted(
            rows,
            key=lambda item: (
                -report_schema.SEVERITY_RANK[item.finding.severity],
                item.finding.path,
                item.finding.line,
                item.finding.code,
                item.item_id,
            ),
        )
    )


def _legacy_debt_reduced_count(report: ScanReport) -> int:
    """统计相对基线已经删除或降级的存量三档问题。"""
    if report.baseline is None:
        return 0
    current = current_quality_findings(report)
    reduced = 0
    for fingerprint, previous in report.baseline.finding_severities.items():
        finding = current.get(fingerprint)
        if (
            finding is None
            or report_schema.SEVERITY_RANK[finding.severity]
            < report_schema.SEVERITY_RANK[previous]
        ):
            reduced += 1
    return reduced


def build_report_facts(
    report: ScanReport, revision: str = "HEAD", debt_mode: str = "progressive"
) -> ReportFacts:
    """根据生产扫描、测试扫描和 Git 差分生成严格报告事实。

    Args:
        report: 包含生产、测试、接口和基线结果的完整扫描报告。
        revision: 用于比较工作区变化的 Git 基线。
        debt_mode: `progressive` 或 `cleanup` 存量债务责任策略。

    Returns:
        已分离生产与测试事实并计算稳定摘要的报告事实。
    """
    if debt_mode not in {"progressive", "cleanup"}:
        raise ValueError(f"unsupported debt mode: {debt_mode!r}")
    architecture_codes = {"QG162", "QG163", "QG164", "QG165", "QG166", "QG167", "QG168"}
    architecture = tuple(
        sorted(
            (item for item in report.findings if item.code in architecture_codes),
            key=lambda item: (item.path, item.line, item.code, item.symbol),
        )
    )
    all_changed_files = collect_changed_files(report.root, revision)
    artifact_raw = tuple(
        item for item in all_changed_files if _is_delivery_artifact(item.path)
    )
    production_raw = tuple(
        item
        for item in all_changed_files
        if not is_test_path(item.path, report.project_name)
        and not _is_delivery_artifact(item.path)
    )
    test_raw = tuple(
        item
        for item in all_changed_files
        if is_test_path(item.path, report.project_name)
        and not _is_delivery_artifact(item.path)
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
    added_function_count, added_variable_count, added_class_count = (
        _addition_kind_counts(additions)
    )
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
        item.change == "removed"
        and item.kind in report_schema.INTERFACE_DEFINITION_KINDS
        for item in production_interface_changes
    )
    interface_definition_net = (
        interface_added_definitions - interface_removed_definitions
    )
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
            change.change == "removed"
            and change.kind in report_schema.INTERFACE_DEFINITION_KINDS
            for change in changes_by_path[item.path]
        )
    )
    quality_deltas = _quality_delta_items(report)
    test_quality_deltas = _quality_delta_items(report, tests=True)
    changed_paths = frozenset(item.path for item in changed_files)
    historical_debt = _historical_debt_items(report, changed_paths)
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
    current_ordinary_debt_total = len(current_quality_findings(report))
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
                "after": item.change.after.to_dict()
                if item.change.after is not None
                else None,
            }
            for item in additions
        ],
        "added_counts": {
            "functions": added_function_count,
            "variables": added_variable_count,
            "classes": added_class_count,
        },
        "test_interface_changes": [item.to_dict() for item in test_interface_changes],
        "parameter_interface_changes": [
            item.to_dict() for item in parameter_interface_changes
        ],
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
        "debt_mode": debt_mode,
        "historical_debt": [
            {
                "id": item.item_id,
                "scope": item.scope,
                "finding": item.finding.to_dict(),
            }
            for item in historical_debt
        ],
        "legacy_debt_total": legacy_debt_total,
        "legacy_debt_reduced": legacy_debt_reduced,
        "current_ordinary_debt_total": current_ordinary_debt_total,
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
        debt_mode=debt_mode,
        historical_debt=historical_debt,
        legacy_debt_total=legacy_debt_total,
        legacy_debt_reduced=legacy_debt_reduced,
        current_ordinary_debt_total=current_ordinary_debt_total,
        digest=digest,
    )
