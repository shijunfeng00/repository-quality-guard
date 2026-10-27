from __future__ import annotations

from .config import is_test_path
from .model import Finding, ScanReport
from .report_schema import BASELINE_GATE_EXEMPT_CODES, SEVERITY_RANK

_ABSOLUTE_BLOCKERS = frozenset(
    {"QG149", "QG168", "QG179", "QG183", "QG186", "QG187", "QG189", "QG190"}
)


def absolute_blocker_codes() -> frozenset[str]:
    """返回当前工作树存在即阻断的稳定规则集合。

    Returns:
        不允许被普通 Delta 或语义说明抵消的绝对阻断规则编码。
    """
    return _ABSOLUTE_BLOCKERS


def is_absolute_blocker_finding(
    finding: Finding,
    report: ScanReport,
    *,
    test_finding: bool = False,
) -> bool:
    """判断单条 Finding 是否按 code_status 的规则直接构成绝对阻断。

    Args:
        finding: 当前报告中的生产或测试 Finding。
        report: 包含项目 Profile 与完整 Finding 集合的扫描报告。
        test_finding: Finding 是否来自独立的 test_findings 集合。

    Returns:
        bool: 该 Finding 是否会通过绝对阻断路径使静态门禁 REJECT。
    """
    evidence = finding.evidence
    if test_finding:
        return (
            finding.code
            in {"QG240", "QG241", "QG242", "QG243", "QG244", "QG245", "QG246"}
            and evidence.get("absolute_blocker") is True
        )
    return evidence.get("absolute_blocker") is True or (
        finding.code in _ABSOLUTE_BLOCKERS
        and not is_test_path(finding.path, report.project_name)
    )


def code_status(report: ScanReport) -> str:
    """按生产质量零回归和接口人工审查计算三态静态结论。

    Args:
        report: 包含当前发现、Git 质量基线和接口差分的扫描报告。

    Returns:
        ``ACCEPT``、``REVIEW_REQUIRED`` 或 ``REJECT``。
    """
    interface_diff = report.interface_diff
    # The same predicate is consumed by compact_report so the displayed table
    # cannot claim a finding is absolute when the gate treats it as nonblocking.
    absolute_test_contract = any(
        is_absolute_blocker_finding(finding, report, test_finding=True)
        for finding in report.test_findings
    )
    absolute_code = any(
        is_absolute_blocker_finding(finding, report) for finding in report.findings
    )
    interface_errors = bool(interface_diff is not None and interface_diff.errors)
    if any(
        (report.baseline_error, absolute_test_contract, absolute_code, interface_errors)
    ):
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
    # Native compilation/coverage belongs to build diagnostics, not the
    # repository's static code-quality verdict. Keep those observations in
    # the report without escalating ACCEPT to REVIEW_REQUIRED.
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
    if report.baseline is None and current_quality:
        return "REVIEW_REQUIRED"
    return "REVIEW_REQUIRED" if has_review else "ACCEPT"
