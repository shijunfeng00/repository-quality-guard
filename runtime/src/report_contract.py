"""Strict-report validation and stable imports for report facts and rendering."""

from __future__ import annotations

import re

from . import report_schema
from .gate_status import code_status
from .model import Finding, ScanReport

from .report_facts import (
    ChangedFile as ChangedFile,
    AddedInterface as AddedInterface,
    QualityDelta as QualityDelta,
    HistoricalDebt as HistoricalDebt,
    ReportFacts as ReportFacts,
    current_quality_findings,
    current_test_quality_findings,
    build_report_facts as build_report_facts,
    collect_changed_files,
)
from .report_rendering import (
    fresh_template,
    status_can_be_downgraded,
    render_strict_modification_report as render_strict_modification_report,
)


_changed_files = collect_changed_files

# Preserve the public report-contract constants exposed by v0.14.5.
REPORT_SCHEMA = report_schema.REPORT_SCHEMA
REPORT_FILENAME = report_schema.REPORT_FILENAME
REPORT_TITLE = report_schema.REPORT_TITLE
REQUIRED_SECTIONS = report_schema.REQUIRED_SECTIONS
COMMON_QUESTION_TITLES = report_schema.COMMON_QUESTION_TITLES
AGENT_SEMANTIC_HEURISTIC_QUESTION = report_schema.AGENT_SEMANTIC_HEURISTIC_QUESTION
VALID_ITEM_STATUSES = report_schema.VALID_ITEM_STATUSES
VALID_FINAL_STATUSES = report_schema.VALID_FINAL_STATUSES


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
            findings.append(
                _report_finding("QG982", f"{item_id} 的附件路径与自动事实不一致。")
            )
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
            findings.append(
                _report_finding("QG982", f"{item_id} 的文件路径与自动事实不一致。")
            )
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
            findings.append(
                _report_finding("QG982", f"{item_id} 使用了非法状态 `{status}`。")
            )
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
            findings.append(
                _report_finding("QG982", f"{item_id} 的测试文件路径与自动事实不一致。")
            )
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
            (
                match.group("risk"),
                report_schema.REQUIRED_TEST_RISK_MARKERS,
                "风险与结论",
            ),
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
            findings.append(
                _report_finding("QG982", f"{item_id} 使用了非法状态 `{status}`。")
            )
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
            findings.append(
                _report_finding("QG982", f"{match.group('id')} 必须填写 `处置=`。")
            )
        if (
            report_schema.contains_placeholder(verification)
            or "证据=" not in verification
            or len(verification) < report_schema.MIN_MANUAL_TEXT
        ):
            findings.append(
                _report_finding("QG982", f"{match.group('id')} 必须填写 `证据=`。")
            )
        if status not in report_schema.VALID_ITEM_STATUSES:
            findings.append(
                _report_finding(
                    "QG982", f"{match.group('id')} 使用了非法状态 `{status}`。"
                )
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
            findings.append(
                _report_finding("QG982", f"{item_id} 的符号与自动事实不一致。")
            )
        if any(
            (
                report_schema.contains_placeholder(reason),
                len(reason.strip()) < report_schema.MIN_ADDITION_FACT_TEXT,
                any(
                    marker not in reason
                    for marker in report_schema.REQUIRED_ADDITION_REASON_MARKERS
                ),
            )
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须分别填写 `失败=`、`职责=`、`边界=`、`绝对必要性=` 与 `不可替代=`：给出不新增时的可观察失败、唯一职责所有者、相邻层边界，以及为何绝对不能复用或保持原接口。",
                )
            )
        if any(
            (
                report_schema.contains_placeholder(existing_check),
                len(existing_check.strip()) < report_schema.MIN_ADDITION_FACT_TEXT,
                any(
                    marker not in existing_check
                    for marker in report_schema.REQUIRED_EXISTING_MARKERS
                ),
            )
        ):
            findings.append(
                _report_finding(
                    "QG982",
                    f"{item_id} 必须逐项披露 `接口目录=`、`查询=`、`候选=`、`源码=`、`HEAD=`、`父类=`、`MRO=`、`兄弟类=`、`公共能力=`；BM25 只召回候选，必须继续阅读候选源码后才能认定不可复用。",
                )
            )
        if any(
            (
                report_schema.contains_placeholder(alternative),
                len(alternative.strip()) < report_schema.MIN_ADDITION_ALTERNATIVE_TEXT,
                any(
                    marker not in alternative
                    for marker in report_schema.REQUIRED_ALTERNATIVE_MARKERS
                ),
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
            evidence.split("语义裁决=", 1)[-1].strip()
            if "语义裁决=" in evidence
            else ""
        )
        has_chain_shape = any(
            (
                "→" in call_chain,
                "->" in call_chain,
                call_chain
                in {
                    "无调用方",
                    "入口协议直接调用",
                    "模块常量无调用链",
                },
            )
        )
        if any(
            (
                report_schema.contains_placeholder(evidence),
                len(evidence.strip()) < report_schema.MIN_ADDITION_FACT_TEXT,
                any(
                    marker not in evidence
                    for marker in report_schema.REQUIRED_ADDITION_EVIDENCE_MARKERS
                ),
                not has_chain_shape,
                len(verification) < report_schema.MIN_ADDITION_VERIFICATION_TEXT,
                len(semantic_decision) < report_schema.MIN_ADDITION_VERIFICATION_TEXT,
            )
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
        explanations.append(
            (item_id, "|".join((reason, existing_check, alternative, evidence)))
        )
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
            findings.append(
                _report_finding("QG982", f"{item_id} 必须填写 `处置=` 及其架构事实。")
            )
        if (
            report_schema.contains_placeholder(verification)
            or len(verification.strip()) < report_schema.MIN_MANUAL_TEXT
            or "证据=" not in verification
        ):
            findings.append(
                _report_finding(
                    "QG982", f"{item_id} 必须填写 `证据=` 并指向调用链或验证结果。"
                )
            )
        if status not in report_schema.VALID_ITEM_STATUSES:
            findings.append(
                _report_finding("QG982", f"{item_id} 使用了非法状态 `{status}`。")
            )
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
            or any(
                marker not in reason
                for marker in report_schema.REQUIRED_DELTA_REASON_MARKERS
            )
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
            or any(
                marker not in risk
                for marker in report_schema.REQUIRED_DELTA_RISK_MARKERS
            )
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
            findings.append(
                _report_finding("QG982", f"{item_id} 的协议符号与自动事实不一致。")
            )
        if finding is None or match.group("fact") != report_schema.escape(
            finding.message
        ):
            findings.append(
                _report_finding("QG981", f"{item_id} 的自动协议变化事实被改写。")
            )
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
                "|".join(
                    (
                        match.group("reason"),
                        match.group("impact"),
                        match.group("evidence"),
                    )
                ),
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
        item.item_id
        for item in facts.changed_files
        if item.path in facts.deletion_only_paths
    )
    expected_artifacts = {
        item.item_id: item.path for item in facts.artifact_changed_files
    }
    expected_test_files = {item.item_id: item.path for item in facts.test_audits}
    expected_additions = {item.item_id: item.change.symbol for item in facts.additions}
    expected_architecture = {
        f"ARCH-{index:03d}": finding
        for index, finding in enumerate(facts.architecture, 1)
    }
    expected_deltas = {item.item_id: item for item in facts.quality_deltas}
    expected_interfaces = {
        f"INTERFACE-{index:03d}": change
        for index, change in enumerate(facts.parameter_interface_changes, 1)
    }
    expected_protocols = {
        f"PROTOCOL-{index:03d}": finding
        for index, finding in enumerate(facts.protocol_findings, 1)
    }
    test_risk_count = sum(len(item.findings) for item in facts.test_audits)
    expected_test_risks = {
        f"TEST-RISK-{index:03d}" for index in range(1, test_risk_count + 1)
    }
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
            findings.append(
                _report_finding("QG982", f"{item_id} 的存量接口符号与自动事实不一致。")
            )
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
    findings.extend(
        _duplicate_text_findings(interface_explanations, "存量接口修改审查")
    )

    findings.extend(_protocol_row_findings(protocol_matches, expected_protocols))

    if facts.interface_definition_net > 0:
        addition_statuses = {
            match.group("id"): match.group("status") for match in addition_matches
        }
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
    missing_locations = [
        location for location in candidate_locations if location not in fact
    ]
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
        item
        for item in candidates
        if not bool(item.evidence.get("authorized_by_baseline", False))
    ]
    if all((candidates, status == "NOT_APPLICABLE")):
        findings.append(
            _report_finding("QG982", "存在 QG178 候选时，Q10 不得标记 NOT_APPLICABLE。")
        )
    if all((candidates, status == "FIXED")):
        findings.append(
            _report_finding(
                "QG982",
                "当前仍存在 QG178 候选时，Q10 不得标记 FIXED；必须删除对应代码后重新扫描。",
            )
        )
    if all((unauthorized, status != "BLOCKING")):
        locations = ", ".join(f"{item.path}:{item.line}" for item in unauthorized)
        findings.append(
            _report_finding(
                "QG982",
                "以下 QG178 候选未获 Git 基线授权，只能 BLOCKING：" + locations,
            )
        )
    if all(
        (
            candidates,
            not unauthorized,
            status == "JUSTIFIED",
            "授权证据=BASELINE_LEDGER" not in fact,
        )
    ):
        findings.append(
            _report_finding(
                "QG982",
                "全部候选虽已由 Git 基线账本授权，但 Q10 标记 JUSTIFIED 时必须填写 "
                "`授权证据=BASELINE_LEDGER`。",
            )
        )
    if all(
        (
            not candidates,
            status == "NOT_APPLICABLE",
            "候选=NONE" not in fact,
        )
    ):
        findings.append(
            _report_finding(
                "QG982", "Q10 标记 NOT_APPLICABLE 时必须明确填写 `候选=NONE`。"
            )
        )
    return findings


