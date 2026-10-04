"""Deterministic strict-report rendering and preservation of authored decisions."""

from __future__ import annotations

import re
from collections.abc import Iterable

from . import report_schema
from .config import is_test_path
from .gate_status import code_status
from .model import Finding, InterfaceChange, ScanReport

from .report_facts import ReportFacts, build_report_facts


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


def _metadata_lines(
    report: ScanReport, facts: ReportFacts, counts: dict[str, int]
) -> list[str]:
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
    lines.extend(
        ["", "### 测试文件独立审计", "", *_auto("test_inventory", test_rows), ""]
    )
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
    lines = [
        report_schema.REQUIRED_SECTIONS[3],
        "",
        *_auto("architecture", automatic),
        "",
    ]
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
        change
        for change in changes
        if not is_test_path(change.path, report.project_name)
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
        "REVIEW_REQUIRED（QG181，需人工确认）"
        if facts.interface_definition_net > 0
        else "PASS"
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
            f"{item.change}:{item.symbol}"
            for item in items[: report_schema.TEST_INTERFACE_PREVIEW]
        )
        if len(items) > report_schema.TEST_INTERFACE_PREVIEW:
            representative += (
                f", 其余 {len(items) - report_schema.TEST_INTERFACE_PREVIEW} 项"
            )
        test_rows.append(
            f"| `{report_schema.escape(path)}` | {counts['added']} | {counts['removed']} | "
            f"{counts['modified']} | {report_schema.escape(representative)} |"
        )
    if not grouped:
        test_rows.append("| — | 0 | 0 | 0 | 无测试接口变化 |")

    deletion_only = bool(production_changes) and all(
        change.change == "removed"
        and change.kind in report_schema.INTERFACE_DEFINITION_KINDS
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
        before = (
            test_baseline_counts[severity] if report.test_baseline is not None else 0
        )
        after = test_counts[severity]
        delta = after - before
        baseline_text = str(before) if report.test_baseline is not None else "N/A"
        delta_text = f"{delta:+d}" if report.test_baseline is not None else "N/A"
        test_budget.append(
            f"| {severity.upper()} | {baseline_text} | {after} | {delta_text} |"
        )
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
        test_delta_rows.append(
            "| — | — | — | — | 未发现测试/Tracing 非阻断域新增或升级质量问题 |"
        )
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
        risk_rows.append(
            "| — | — | — | — | 未发现删除用例、减少断言或增加 skip 等测试弱化风险 |"
        )
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
        f"| {item_id} | 处置=待填写 | 证据=待填写 | PENDING |"
        for item_id, _finding in test_risks
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
        severity: baseline_counts[severity]
        + report.quality_count_deltas.get(severity, 0)
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
    rows.extend(
        ["", "| 规则 | 级别 | 数量 | 代表位置 | 代表问题 |", "|---|---|---:|---|---|"]
    )
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for finding in report.findings:
        if (
            finding.severity not in report_schema.SEVERITY_RANK
            or finding.code.startswith("QG98")
        ):
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
        lines.append(
            "| — | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE |"
        )
    lines.extend(
        [
            "",
            f"- 自动存量债务事实：基线生产三档共 {facts.legacy_debt_total} 项；本轮已删除或降级 {facts.legacy_debt_reduced} 项。",
            "- Legacy 十节报告仅保留自动统计；Agent-facing progressive/cleanup 责任账本由紧凑报告的 DEBT-* 契约负责。",
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


def fresh_template(report: ScanReport, revision: str) -> str:
    """Render the initial strict report from a single immutable scan.

    Args:
        report: Production, test and interface facts used by all report sections.
        revision: Explicit Git baseline used for change and debt inventories.

    Returns:
        The complete ten-section Markdown template with machine fact blocks.
    """
    facts = build_report_facts(report, revision)
    counts = {"critical": 0, "error": 0, "warning": 0}
    for finding in report.findings:
        if not finding.code.startswith("QG98") and finding.severity in counts:
            counts[finding.severity] += 1
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


def status_can_be_downgraded(tool_status: str, final_status: str) -> bool:
    """Check that a manual verdict does not weaken the static verdict.

    Args:
        tool_status: Static verdict produced from scan facts.
        final_status: Manual verdict read from the report front matter.

    Returns:
        True for known verdicts whose manual rank is equal or stricter.
    """
    return (
        tool_status in report_schema.STATUS_RANK
        and final_status in report_schema.STATUS_RANK
        and report_schema.STATUS_RANK[final_status]
        <= report_schema.STATUS_RANK[tool_status]
    )


def replace_front_matter(text: str, facts: ReportFacts, tool_status: str) -> str:
    """Refresh report identity while preserving a valid stricter verdict.

    Args:
        text: Existing strict-report Markdown.
        facts: Fresh baseline-bound report facts.
        tool_status: Current static verdict.

    Returns:
        Markdown with current schema, fact digest and static verdict.
    """
    current = report_schema.front_matter(text)
    final_status = current.get("final_status", "PENDING")
    if not status_can_be_downgraded(tool_status, final_status):
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


def refresh_auto_blocks(existing: str, fresh: str) -> str:
    """Replace machine fact blocks while retaining authored report sections.

    Args:
        existing: Previously authored Markdown with complete automatic blocks.
        fresh: Current automatically rendered template.

    Returns:
        Refreshed Markdown, or the fresh template when a required block is absent.
    """
    fresh_blocks = {
        match.group("name"): match.group(0)
        for match in report_schema.AUTO_BLOCK.finditer(fresh)
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
    fresh = fresh_template(report, revision)
    if (
        not existing
        or report_schema.front_matter(existing).get("report_schema")
        != report_schema.REPORT_SCHEMA
    ):
        return fresh
    facts = build_report_facts(report, revision)
    refreshed = replace_front_matter(existing, facts, code_status(report))
    return refresh_auto_blocks(refreshed, fresh)
