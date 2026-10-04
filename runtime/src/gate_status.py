from __future__ import annotations

from .config import is_test_path
from .model import ScanReport
from .report_schema import BASELINE_GATE_EXEMPT_CODES, SEVERITY_RANK

_ABSOLUTE_BLOCKERS = frozenset(
    {"QG168", "QG179", "QG183", "QG186", "QG187", "QG189", "QG190"}
)


def absolute_blocker_codes() -> frozenset[str]:
    """返回当前工作树存在即阻断的稳定规则集合。

    Returns:
        不允许被普通 Delta 或语义说明抵消的绝对阻断规则编码。
    """
    return _ABSOLUTE_BLOCKERS


def code_status(report: ScanReport) -> str:
    """按生产质量零回归和接口人工审查计算三态静态结论。

    Args:
        report: 包含当前发现、Git 质量基线和接口差分的扫描报告。

    Returns:
        ``ACCEPT``、``REVIEW_REQUIRED`` 或 ``REJECT``。
    """
    interface_diff = report.interface_diff
    absolute_evidence = any(
        all(
            (
                "absolute_blocker" in finding.evidence,
                finding.evidence.get("absolute_blocker") is True,
            )
        )
        for finding in report.findings
    )
    absolute_code = any(
        all(
            (
                finding.code in _ABSOLUTE_BLOCKERS,
                not is_test_path(finding.path, report.project_name),
            )
        )
        for finding in report.findings
    )
    interface_errors = bool(interface_diff is not None and interface_diff.errors)
    if any((report.baseline_error, absolute_evidence, absolute_code, interface_errors)):
        return "REJECT"
    review_changes = ()
    protocol_review = False
    if interface_diff is not None:
        review_changes = tuple(
            change
            for change in interface_diff.changes
            if all(
                (
                    not is_test_path(change.path, report.project_name),
                    change.change != "removed",
                    change.kind != "file",
                )
            )
        )
        protocol_review = any(
            all(
                (
                    finding.code == "QG182",
                    not is_test_path(finding.path, report.project_name),
                )
            )
            for finding in interface_diff.contract_findings
        )
    has_review = bool(review_changes or protocol_review)
    current_quality = any(
        all(
            (
                finding.severity in SEVERITY_RANK,
                not finding.code.startswith(("QG98", "QG99")),
                finding.code not in BASELINE_GATE_EXEMPT_CODES,
            )
        )
        for finding in report.findings
    )
    if all((report.baseline is None, any((current_quality, has_review)))):
        return "REJECT"
    return "REVIEW_REQUIRED" if has_review else "ACCEPT"
