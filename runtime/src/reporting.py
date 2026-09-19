from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable

from .config import is_test_path
from .model import (
    Finding,
    InterfaceChange,
    InterfaceDiffReport,
    InterfaceParameter,
    InterfaceSymbol,
    ScanReport,
)

SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}
_MAX_DECLARATION_LENGTH = 260
_EMPTY_QUALITY_COUNTS = {"critical": 0, "error": 0, "warning": 0}


def _production_interface_diff(
    interface_diff: InterfaceDiffReport | None,
    project_name: str,
) -> InterfaceDiffReport | None:
    """过滤测试路径，只保留生产接口和生产契约发现。

    Args:
        interface_diff: 完整 Git 接口差异报告。
        project_name: 当前启用的项目 profile。

    Returns:
        仅含生产接口的差异报告；输入为空时返回 None。
    """
    if interface_diff is None:
        return None
    return InterfaceDiffReport(
        base=interface_diff.base,
        target=interface_diff.target,
        changes=tuple(
            change
            for change in interface_diff.changes
            if not is_test_path(change.path, project_name)
        ),
        base_files=interface_diff.base_files,
        target_files=interface_diff.target_files,
        errors=interface_diff.errors,
        contract_findings=tuple(
            finding
            for finding in interface_diff.contract_findings
            if not is_test_path(finding.path, project_name)
        ),
        api_scope=interface_diff.api_scope,
        private_min_lines=interface_diff.private_min_lines,
    )


def _production_added_kind_counts(
    interface_diff: InterfaceDiffReport | None,
    project_name: str,
) -> tuple[int, int, int]:
    """统计生产代码新增函数、变量和类数量。

    Args:
        interface_diff: 完整 Git 接口差异报告。
        project_name: 当前启用的项目 profile。

    Returns:
        依次返回函数（含方法）、变量（含字段）和类数量。
    """
    if interface_diff is None:
        return 0, 0, 0
    added = [
        change
        for change in interface_diff.changes
        if change.change == "added" and not is_test_path(change.path, project_name)
    ]
    functions = sum(change.kind in {"function", "method"} for change in added)
    variables = sum(change.kind in {"global_variable", "member_variable"} for change in added)
    classes = sum(change.kind == "class" for change in added)
    return functions, variables, classes


def _test_interface_change_count(
    interface_diff: InterfaceDiffReport | None,
    project_name: str,
) -> int:
    """统计测试路径中的接口变化数量。

    Args:
        interface_diff: 完整 Git 接口差异报告。
        project_name: 当前启用的项目 profile。

    Returns:
        测试路径接口变化总数。
    """
    if interface_diff is None:
        return 0
    return sum(is_test_path(change.path, project_name) for change in interface_diff.changes)


def _append_test_audit_summary(lines: list[str], report: ScanReport) -> None:
    """追加独立的测试/Tracing 非阻断审计摘要。

    该域完整披露，但不参与生产零回归和接口硬门槛。

    Args:
        lines: 正在构建的报告文本行。
        report: 包含测试扫描结果的完整报告。

    Returns:
        None。
    """
    interface_changes = _test_interface_change_count(report.interface_diff, report.project_name)
    if not report.test_files_scanned and not report.test_findings and not interface_changes:
        return
    counts = _finding_counts(report.test_findings)
    lines.extend(
        [
            "",
            "## 测试/Tracing 非阻断审计",
            "",
            f"- 非阻断文件：{report.test_files_scanned}",
            f"- 非阻断定义：{report.test_definitions}",
            f"- 测试接口变化：{interface_changes}（不计入生产接口明细）",
            f"- Critical / Error / Warning / Info：{counts['critical']} / "
            f"{counts['error']} / {counts['warning']} / {counts['info']}",
            "- 测试会单独检查删除用例、减少断言、增加 skip/xfail、私有边界访问和无有效断言的新测试；不会作为生产 ADD/ARCH/DELTA 展开。",
            "",
        ]
    )
    key_findings = [
        finding
        for finding in report.test_findings
        if finding.severity in {"critical", "error", "warning"}
    ]
    if not key_findings:
        lines.extend(["未发现需要人工处理的测试风险。", ""])
        return
    lines.extend(
        [
            "| 规则 | 级别 | 位置 | 测试风险 |",
            "|---|---|---|---|",
        ]
    )
    for finding in key_findings[:40]:
        lines.append(
            f"| `{finding.code}` | `{finding.severity.upper()}` | "
            f"`{finding.path}:{finding.line}` | {finding.message} |"
        )
    remaining = len(key_findings) - 40
    if remaining > 0:
        lines.append(f"| … | … | … | 其余 {remaining} 项保留在 JSON 明细中 |")
    lines.append("")