def _question_findings(text: str, report: ScanReport) -> list[Finding]:
    """验证通用审判及 profile 专项审判均有事实和独立结论。"""
    findings: list[Finding] = []
    matches = {
        match.group("title"): match
        for match in report_schema.QUESTION_BLOCK.finditer(text)
    }
    fact_values: list[tuple[str, str]] = []
    conclusion_values: list[tuple[str, str]] = []
    for title in report_schema.question_titles(report):
        match = matches.get(title)
        if match is None:
            findings.append(
                _report_finding("QG980", f"架构与必要性审判缺失或格式错误：{title}")
            )
            continue
        status = match.group("status")
        fact = match.group("fact").strip()
        conclusion = match.group("conclusion").strip()
        if status not in report_schema.VALID_ITEM_STATUSES:
            findings.append(
                _report_finding("QG982", f"{title} 使用了非法状态 `{status}`。")
            )
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
            "quality_check.py" in command or "quality-guard" in command
            for command in commands
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
            findings.append(
                _report_finding("QG983", f"验证章节缺少 `{category}` 类命令。")
            )
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
        required_text = (
            (object_text, object_min),
            (reason, report_schema.MIN_RISK_TEXT),
            (proposal, report_schema.MIN_RISK_TEXT),
        )
        if any(
            report_schema.contains_placeholder(value) or len(value.strip()) < minimum
            for value, minimum in required_text
        ):
            findings.append(
                _report_finding("QG982", "剩余风险表仍包含占位或空泛说明。")
            )
    has_open_quality = bool(
        current_quality_findings(report) or current_test_quality_findings(report)
    )
    has_declared_risk = any(item != "NONE" for item in severities)
    if final_status == "ACCEPT" and has_declared_risk:
        findings.append(
            _report_finding("QG982", "代码结论为 ACCEPT 时不得继续声明三档未解决问题。")
        )
    if (
        final_status in {"REVIEW_REQUIRED", "REJECT"}
        and has_open_quality
        and not has_declared_risk
    ):
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
        re.search(
            r'^git commit -m "[a-z]+(?:\([^)]+\))?!?:\s*.*[\u4e00-\u9fff]', subject
        )
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
    if any(
        (
            text.count(report_schema.REPORT_TITLE) != 1,
            tuple(headings) != report_schema.REQUIRED_SECTIONS,
        )
    ):
        findings.append(
            _report_finding(
                "QG980",
                "修改说明必须且只能包含固定十个二级章节，并保持名称与顺序完全一致。",
            )
        )
    for title in report_schema.question_titles(report):
        if text.count(f"### {title}") != 1:
            findings.append(
                _report_finding("QG980", f"架构与必要性审判缺失或重复：{title}")
            )
    front = report_schema.front_matter(text)
    tool_status = code_status(report)
    final_status = front.get("final_status", "")
    if any(
        (
            front.get("report_schema") != report_schema.REPORT_SCHEMA,
            front.get("change_digest") != facts.digest,
            front.get("tool_status") != tool_status,
        )
    ):
        findings.append(
            _report_finding(
                "QG981",
                "schema、change_digest 或 tool_status 与当前 Git/接口/架构静态事实不一致。",
            )
        )
    if any(
        (
            final_status not in report_schema.VALID_FINAL_STATUSES,
            not status_can_be_downgraded(tool_status, final_status),
        )
    ):
        findings.append(
            _report_finding(
                "QG982",
                "final_status 只能等于或严于 tool_status：允许模型基于语义降级，禁止把静态 REJECT/REVIEW_REQUIRED 抬高。",
            )
        )
    expected_auto = {
        match.group("name"): match.group("body")
        for match in report_schema.AUTO_BLOCK.finditer(fresh_template(report, revision))
    }
    actual_matches = list(report_schema.AUTO_BLOCK.finditer(text))
    actual_auto = {match.group("name"): match.group("body") for match in actual_matches}
    if any((len(actual_matches) != len(actual_auto), actual_auto != expected_auto)):
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
        if any(
            (
                not value,
                report_schema.contains_placeholder(value),
                len(value) < report_schema.MIN_SUMMARY_TEXT,
            )
        ):
            findings.append(
                _report_finding("QG982", f"必填结论缺失或仍是占位：{marker}")
            )
    final_line = next(
        (item for item in text.splitlines() if item.startswith("- 最终人工结论：")),
        "",
    )
    manual_final_status = final_line.split("：", 1)[-1].strip() if final_line else ""
    if any(
        (
            manual_final_status not in report_schema.VALID_FINAL_STATUSES,
            manual_final_status != final_status,
        )
    ):
        findings.append(
            _report_finding(
                "QG982",
                "正文最终人工结论必须与 front matter 的 final_status 一致。",
            )
        )
    item_statuses = [
        *(
            match.group("status")
            for match in report_schema.FILE_MANUAL_ROW.finditer(text)
        ),
        *(
            match.group("status")
            for match in report_schema.ARTIFACT_MANUAL_ROW.finditer(text)
        ),
        *(
            match.group("status")
            for match in report_schema.TEST_FILE_MANUAL_ROW.finditer(text)
        ),
        *(
            match.group("status")
            for match in report_schema.ADDITION_MANUAL_ROW.finditer(text)
        ),
        *(
            match.group("status")
            for match in report_schema.ARCH_MANUAL_ROW.finditer(text)
        ),
        *(
            match.group("status")
            for match in report_schema.QUALITY_DELTA_ROW.finditer(text)
        ),
        *(
            match.group("status")
            for match in report_schema.INTERFACE_REVIEW_ROW.finditer(text)
        ),
        *(
            match.group("status")
            for match in report_schema.PROTOCOL_REVIEW_ROW.finditer(text)
        ),
        *(
            match.group("status")
            for match in report_schema.TEST_RISK_MANUAL_ROW.finditer(text)
        ),
        *(
            match.group("status")
            for match in report_schema.QUESTION_BLOCK.finditer(text)
        ),
    ]
    has_blocking = "BLOCKING" in item_statuses
    if all((has_blocking, final_status != "REJECT")):
        findings.append(
            _report_finding(
                "QG982",
                "存在 BLOCKING 项时 final_status 和最终人工结论必须为 REJECT。",
            )
        )
    if all((final_status == "REJECT", not has_blocking)):
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
