"""Git-baseline quality contracts with explicit independent QG169/QG179 owners."""

from __future__ import annotations

from dataclasses import dataclass

from .core_rule_contract import CoreQualityRule
from .model import Finding, ScanReport


@dataclass(frozen=True, slots=True)
class QualityRegressionContext:
    """Inputs for the QG179 regression ledger.

    The completed report provides current findings and frozen baseline
    severities; interface_codes excludes policy-specific QGs counted elsewhere.
    """

    report: ScanReport
    interface_codes: frozenset[str]


class DocstringRegressionRule(CoreQualityRule[ScanReport]):
    """Detect newly undocumented definitions at the repository boundary.

    This owner compares QG027 identities across revisions and produces an
    aggregate Critical only when a new missing-docstring fingerprint appears.
    """

    code = "QG169"

    def evaluate(self, report: ScanReport) -> list[Finding]:
        """Report newly undocumented definitions using stable QG027 fingerprints.

        Compare the current findings with the recorded Git baseline, not with a
        percentage: removing documented helpers does not hide newly missing docs.

        Args:
            report: Completed scan including current findings and baseline
                finding severities, or an absent baseline for a standalone scan.

        Returns:
            One aggregate QG169 Critical finding identifying introduced symbols,
            or an empty list when no baseline regression is demonstrable.
        """
        new_findings: list[Finding] = []
        baseline = report.baseline
        if baseline is None:
            return []
        baseline_missing = {
            fingerprint
            for fingerprint in baseline.finding_severities
            if ":QG027:" in fingerprint
        }
        current_missing = {
            finding.fingerprint: finding
            for finding in report.findings
            if finding.code == "QG027"
        }
        introduced = sorted(set(current_missing) - baseline_missing)
        if not introduced:
            return []
        introduced_findings = [
            current_missing[fingerprint] for fingerprint in introduced
        ]
        symbols = [
            finding.symbol or f"{finding.path}:{finding.line}"
            for finding in introduced_findings
        ]
        new_findings.append(
            Finding(
                code="QG169",
                severity="critical",
                confidence="high",
                path=".",
                line=1,
                column=1,
                symbol="repository.missing_docstrings",
                message=(
                    f"相对 Git 基线 `{baseline.revision}` 新增 "
                    f"{len(introduced_findings)} 个缺失 docstring 的生产定义。"
                ),
                suggestion=(
                    "为新增缺失定义补充准确 docstring，或删除不必要定义。覆盖率百分比仅展示，"
                    "不得通过扩写无关 docstring 抵消缺失对象。"
                ),
                evidence={
                    "baseline_revision": baseline.revision,
                    "introduced_missing_count": len(introduced_findings),
                    "introduced_missing_symbols": symbols,
                    "introduced_missing_fingerprints": introduced,
                    "baseline_coverage_percent": baseline.docstring_coverage,
                    "current_coverage_percent": report.docstring_coverage,
                    "coverage_percent_is_informational": True,
                },
            )
        )
        return new_findings


class QualityFingerprintRegressionRule(CoreQualityRule[QualityRegressionContext]):
    """Enforce the non-offsetting C/E/W fingerprint regression gate.

    Ignore baseline improvements when deciding whether new or upgraded
    production quality findings require a blocking QG179 explanation.
    """

    code = "QG179"

    def evaluate(self, context: QualityRegressionContext) -> list[Finding]:
        """Flag each introduced or severity-worsened C/E/W finding fingerprint.

        Unrelated debt reductions cannot offset regression. Interface-specific
        codes and explicit QG179-exempt facts are excluded from this ledger.

        Args:
            context: Completed report plus the set of interface-policy codes
                evaluated independently by the interface change gates.

        Returns:
            A single QG179 Critical finding with introduced/worsened details,
            or no finding when the baseline is absent or all fingerprints hold.
        """
        report = context.report
        new_findings: list[Finding] = []
        baseline = report.baseline
        _INTERFACE_POLICY_CODES = context.interface_codes
        if baseline is None:
            return []
        severities = ("critical", "error", "warning")
        current_counts = dict.fromkeys(severities, 0)
        current_findings: dict[str, Finding] = {}
        for finding in report.findings:
            if any(
                (
                    finding.code.startswith("QG98"),
                    finding.code == "QG179",
                    finding.code in _INTERFACE_POLICY_CODES,
                    finding.evidence.get("qg179_exempt") is True,
                    finding.severity not in current_counts,
                )
            ):
                continue
            current_counts[finding.severity] += 1
            current_findings[finding.fingerprint] = finding
        report.quality_count_deltas = {
            severity: current_counts[severity] - baseline.summary[severity]
            for severity in severities
        }

        introduced: list[Finding] = []
        worsened: list[tuple[Finding, str]] = []
        rank = {"warning": 1, "error": 2, "critical": 3}
        for fingerprint, finding in current_findings.items():
            previous = baseline.finding_severities.get(fingerprint)
            if previous is None:
                introduced.append(finding)
            elif rank[finding.severity] > rank[previous]:
                worsened.append((finding, previous))
        if all((not introduced, not worsened)):
            return []

        introduced_counts = dict.fromkeys(severities, 0)
        worsened_counts = dict.fromkeys(severities, 0)
        for finding in introduced:
            introduced_counts[finding.severity] += 1
        for finding, _previous in worsened:
            worsened_counts[finding.severity] += 1
        detail_parts = []
        for severity in severities:
            count = introduced_counts[severity]
            upgraded = worsened_counts[severity]
            if any((count, upgraded)):
                detail_parts.append(
                    f"{severity.upper()} 新增 {count}、升级至该档 {upgraded}"
                )
        new_findings.append(
            Finding(
                code="QG179",
                severity="critical",
                confidence="high",
                path=".",
                line=1,
                column=1,
                symbol="repository.quality_fingerprint_regression",
                message=(
                    f"相对 Git 基线 `{baseline.revision}` 检测到不可抵消的新增或升级问题："
                    + "；".join(detail_parts)
                    + "。历史债务减少不抵消本轮回归。"
                ),
                suggestion=(
                    "本门禁不可由修改说明或历史债务削减豁免。必须删除、修复或回退每一项"
                    " INTRODUCED/WORSENED 问题后重新扫描。"
                ),
                evidence={
                    "comparison_mode": report.comparison_mode,
                    "comparison_target": report.comparison_target,
                    "baseline_counts": {
                        severity: baseline.summary[severity] for severity in severities
                    },
                    "current_counts": current_counts,
                    "raw_count_delta": dict(report.quality_count_deltas),
                    "introduced": [
                        {
                            "fingerprint": finding.fingerprint,
                            "code": finding.code,
                            "severity": finding.severity,
                            "path": finding.path,
                            "line": finding.line,
                            "symbol": finding.symbol,
                        }
                        for finding in introduced
                    ],
                    "worsened": [
                        {
                            "fingerprint": finding.fingerprint,
                            "code": finding.code,
                            "baseline_severity": previous,
                            "current_severity": finding.severity,
                            "path": finding.path,
                            "line": finding.line,
                            "symbol": finding.symbol,
                        }
                        for finding, previous in worsened
                    ],
                },
            )
        )
        return new_findings