def _finding_counts(findings: Iterable[Finding]) -> dict[str, int]:
    """按严重级别统计发现数量。

    Args:
        findings: 待汇总的静态发现。

    Returns:
        Critical、Error、Warning 和 Info 计数字典。
    """
    counts = {"critical": 0, "error": 0, "warning": 0, "info": 0}
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def _quality_delta_rows(report: ScanReport) -> list[tuple[str, int, int, int]]:
    """生成基线与最终工作区的分级质量变化。

    Args:
        report: 包含可选 Git 质量基线的扫描报告。

    Returns:
        严重级别、基线数、最终数和净变化组成的行列表。
    """
    if report.baseline is None:
        raise ValueError("质量变化需要可用 Git 基线")
    final_counts = _finding_counts(report.findings)
    baseline_counts = report.baseline.summary
    return [
        (
            severity,
            baseline_counts[severity],
            final_counts[severity],
            final_counts[severity] - baseline_counts[severity],
        )
        for severity in ("critical", "error", "warning")
    ]


def _quality_dimensions(
    findings: Iterable[Finding],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """按规则和文件汇总关键严重级别。

    Args:
        findings: 待汇总的静态发现。

    Returns:
        规则计数和文件计数两个映射。
    """
    by_rule: defaultdict[str, dict[str, int]] = defaultdict(lambda: dict(_EMPTY_QUALITY_COUNTS))
    by_file: defaultdict[str, dict[str, int]] = defaultdict(lambda: dict(_EMPTY_QUALITY_COUNTS))
    for finding in findings:
        if finding.severity == "info":
            continue
        by_rule[finding.code][finding.severity] += 1
        by_file[finding.path][finding.severity] += 1
    return dict(by_rule), dict(by_file)


def _increased_dimension_rows(
    baseline: dict[str, dict[str, int]],
    final: dict[str, dict[str, int]],
) -> list[tuple[int, str, str, int, int]]:
    """找出指定维度中净增加的关键问题。

    Args:
        baseline: Git 基线按维度汇总的计数。
        final: 最终工作区按同一维度汇总的计数。

    Returns:
        排序后的严重级别、键、级别、基线和最终计数。
    """
    rows: list[tuple[int, str, str, int, int]] = []
    for key in sorted(set(baseline) | set(final)):
        before_counts = baseline[key] if key in baseline else _EMPTY_QUALITY_COUNTS
        after_counts = final[key] if key in final else _EMPTY_QUALITY_COUNTS
        for severity in ("critical", "error", "warning"):
            before = before_counts[severity]
            after = after_counts[severity]
            if after > before:
                rows.append((SEVERITY_RANK[severity], key, severity, before, after))
    rows.sort(key=lambda row: (-row[0], -(row[4] - row[3]), row[1]))
    return rows


def _positive_quality_deltas(report: ScanReport, limit: int = 40) -> list[str]:
    """渲染净新增 Critical/Error/Warning 的规则和文件归因。

    Args:
        report: 包含基线摘要的扫描报告。
        limit: 每个维度最多输出的净新增项数量。

    Returns:
        可直接插入 Markdown 的归因明细。
    """
    if report.baseline is None:
        return [
            "## 自动 Git 质量基线不可用",
            "",
            f"- {report.baseline_error or '未生成 Git 静态质量基线。'}",
            "- 不接受模型手填、旧报告估算或口头说明替代静态基线；必须修复 Git 路径、权限或 safe.directory 后重跑。",
            "- 在自动基线恢复前，质量门禁保持 REJECT。",
            "",
        ]
    final_by_rule, final_by_file = _quality_dimensions(report.findings)
    rule_rows = _increased_dimension_rows(report.baseline.by_rule, final_by_rule)
    file_rows = _increased_dimension_rows(report.baseline.by_file, final_by_file)
    if not rule_rows and not file_rows:
        return [
            "## 净新增质量问题归因",
            "",
            "Critical/Error/Warning 均未出现净新增规则或文件热点。",
            "",
        ]
    lines = [
        "## 净新增质量问题归因",
        "",
        "任一严重级别增加都不能由其他级别下降抵扣。以下净新增项必须优先修复；仍保留时，最终报告必须逐项说明代码位置、必要性、尝试过的修改和本轮不能安全消除的原因。",
        "",
        "### 按规则",
        "",
        "| 规则 | 级别 | 基线 | 最终 | 净增 |",
        "|---|---|---:|---:|---:|",
    ]
    for _, code, severity, before, after in rule_rows[:limit]:
        lines.append(f"| {code} | {severity.upper()} | {before} | {after} | +{after - before} |")
    if len(rule_rows) > limit:
        lines.append(f"| … | … | … | … | 其余 {len(rule_rows) - limit} 项 |")
    lines.extend(
        [
            "",
            "### 按文件",
            "",
            "| 文件 | 级别 | 基线 | 最终 | 净增 |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for _, path, severity, before, after in file_rows[:limit]:
        lines.append(f"| `{path}` | {severity.upper()} | {before} | {after} | +{after - before} |")
    if len(file_rows) > limit:
        lines.append(f"| … | … | … | … | 其余 {len(file_rows) - limit} 项 |")
    lines.append("")
    return lines


def merge_findings(report: ScanReport, extra: Iterable[Finding]) -> ScanReport:
    """
    把外部检查结果合并到扫描报告并重新排序。

    Args:
        report: 待处理的扫描报告。
        extra: 需要合并的附加发现。

    Returns:
        原地更新后的扫描报告。
    """
    report.findings.extend(extra)
    report.findings.sort(
        key=lambda item: (-SEVERITY_RANK[item.severity], item.path, item.line, item.code)
    )
    return report


def _interface_summary_line(interface_diff: InterfaceDiffReport) -> str:
    """
    渲染接口差异的一行摘要。

    Args:
        interface_diff: Git 基线与当前工作区的接口差异报告。

    Returns:
        包含新增、删除、修改和破坏性数量的摘要文本。
    """
    summary = interface_diff.summary()
    by_change = summary["by_change"]
    return (
        f"接口差异 {interface_diff.base} -> {interface_diff.target} "
        f"[{interface_diff.api_scope}]：新增={by_change['added']} 删除={by_change['removed']} "
        f"修改={by_change['modified']} 可能破坏={summary['breaking']} "
        f"契约问题={len(interface_diff.contract_findings)}"
    )


def _format_parameter(parameter: InterfaceParameter) -> str:
    """
    把结构化参数转换成人工可读的 Python 签名片段。

    Args:
        parameter: 待渲染的接口参数。

    Returns:
        包含名称、类型注解和默认值的参数声明。
    """
    if parameter.kind == "var_positional":
        text = f"*{parameter.name}"
    elif parameter.kind == "var_keyword":
        text = f"**{parameter.name}"
    else:
        text = parameter.name
    if parameter.annotation:
        text += f": {parameter.annotation}"
    if parameter.has_default:
        default = parameter.default or "None"
        text += f" = {default}"
    return text


def _format_parameters(parameters: tuple[InterfaceParameter, ...]) -> str:
    """
    把完整参数列表转换成人工可读的 Python 参数串。

    Args:
        parameters: 按源码顺序保存的接口参数列表。

    Returns:
        包含位置参数、关键字参数和变长参数标记的参数串。
    """
    parts: list[str] = []
    has_var_positional = any(item.kind == "var_positional" for item in parameters)
    keyword_marker_added = False
    positional_only_count = sum(item.kind == "positional_only" for item in parameters)
    for index, parameter in enumerate(parameters):
        if parameter.kind == "keyword_only" and not has_var_positional and not keyword_marker_added:
            parts.append("*")
            keyword_marker_added = True
        parts.append(_format_parameter(parameter))
        if positional_only_count and index + 1 == positional_only_count:
            parts.append("/")
    return ", ".join(parts)


def _shorten(text: str, limit: int = _MAX_DECLARATION_LENGTH) -> str:
    """
    限制单行接口声明长度，避免长表达式淹没审查报告。

    Args:
        text: 原始声明文本。
        limit: 允许保留的最大字符数。

    Returns:
        不超过指定长度的单行文本。
    """
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _symbol_declaration(symbol: InterfaceSymbol | None) -> str:
    """
    把接口符号转换成人工可审阅的一行声明。

    Args:
        symbol: Git 基线或当前工作区中的接口符号；为空表示该侧不存在。

    Returns:
        接近 Python 源码风格的接口声明。
    """
    if symbol is None:
        return "<不存在>"
    decorators = f" decorators={list(symbol.decorators)!r}" if symbol.decorators else ""
    if symbol.kind == "file":
        return f"file {symbol.path}"
    if symbol.kind == "class":
        bases = f"({', '.join(symbol.bases)})" if symbol.bases else ""
        return _shorten(f"class {symbol.qualname}{bases}{decorators}")
    if symbol.kind in {"function", "method"}:
        prefix = "async def" if symbol.is_async else "def"
        returns = f" -> {symbol.return_type}" if symbol.return_type else ""
        return _shorten(
            f"{prefix} {symbol.qualname}({_format_parameters(symbol.parameters)}){returns}{decorators}"
        )
    assignment = symbol.qualname
    if symbol.annotation:
        assignment += f": {symbol.annotation}"
    if symbol.value:
        assignment += f" = {symbol.value}"
    storage = f"  # {symbol.storage}" if symbol.storage else ""
    return _shorten(f"{symbol.kind} {assignment}{storage}")


def _parameter_names(parameters: list[dict[str, object]]) -> list[str]:
    """
    从参数差异字典中提取参数名。

    Args:
        parameters: 参数差异中的 added 或 removed 列表。

    Returns:
        参数名称列表。
    """
    return [str(item["name"]) for item in parameters]


def _change_notes(change: InterfaceChange) -> list[str]:
    """
    把结构化接口差异压缩成人工分诊摘要。

    Args:
        change: 单条接口变化。

    Returns:
        可直接展示的变化摘要列表。
    """
    if change.change == "added":
        return ["新增接口"]
    if change.change == "removed":
        return ["删除接口，可能破坏已有调用方"]
    notes: list[str] = []
    parameters = change.details.get("parameters")
    if isinstance(parameters, dict):
        added = _parameter_names(parameters.get("added", []))
        removed = _parameter_names(parameters.get("removed", []))
        modified = [str(item["name"]) for item in parameters.get("modified", [])]
        if added:
            notes.append("新增参数: " + ", ".join(added))
        if removed:
            notes.append("删除参数: " + ", ".join(removed))
        if modified:
            notes.append("修改参数: " + ", ".join(modified))
        if parameters.get("order_changed"):
            notes.append("参数顺序变化")
    for field_name, label in (
        ("return_type", "返回类型"),
        ("annotation", "变量注解"),
        ("value", "变量值"),
        ("storage", "变量存储位置"),
        ("decorators", "装饰器"),
        ("bases", "基类"),
        ("is_async", "同步/异步"),
        ("exports", "__all__ 导出"),
    ):
        field = change.details.get(field_name)
        if isinstance(field, dict):
            notes.append(f"{label}: {field.get('before')!r} -> {field.get('after')!r}")
    return notes or ["接口声明变化"]


def _change_line_number(change: InterfaceChange) -> int:
    """
    选择接口变化的当前侧或旧侧行号。

    Args:
        change: 单条接口变化。

    Returns:
        可用于报告定位的源码行号。
    """
    symbol = change.after or change.before
    return symbol.line if symbol is not None else 1


def _append_interface_review_markdown(
    lines: list[str],
    interface_diff: InterfaceDiffReport | None,
) -> None:
    """
    向 Markdown 报告追加可审阅的 before/after 接口对比。

    Args:
        lines: 正在构建的 Markdown 文本行。
        interface_diff: 可选接口差异报告。

    Returns:
        None。
    """
    if interface_diff is None:
        return
    summary = interface_diff.summary()
    by_change = summary["by_change"]
    lines.extend(
        [
            "",
            "## Git 工作区接口差异",
            "",
            f"- 比较范围：`{interface_diff.base}` → `{interface_diff.target}`",
            f"- 接口范围：`{interface_diff.api_scope}`",
            f"- 新增：{by_change['added']}",
            f"- 删除：{by_change['removed']}",
            f"- 修改：{by_change['modified']}",
            f"- 可能破坏已有调用方：{summary['breaking']}",
            "",
        ]
    )
    if interface_diff.errors:
        lines.extend(["### 解析错误", ""])
        lines.extend(f"- {error}" for error in interface_diff.errors)
        lines.append("")
    if interface_diff.contract_findings:
        lines.extend(["### 项目接口契约", ""])
        for finding in interface_diff.contract_findings:
            lines.append(
                f"- **{finding.code} · {finding.severity.upper()}** "
                f"`{finding.path}:{finding.line}` `{finding.symbol}`：{finding.message}"
            )
            if finding.evidence:
                lines.extend(
                    [
                        "",
                        "```json",
                        json.dumps(finding.evidence, ensure_ascii=False, indent=2, sort_keys=True),
                        "```",
                        "",
                    ]
                )
        lines.append("")
    if not interface_diff.changes:
        lines.extend(["### 接口变化明细", "", "未发现接口变化。", ""])
        return
    lines.extend(["### 接口变化明细", ""])
    for index, change in enumerate(interface_diff.changes, start=1):
        line = _change_line_number(change)
        breaking = "可能破坏" if change.breaking else "非破坏新增"
        lines.extend(
            [
                f"#### {index}. API-{change.change.upper()} · {change.kind} · `{change.path}:{line}`",
                "",
                f"- 符号：`{change.symbol}`",
                f"- 影响：{breaking}",
                f"- 摘要：{'；'.join(_change_notes(change))}",
                "",
                "```diff",
                f"- before: {_symbol_declaration(change.before)}",
                f"+ after : {_symbol_declaration(change.after)}",
                "```",
                "",
            ]
        )


def _append_interface_review_text(
    lines: list[str], interface_diff: InterfaceDiffReport | None
) -> None:
    """
    向纯文本报告追加可审阅的 before/after 接口对比。

    Args:
        lines: 正在构建的文本行。
        interface_diff: 可选接口差异报告。

    Returns:
        None。
    """
    if interface_diff is None:
        return
    lines.extend(["", _interface_summary_line(interface_diff)])
    for error in interface_diff.errors:
        lines.append(f"[API-ERROR] {error}")
    for finding in interface_diff.contract_findings:
        lines.append(
            f"{finding.path}:{finding.line} "
            f"[API-CONTRACT][{finding.severity.upper()}][{finding.code}] "
            f"{finding.message}"
        )
    for change in interface_diff.changes:
        line = _change_line_number(change)
        lines.append(
            f"{change.path}:{line} [API-{change.change.upper()}][{change.kind}] {change.symbol}"
        )
        lines.append(f"  摘要: {'；'.join(_change_notes(change))}")
        lines.append(f"  before: {_symbol_declaration(change.before)}")
        lines.append(f"  after : {_symbol_declaration(change.after)}")


def render_summary(report: ScanReport) -> str:
    """
    渲染适合首次人工分诊的规则与文件聚合摘要。

    Args:
        report: 待处理的扫描报告。

    Returns:
        不展开逐条发现的终端摘要。
    """
    counts = _finding_counts(report.findings)
    lines = [
        (
            f"扫描 {report.files_scanned} 个 Python 文件、{report.definitions} 个定义；"
            f"docstring={report.docstring_coverage:.2f}% "
            f"完整={report.complete_docstring_coverage:.2f}%；"
            f"critical={counts['critical']} error={counts['error']} warning={counts['warning']} info={counts['info']}"
        ),
        "",
        "按规则统计：",
    ]
    for row in report.rule_summary():
        severity = row["severity"]
        lines.append(
            f"  {row['code']}: {row['count']} "
            f"(error={severity['error']} warning={severity['warning']} info={severity['info']})"
        )
    lines.extend(["", "文件热点："])
    for row in report.file_hotspots()[:30]:
        lines.append(
            f"  {row['path']}: {row['count']} "
            f"(error={row['error']} warning={row['warning']} info={row['info']})"
        )
    if report.interface_diff is not None:
        production_diff = _production_interface_diff(report.interface_diff, report.project_name)
        if production_diff is not None:
            lines.extend(["", _interface_summary_line(production_diff)])
        functions, variables, classes = _production_added_kind_counts(
            report.interface_diff, report.project_name
        )
        lines.extend(
            [
                f"生产新增函数：{functions}",
                f"生产新增变量：{variables}",
                f"生产新增类：{classes}",
            ]
        )
        test_changes = _test_interface_change_count(report.interface_diff, report.project_name)
        if test_changes:
            lines.append(f"测试接口变化：{test_changes}（独立审计，不计入生产接口）")
    return "\n".join(lines) + "\n"


def render_text(report: ScanReport) -> str:
    """
    把扫描报告渲染为纯文本明细。

    Args:
        report: 待处理的扫描报告。

    Returns:
        包含逐条发现和接口 before/after 对比的文本。
    """
    counts = {"info": 0, "warning": 0, "error": 0, "critical": 0}
    for finding in report.findings:
        counts[finding.severity] += 1
    lines = [
        (
            f"扫描 {report.files_scanned} 个 Python 文件、{report.definitions} 个定义；"
            f"docstring={report.docstring_coverage:.2f}% "
            f"完整={report.complete_docstring_coverage:.2f}%；"
            f"critical={counts['critical']} error={counts['error']} warning={counts['warning']} info={counts['info']}"
        )
    ]
    for finding in report.findings:
        lines.append(
            f"{finding.path}:{finding.line}:{finding.column} "
            f"[{finding.severity.upper()}][{finding.code}][{finding.confidence}] "
            f"{finding.message}"
        )
        if finding.suggestion:
            lines.append(f"  建议: {finding.suggestion}")
        if finding.evidence:
            evidence = json.dumps(finding.evidence, ensure_ascii=False, sort_keys=True)
            lines.append(f"  证据: {evidence}")
    _append_interface_review_text(
        lines, _production_interface_diff(report.interface_diff, report.project_name)
    )
    return "\n".join(lines) + "\n"


def render_markdown(report: ScanReport) -> str:
    """
    把扫描报告渲染为 Markdown 明细文档。

    Args:
        report: 待处理的扫描报告。

    Returns:
        Markdown 格式的完整发现和接口对比。
    """
    counts = {"info": 0, "warning": 0, "error": 0, "critical": 0}
    for finding in report.findings:
        counts[finding.severity] += 1
    lines = [
        "# Repository Quality Guard 报告",
        "",
        f"- 扫描文件：{report.files_scanned}",
        f"- 代码定义：{report.definitions}",
        f"- Docstring 覆盖率：{report.docstring_coverage:.2f}%",
        f"- 完整 Docstring 覆盖率：{report.complete_docstring_coverage:.2f}%",
        f"- Critical：{counts['critical']}",
        f"- Error：{counts['error']}",
        f"- Warning：{counts['warning']}",
        f"- Info：{counts['info']}",
        f"- 生产新增函数：{_production_added_kind_counts(report.interface_diff, report.project_name)[0]}",
        f"- 生产新增变量：{_production_added_kind_counts(report.interface_diff, report.project_name)[1]}",
        f"- 生产新增类：{_production_added_kind_counts(report.interface_diff, report.project_name)[2]}",
        "",
        *(
            ["## 审计范围说明", "", *(f"- {note}" for note in report.scope_notes), ""]
            if report.scope_notes
            else []
        ),
        *(
            ["## 输出聚焦路径", "", *(f"- `{path}`" for path in report.focus_paths), ""]
            if report.focus_paths
            else []
        ),
        "## 规则统计",
        "",
        "| 规则 | 总数 | Critical | Error | Warning | Info |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report.rule_summary():
        severity = row["severity"]
        lines.append(
            f"| {row['code']} | {row['count']} | {severity['critical']} | "
            f"{severity['error']} | {severity['warning']} | {severity['info']} |"
        )
    lines.extend(["", "## 逐条发现", ""])
    for finding in report.findings:
        lines.extend(
            [
                f"### {finding.code} · {finding.severity.upper()} · {finding.confidence}",
                "",
                f"`{finding.path}:{finding.line}:{finding.column}`",
                "",
                finding.message,
                "",
            ]
        )
        if finding.suggestion:
            lines.extend([f"**建议：** {finding.suggestion}", ""])
        if finding.evidence:
            lines.extend(
                [
                    "```json",
                    json.dumps(finding.evidence, ensure_ascii=False, indent=2, sort_keys=True),
                    "```",
                    "",
                ]
            )
    _append_test_audit_summary(lines, report)
    _append_interface_review_markdown(
        lines, _production_interface_diff(report.interface_diff, report.project_name)
    )
    return "\n".join(lines)


def render_audit_markdown(report: ScanReport) -> str:
    """
    渲染聚合审计 Markdown，避免逐条发现淹没人工判断。

    Args:
        report: 待处理的扫描报告。

    Returns:
        分层汇总、热点、关键错误和接口变化组成的 Markdown 文档。
    """
    if report.files_scanned == 0 and not report.findings and report.interface_diff is not None:
        lines = ["# Repository Quality Guard 接口审查"]
        _append_test_audit_summary(lines, report)
        _append_interface_review_markdown(
            lines, _production_interface_diff(report.interface_diff, report.project_name)
        )
        return "\n".join(lines) + "\n"
    counts = {"info": 0, "warning": 0, "error": 0, "critical": 0}
    for finding in report.findings:
        counts[finding.severity] += 1
    lines = [
        "# Repository Quality Guard 审计汇总",
        "",
        "## 总览",
        "",
        f"- 扫描文件：{report.files_scanned}",
        f"- 代码定义：{report.definitions}",
        f"- Docstring 覆盖率：{report.docstring_coverage:.2f}%",
        f"- 完整 Docstring 覆盖率：{report.complete_docstring_coverage:.2f}%",
        f"- Critical：{counts['critical']}",
        f"- Error：{counts['error']}",
        f"- Warning：{counts['warning']}",
        f"- Info：{counts['info']}",
        f"- 生产新增函数：{_production_added_kind_counts(report.interface_diff, report.project_name)[0]}",
        f"- 生产新增变量：{_production_added_kind_counts(report.interface_diff, report.project_name)[1]}",
        f"- 生产新增类：{_production_added_kind_counts(report.interface_diff, report.project_name)[2]}",
        "",
        *(
            ["## 审计范围说明", "", *(f"- {note}" for note in report.scope_notes), ""]
            if report.scope_notes
            else []
        ),
        *(
            ["## 输出聚焦路径", "", *(f"- `{path}`" for path in report.focus_paths), ""]
            if report.focus_paths
            else []
        ),
        *(
            [
                "## Git 静态质量基线对比",
                "",
                f"- 比较模式：{report.comparison_mode or '未记录'}",
                f"- 基线来源：Git `{report.baseline.revision}`。",
                f"- 比较目标：`{report.comparison_target}`。",
                "- 普通 Critical、Error、Warning 任一问题指纹新增或升级时，QG179 直接拒绝；历史问题减少不得抵消。QG180/QG181 进入 REVIEW_REQUIRED 人工接口账本。",
                "",
                "| 级别 | 基线 | 最终工作区 | 净变化 |",
                "|---|---:|---:|---:|",
                *(
                    f"| {severity.upper()} | {before} | {after} | {delta:+d} |"
                    for severity, before, after, delta in _quality_delta_rows(report)
                ),
                "",
            ]
            if report.baseline is not None
            else [
                "## Git 静态质量基线对比",
                "",
                f"- 自动基线不可用：{report.baseline_error or '未生成基线。'}",
                "- 不允许人工补填替代；修复 Git 环境后重跑。",
                "",
            ]
        ),
        "## 规则聚合",
        "",
        "| 规则 | 总数 | Critical | Error | Warning | Info |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report.rule_summary()[:80]:
        severity = row["severity"]
        lines.append(
            f"| {row['code']} | {row['count']} | {severity['critical']} | "
            f"{severity['error']} | {severity['warning']} | {severity['info']} |"
        )
    lines.extend(
        [
            "",
            "## 文件热点",
            "",
            "| 文件 | 总数 | Critical | Error | Warning | Info |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report.file_hotspots()[:40]:
        lines.append(
            f"| `{row['path']}` | {row['count']} | {row['critical']} | "
            f"{row['error']} | {row['warning']} | {row['info']} |"
        )
    lines.extend(_positive_quality_deltas(report))
    _append_key_findings(lines, report.findings)
    _append_test_audit_summary(lines, report)
    _append_interface_review_markdown(
        lines, _production_interface_diff(report.interface_diff, report.project_name)
    )
    return "\n".join(lines) + "\n"


def _append_key_findings(lines: list[str], findings: list[Finding]) -> None:
    """追加有限数量的 error 和高置信 warning 示例。

    Args:
        lines: 正在构建的 Markdown 文本行。
        findings: 当前报告内的全部质量发现。

    Returns:
        None。
    """
    key_items = [item for item in findings if item.severity in {"critical", "error"}]
    key_items.extend(
        item for item in findings if item.severity == "warning" and item.confidence == "high"
    )
    if not key_items:
        lines.extend(["", "## 关键发现", "", "未发现 critical、error 或高置信 warning。", ""])
        return
    lines.extend(["", "## 关键发现", ""])
    for finding in key_items[:80]:
        lines.extend(
            [
                f"### {finding.code} · {finding.severity.upper()} · {finding.confidence}",
                "",
                f"`{finding.path}:{finding.line}:{finding.column}` `{finding.symbol}`",
                "",
                finding.message,
                "",
            ]
        )
        if finding.suggestion:
            lines.extend([f"**建议：** {finding.suggestion}", ""])
        if finding.evidence:
            lines.extend(
                [
                    "```json",
                    json.dumps(finding.evidence, ensure_ascii=False, indent=2, sort_keys=True),
                    "```",
                    "",
                ]
            )
    remaining = len(key_items) - 80
    if remaining > 0:
        lines.extend([f"还有 {remaining} 条关键发现已保留在 JSON 明细中。", ""])


def render_json(report: ScanReport) -> str:
    """
    把扫描报告渲染为格式化 JSON。

    Args:
        report: 待处理的扫描报告。

    Returns:
        带缩进和结尾换行的 JSON 字符串。
    """
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"
