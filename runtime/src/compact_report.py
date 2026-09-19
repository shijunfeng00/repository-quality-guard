"""紧凑审计报告：机器填事实、关系影响，模型填写语义、测试契约、减法与整包提交说明。"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .gate_status import absolute_blocker_codes, code_status
from .model import Finding, ScanReport
from .report_contract import ReportFacts, build_report_facts
from .report_schema import escape, front_matter
from .semantic_review import semantic_review_candidates, semantic_review_key

REPORT_SCHEMA = "repository-quality-guard/audit-v17.0"
REPORT_FILENAME = "修改说明.md"
REPORT_TITLE = "# Repository Quality Guard 审计报告"
REQUIRED_SECTIONS = (
    "## 1. 审计元数据",
    "## 2. 变更事实",
    "## 3. 静态质量门禁",
    "## 4. 新增与接口必要性审判",
    "## 5. 语义审计账本",
    "## 6. 十一项架构拷问",
    "## 7. Reduction Pass",
    "## 8. 最终门禁",
    "## 9. 提交 Commit",
)
AUTO_BLOCK = re.compile(
    r"<!-- RQG:AUTO:BEGIN (?P<name>[a-z_]+) -->\n(?P<body>.*?)\n<!-- RQG:AUTO:END (?P=name) -->",
    re.DOTALL,
)
PLACEHOLDERS = (
    "PENDING",
    "待填写",
    "TODO",
    "TBD",
    "以后处理",
    "后续处理",
    "应该没问题",
)
_MANUAL_KEYS = (
    "行为需求",
    "可验证场景",
    "设计边界与唯一所有者",
    "本轮非目标",
    "检查范围",
    "可继续削减项",
    "已执行减法",
    "保留项证据",
    "历史抽样结论",
    "历史抽样处理",
    "Reduction结论",
)

_TABLE_HEADER_ROWS = 2

QUESTION_TITLES = (
    "Q1. 可观察需求、失败场景、边界和非目标是否清楚，修改是否只服务于已证明需求？",
    "Q2. 是否优先保持 accepted design baseline 的既有接口、参数、返回结构、协议与 owner；任何变化是否由本轮明确需求授权，并同步生产方、消费方、README、Prompt、配置与测试？",
    "Q3. 新增或新实现能力是否先检索 API 目录/BM25 Top10、阅读真实源码与调用方，并尝试删除、内联、合并、复用或 code-judo 方案；复杂度是否真正减少而不是搬到更多 helper/mode/layer？",
    "Q4. 每项职责是否位于唯一 canonical owner/layer；本轮是否让局部 feature knowledge 泄漏到 shared/controller/state/infra，或让依赖方向、耦合与状态传播变得更难局部推理？",
    "Q5. 是否存在重复实现、父类能力下沉、子类共性未上提、已有公共协作者未复用，或新增 wrapper/helper 只是转发/改名/搬运复杂度而没有赚回自身间接性？",
    "Q6. 本轮 fallback、宽松契约、suppression、动态属性、静默截断和返回结构漂移是否全部完成语义裁决？",
    "Q7. docstring、注释与接口说明是否准确反映实现和可验证契约，没有删除或弱化必要说明？",
    "Q8. 是否破坏缓存、append-only、session/loop、事务、并发、锁或资源生命周期顺序；相关状态更新是否保持必要原子性，独立步骤是否被无意义串行编排并制造额外中间态？",
    "Q9. Critical/Error/Warning 是否独立核算，并在写完报告后再次执行减法；concept/branch/mode/helper/layer/coupling 是真正减少还是仅重新排列，能安全消除的剩余问题是否已处理？",
    "Q10. 是否存在未经授权的正则、关键词、词表、阈值或硬编码启发式替代协议、领域算法或语义模型判断？",
    "Q11. Tests 是否继续充当稳定行为/契约回归护栏：接口返回值、字段/格式/序列化、异常、状态副作用与已修复 Bug 是否由自动化测试保护；本轮是否先原样运行受影响既有测试，并且没有为迎合实现而机械修改断言？",
)

VALID_QUESTION_STATUS = frozenset({"PASS", "BLOCKING", "NOT_APPLICABLE"})
VALID_ADD_STATUS = frozenset({"JUSTIFIED", "BLOCKING"})
VALID_SEMANTIC_VERDICTS = frozenset(
    {
        "JUSTIFIED",
        "JUSTIFIED_EXTERNAL_COMPAT",
        "JUSTIFIED_USER_DEGRADATION",
        "JUSTIFIED_OPTIONAL_CAPABILITY",
        "JUSTIFIED_BEST_EFFORT_SIDE_EFFECT",
        "JUSTIFIED_PROTOCOL",
        "JUSTIFIED_EXPLICIT_UNION",
        "JUSTIFIED_NARROW_SUPPRESSION",
        "JUSTIFIED_DEPENDENCY_CONTRACT",
        "BLOCKING",
    }
)

_ADD_ROW = re.compile(
    r"^\| (?P<id>ADD-\d{3,}) \| `(?P<symbol>.*?)` \| (?P<reuse>.*?) \| "
    r"(?P<owner>.*?) \| (?P<necessity>.*?) \| (?P<evidence>.*?) \| (?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
_SEM_ROW = re.compile(
    r"^\| (?P<id>SEM-[0-9a-f]{10}) \| (?P<verdict>[A-Z_]+) \| (?P<reason>.*?) \| "
    r"(?P<action>.*?) \| (?P<evidence>.*?) \|$",
    re.MULTILINE,
)
_TEST_CHANGE_ROW = re.compile(
    r"^\| (?P<id>TEST-CHANGE-[0-9a-f]{10}) \| (?P<kind>[A-Z_]+) \| "
    r"(?P<reason>.*?) \| (?P<evidence>.*?) \| (?P<observable>.*?) \| "
    r"(?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
VALID_TEST_CHANGE_KINDS = frozenset(
    {
        "REQUIREMENT_CHANGE",
        "BUG_FIX",
        "TEST_DEFECT",
        "TEST_REFACTOR",
        "STABLE_ARCHITECTURE_CONTRACT",
        "IMPLEMENTATION_COUPLED",
    }
)
VALID_TEST_CHANGE_STATUS = frozenset({"JUSTIFIED", "BLOCKING"})
_QUESTION_BLOCK = re.compile(
    r"^### (?P<title>Q\d+\..+?)\n\n- 状态：(?P<status>[A-Z_]+)\n"
    r"- 证据：(?P<evidence>.+?)\n- 结论：(?P<conclusion>.+?)(?=\n\n### Q|\n\n## 7\.)",
    re.MULTILINE | re.DOTALL,
)
_MANUAL_LINE = re.compile(
    r"^- (?P<key>行为需求|可验证场景|设计边界与唯一所有者|本轮非目标|检查范围|可继续削减项|已执行减法|保留项证据|历史抽样结论|历史抽样处理|Reduction结论)：(?P<value>.*)$",
    re.MULTILINE,
)
_COMMIT_BLOCK = re.compile(
    r'```bash\n(?P<command>git commit -m ".*?"\n?)```',
    re.DOTALL,
)
_CHINESE = re.compile(r"[\u4e00-\u9fff]")
_MIN_COMMIT_BULLETS = 2
_PENDING_COMMIT_COMMAND = (
    'git commit -m "PENDING: 概括整个 patch\n\n- PENDING\n- PENDING"'
)


@dataclass(slots=True, frozen=True)
class SemanticItem:
    """
    绑定当前语义候选与稳定 SEM ID。

    Attributes:
        item_id: 稳定语义审计标识。
        finding: 原始静态 finding。
        delta: 是否为相对 Git baseline 新增的语义候选。
        touched_historical: 是否为本轮触及文件中的历史候选。
    """

    item_id: str
    finding: Finding
    delta: bool
    touched_historical: bool


@dataclass(slots=True, frozen=True)
class ReportValidation:
    """
    保存紧凑报告验证结果与语义状态。

    Attributes:
        issues: 报告契约或证据错误。
        semantic_status: 语义层最终状态。
    """

    issues: tuple[str, ...]
    semantic_status: str

    @property
    def passed(self) -> bool:
        """没有结构/证据问题时返回 True。"""
        return not self.issues


def _auto(name: str, lines: Iterable[str]) -> list[str]:
    """包装不可由模型修改的自动事实块。"""
    return [f"<!-- RQG:AUTO:BEGIN {name} -->", *lines, f"<!-- RQG:AUTO:END {name} -->"]


def _counts(report: ScanReport) -> dict[str, int]:
    """统计生产 findings 四档严重级别。"""
    result = dict.fromkeys(("critical", "error", "warning", "info"), 0)
    for finding in report.findings:
        if not finding.code.startswith("QG98"):
            result[finding.severity] += 1
    return result


def _semantic_id(key: str, occurrence: int) -> str:
    """由稳定语义身份键和同类 occurrence 生成 SEM ID。"""
    return "SEM-" + hashlib.sha256(f"{key}:{occurrence}".encode()).hexdigest()[:10]


def _semantic_items(report: ScanReport, facts: ReportFacts) -> tuple[SemanticItem, ...]:
    """基于同一份报告事实构造语义候选，避免重复 Git/测试事实计算。"""
    baseline = report.baseline
    remaining = (
        Counter()
        if baseline is None
        else Counter(baseline.semantic_review_fingerprints)
    )
    changed_paths = {item.path for item in facts.changed_files}
    occurrences: defaultdict[str, int] = defaultdict(int)
    items: list[SemanticItem] = []
    for finding in semantic_review_candidates(list(report.findings)):
        key = semantic_review_key(finding)
        occurrences[key] += 1
        historical = remaining[key] > 0
        if historical:
            remaining[key] -= 1
        items.append(
            SemanticItem(
                item_id=_semantic_id(key, occurrences[key]),
                finding=finding,
                delta=not historical,
                touched_historical=historical and finding.path in changed_paths,
            )
        )
    return tuple(sorted(items, key=lambda item: item.item_id))


def semantic_items(report: ScanReport) -> tuple[SemanticItem, ...]:
    """
    用 baseline 多重计数区分新增候选，避免单纯行号移动制造伪 Delta。

    Args:
        report: 当前完整生产扫描报告。

    Returns:
        带稳定 SEM ID、Delta 与 touched-historical 标记的语义候选。
    """
    revision = report.baseline.revision if report.baseline is not None else "HEAD"
    facts = build_report_facts(report, revision)
    return _semantic_items(report, facts)


def _historical_sample(
    items: tuple[SemanticItem, ...], limit: int = 8
) -> tuple[SemanticItem, ...]:
    """从 touched historical 中按严重度与规则多样性选择有限复核样本。"""
    priority = {"critical": 0, "error": 1, "warning": 2, "info": 3}
    candidates = sorted(
        (item for item in items if item.touched_historical),
        key=lambda item: (
            priority[item.finding.severity],
            item.finding.code,
            item.finding.path,
            item.finding.line,
        ),
    )
    selected: list[SemanticItem] = []
    seen_rules: set[str] = set()
    for item in candidates:
        if item.finding.code not in seen_rules:
            selected.append(item)
            seen_rules.add(item.finding.code)
        if len(selected) >= limit:
            return tuple(selected)
    selected_ids = {item.item_id for item in selected}
    selected.extend(item for item in candidates if item.item_id not in selected_ids)
    return tuple(selected[:limit])


def _manual_values(existing: str | None) -> dict[str, str]:
    """从旧报告读取固定人工字段；缺失字段统一显式初始化为 PENDING。"""
    result = dict.fromkeys(_MANUAL_KEYS, "PENDING")
    if existing is None:
        return result
    for match in _MANUAL_LINE.finditer(existing):
        result[match.group("key")] = match.group("value").strip()
    return result


def _existing_add_rows(
    existing: str | None,
) -> dict[str, tuple[str, str, str, str, str]]:
    """按 ADD ID 读取旧报告人工裁决。"""
    if not existing:
        return {}
    return {
        match.group("id"): (
            match.group("reuse"),
            match.group("owner"),
            match.group("necessity"),
            match.group("evidence"),
            match.group("status"),
        )
        for match in _ADD_ROW.finditer(existing)
    }


def _existing_sem_rows(existing: str | None) -> dict[str, tuple[str, str, str, str]]:
    """按 SEM ID 读取旧报告人工语义裁决。"""
    if not existing:
        return {}
    return {
        match.group("id"): (
            match.group("verdict"),
            match.group("reason"),
            match.group("action"),
            match.group("evidence"),
        )
        for match in _SEM_ROW.finditer(existing)
    }


def _existing_test_change_rows(
    existing: str | None,
) -> dict[str, tuple[str, str, str, str, str]]:
    """按稳定 TEST-CHANGE ID 读取旧报告中的测试契约裁决。"""
    if not existing:
        return {}
    return {
        match.group("id"): (
            match.group("kind"),
            match.group("reason"),
            match.group("evidence"),
            match.group("observable"),
            match.group("status"),
        )
        for match in _TEST_CHANGE_ROW.finditer(existing)
    }


def _test_change_items(facts: ReportFacts) -> tuple[object, ...]:
    """按稳定 ID 汇总需要语义说明的修改/删除测试和高风险新增测试。"""
    items = [change for audit in facts.test_audits for change in audit.case_changes]
    return tuple(sorted(items, key=lambda item: item.item_id))


def _existing_questions(existing: str | None) -> dict[str, tuple[str, str, str]]:
    """按 Q 编号保留旧报告固定架构拷问答案。"""
    if not existing:
        return {}
    result: dict[str, tuple[str, str, str]] = {}
    for match in _QUESTION_BLOCK.finditer(existing):
        result[match.group("title").split(".", 1)[0]] = (
            match.group("status"),
            match.group("evidence"),
            match.group("conclusion"),
        )
    return result


def _existing_commit_command(existing: str | None, current_digest: str) -> str:
    """仅在 patch digest 未变化时保留已经填写的完整 commit 命令。"""
    if not existing:
        return _PENDING_COMMIT_COMMAND
    metadata = front_matter(existing)
    if "change_digest" not in metadata or metadata["change_digest"] != current_digest:
        return _PENDING_COMMIT_COMMAND
    matches = list(_COMMIT_BLOCK.finditer(existing))
    if len(matches) != 1:
        return _PENDING_COMMIT_COMMAND
    return matches[0].group("command").rstrip("\n")


def _report_digest(
    report: ScanReport, facts: ReportFacts, items: tuple[SemanticItem, ...]
) -> str:
    """绑定当前 Git/接口/质量/语义候选的报告事实摘要。"""
    payload = "\n".join(
        [
            facts.digest,
            code_status(report),
            *(
                f"{item.item_id}:{semantic_review_key(item.finding)}:{item.delta}"
                for item in items
            ),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _append_metadata_and_changes(
    lines: list[str],
    report: ScanReport,
    revision: str,
    manual: dict[str, str],
    facts: ReportFacts,
) -> None:
    """追加审计元数据与机器变更事实。"""
    counts = _counts(report)
    lines.extend(
        [
            "## 1. 审计元数据",
            "",
            *_auto(
                "metadata",
                [
                    f"- Profile：`{report.project_name or '通用'}`",
                    f"- Git 基线：`{revision}`",
                    f"- Accepted design baseline：Git `{revision}`（设计权威来自冻结 revision，不来自 author/committer identity）。",
                    f"- 比较目标：`{report.comparison_target}`",
                    f"- 静态事实门禁：**{code_status(report)}**",
                    f"- Critical / Error / Warning / Info：**{counts['critical']} / {counts['error']} / {counts['warning']} / {counts['info']}**",
                    *(
                        [
                            "- 多语言事实："
                            + ", ".join(
                                f"{language}={count}"
                                for language, count in report.multilang_summary["files"].items()
                            )
                            + f"；multilang findings={report.multilang_summary['findings']}"
                        ]
                        if report.multilang_summary
                        else []
                    ),
                ],
            ),
            "",
            f"- 行为需求：{manual['行为需求']}",
            f"- 可验证场景：{manual['可验证场景']}",
            f"- 设计边界与唯一所有者：{manual['设计边界与唯一所有者']}",
            f"- 本轮非目标：{manual['本轮非目标']}",
            "",
            "## 2. 变更事实",
            "",
        ]
    )
    file_rows = ["| 状态 | 文件 | + / - |", "|---|---|---:|"]
    file_rows.extend(
        f"| `{escape(item.status)}` | `{escape(item.path)}` | +{item.added}/-{item.removed} |"
        for item in facts.changed_files
    )
    if len(file_rows) == _TABLE_HEADER_ROWS:
        file_rows.append("| — | 无生产文件变化 | 0 |")
    lines.extend(_auto("changed_files", file_rows))
    change_rows = ["| Change | Kind | Symbol | Path |", "|---|---|---|---|"]
    if report.interface_diff is not None:
        change_rows.extend(
            f"| `{change.change}` | `{change.kind}` | `{escape(change.symbol)}` | `{escape(change.path)}` |"
            for change in report.interface_diff.changes
            if not change.path.startswith("tests/") and "/tests/" not in change.path
        )
    if len(change_rows) == _TABLE_HEADER_ROWS:
        change_rows.append("| unchanged | — | — | — |")
    lines.extend(
        [
            "",
            "### Definition / API 变化",
            "",
            *_auto("definition_changes", change_rows),
            "",
        ]
    )
    test_rows = [
        "| Test file | + / - | Cases + / ~ / - | Assertions | Risks |",
        "|---|---:|---:|---:|---|",
    ]
    for audit in facts.test_audits:
        risk_codes = sorted({finding.code for finding in audit.findings})
        test_rows.append(
            f"| `{escape(audit.path)}` | +{audit.added}/-{audit.removed} | "
            f"+{len(audit.added_cases)} / ~{len(audit.modified_cases)} / -{len(audit.removed_cases)} | "
            f"{audit.before.assertions} → {audit.after.assertions} | "
            f"{', '.join(risk_codes) if risk_codes else '—'} |"
        )
    if len(test_rows) == _TABLE_HEADER_ROWS:
        test_rows.append("| — | 0 | 0 | 0 | 无测试文件变化 |")
    test_summary = [
        f"- 变更测试文件：{len(facts.test_audits)}",
        f"- 新增测试用例：{sum(len(item.added_cases) for item in facts.test_audits)}",
        f"- 修改既有测试用例：{sum(len(item.modified_cases) for item in facts.test_audits)}",
        f"- 删除既有测试用例：{sum(len(item.removed_cases) for item in facts.test_audits)}",
        f"- 需语义裁决的 TEST-CHANGE：{len(_test_change_items(facts))}",
    ]
    lines.extend(
        [
            "### Tests 变化",
            "",
            *_auto("test_change_summary", test_summary),
            "",
            *_auto("test_changed_files", test_rows),
            "",
        ]
    )


def _absolute_blocker_rows(report: ScanReport) -> list[str]:
    """渲染当前树绝对阻断，避免 compact report 只显示 Delta 而隐藏 REJECT 原因。"""
    rows = ["| Rule | Path | Symbol | Evidence path |", "|---|---|---|---|"]
    blockers = absolute_blocker_codes()
    for finding in sorted(
        (item for item in report.findings if item.code in blockers),
        key=lambda item: (item.code, item.path, item.line, item.symbol),
    ):
        relation_path = (
            finding.evidence["chain"]
            if finding.code == "QG168" and "chain" in finding.evidence
            else ()
        )
        path_text = " → ".join(str(item) for item in relation_path) or "—"
        rows.append(
            f"| `{finding.code}` | `{escape(finding.path)}:{finding.line}` | `{escape(finding.symbol)}` | {escape(path_text)} |"
        )
    if len(rows) == _TABLE_HEADER_ROWS:
        rows.append("| — | 无当前绝对阻断 | — | — |")
    return rows


def _relation_rows(report: ScanReport) -> list[str]:
    """渲染 affected-tests、接口 blast radius 和可达性保留摘要。"""
    graph = report.relation_graph
    rows = ["| Relation fact | Subject | Evidence |", "|---|---|---|"]
    if not graph:
        rows.append("| — | 无 Git 关系图 | — |")
        return rows
    current = graph["current"]
    rows.append(
        "| graph | repository | "
        f"nodes={current['nodes']}, edges={current['edges']}, calls={current['calls']}, imports={current['imports']} |"
    )
    affected = graph["affected_tests"]
    affected_text = (
        ", ".join(f"`{escape(str(item))}`" for item in affected) if affected else "—"
    )
    rows.append(
        f"| affected-tests | changed production | count={graph['affected_test_count']}; {affected_text} |"
    )
    for item in graph["preserved_reachability"]:
        path = " → ".join(str(part) for part in item["path"])
        rows.append(
            f"| reachability-preserved | `{escape(str(item['source']))}` | {escape(path)} |"
        )
    for item in graph["interface_impacts"]:
        rows.append(
            f"| interface-impact | `{escape(str(item['path']))}:{escape(str(item['symbol']))}` | "
            f"static_direct={item['direct_caller_count']}, static_transitive={item['transitive_dependent_count']}, static_test_files={item['affected_test_file_count']} |"
        )
    for item in graph["suppressed_direct_edge_findings"]:
        path = " → ".join(str(part) for part in item["replacement_path"])
        rows.append(
            f"| direct-edge-false-positive-suppressed | `{escape(str(item['symbol']))}` | {escape(path)} |"
        )
    return rows


def _relation_resolution_rows(report: ScanReport) -> list[str]:
    """渲染旧版不确定、现由关系路径证明的 resolution gain。"""
    rows = [
        "| Resolution | Subject | Evidence |",
        "|---|---|---|",
    ]
    graph = report.relation_graph
    if not graph:
        rows.append("| — | 无 Git 关系图 | — |")
        return rows
    for item in graph["preserved_reachability"]:
        path = " → ".join(str(part) for part in item["path"])
        rows.append(
            f"| direct-edge-preserved | `{escape(str(item['source']))}` | {escape(path)} |"
        )
    for item in graph["suppressed_direct_edge_findings"]:
        path = " → ".join(str(part) for part in item["replacement_path"])
        rows.append(
            f"| legacy-false-positive-resolved | `{escape(str(item['symbol']))}` | {escape(path)} |"
        )
    if len(rows) == _TABLE_HEADER_ROWS:
        rows.append("| — | 本轮无可由关系图进一步确定的旧歧义 | — |")
    return rows


def _relation_recall_rows(report: ScanReport) -> list[str]:
    """单列关系图新增召回，避免与 legacy finding parity 混账。"""
    rows = [
        "| Rule | Kind | Path | Evidence |",
        "|---|---|---|---|",
    ]
    for finding in sorted(
        (item for item in report.findings if item.code in {"QG193", "QG194"}),
        key=lambda item: (item.code, item.path, item.line, item.symbol),
    ):
        if finding.code == "QG193":
            affected_by = ", ".join(
                str(item) for item in finding.evidence["affected_by"][:3]
            )
            evidence = (
                f"test_change={finding.evidence['test_change']}; "
                f"affected_by={finding.evidence['affected_by_count']}; "
                f"examples={affected_by or '—'}"
            )
        else:
            interfaces = ", ".join(
                str(item) for item in finding.evidence["interfaces"][:3]
            )
            evidence = (
                f"interfaces={finding.evidence['interface_count']}; "
                f"direct≤{finding.evidence['max_direct_caller_count']}; "
                f"transitive≤{finding.evidence['max_transitive_dependent_count']}; "
                f"examples={interfaces or '—'}"
            )
        rows.append(
            f"| `{finding.code}` | `{escape(str(finding.evidence['semantic_review_kind']))}` | "
            f"`{escape(finding.path)}:{finding.line}` | {escape(evidence)} |"
        )
    if len(rows) == _TABLE_HEADER_ROWS:
        rows.append("| — | — | 本轮无 RelationGraph 新召回候选 | — |")
    return rows


def _append_static_and_additions(
    lines: list[str], report: ScanReport, existing: str | None, facts: ReportFacts
) -> None:
    """追加静态门禁与新增接口必要性审判。"""
    counts = _counts(report)
    baseline_summary = (
        report.baseline.summary
        if report.baseline is not None
        else dict.fromkeys(counts, 0)
    )
    static_rows = ["| Severity | Baseline | Current | Delta |", "|---|---:|---:|---:|"]
    for severity in ("critical", "error", "warning", "info"):
        before = int(baseline_summary[severity])
        static_rows.append(
            f"| {severity.upper()} | {before} | {counts[severity]} | {counts[severity] - before:+d} |"
        )
    delta_rows = ["| ID | Rule | Severity | Path | Symbol |", "|---|---|---|---|---|"]
    delta_rows.extend(
        f"| {delta.item_id} | `{delta.finding.code}` | `{delta.finding.severity.upper()}` | `{escape(delta.finding.path)}:{delta.finding.line}` | `{escape(delta.finding.symbol)}` |"
        for delta in facts.quality_deltas
    )
    if len(delta_rows) == _TABLE_HEADER_ROWS:
        delta_rows.append("| — | — | — | 无普通 C/E/W 增量 | — |")
    lines.extend(
        [
            "## 3. 静态质量门禁",
            "",
            *_auto("static_gate", static_rows),
            "",
            *_auto("quality_deltas", delta_rows),
            "",
            *_auto("current_absolute_blockers", _absolute_blocker_rows(report)),
            "",
            *_auto("relation_impact", _relation_rows(report)),
            "",
            "### RelationGraph 增量审计分账",
            "",
            "Legacy finding 继续由原 QG 规则与版本回放负责；以下只列 Graph 带来的确定性提升与新增召回，不用新增项掩盖旧 finding 消失。",
            "",
            *_auto("relation_resolution_gain", _relation_resolution_rows(report)),
            "",
            *_auto("relation_new_recall", _relation_recall_rows(report)),
            "",
        ]
    )
    add_auto = ["| ADD | Kind | Symbol | Path |", "|---|---|---|---|"]
    add_auto.extend(
        f"| {item.item_id} | `{item.change.kind}` | `{escape(item.change.symbol)}` | `{escape(item.change.path)}` |"
        for item in facts.additions
    )
    if len(add_auto) == _TABLE_HEADER_ROWS:
        add_auto.append("| — | — | 无生产新增接口 | — |")
    lines.extend(["## 4. 新增与接口必要性审判", "", *_auto("additions", add_auto), ""])
    lines.extend(
        [
            "> `复用检索` 必须引用 doc-search ledger/Top10 与真实源码；`所有权` 覆盖 HEAD/父类/MRO/兄弟类/公共协作者；`必要性` 包含删除/内联/合并/复用减法结论。",
            "",
            "| ADD | Symbol | 复用检索 | 所有权 | 必要性/减法 | 源码/调用方证据 | 状态 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    previous = _existing_add_rows(existing)
    for item in facts.additions:
        row = previous.get(
            item.item_id, ("PENDING", "PENDING", "PENDING", "PENDING", "PENDING")
        )
        lines.append(
            f"| {item.item_id} | `{escape(item.change.symbol)}` | {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} |"
        )
    if not facts.additions:
        lines.append(
            "| — | 无生产新增接口 | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | JUSTIFIED |"
        )
    lines.append("")


def _append_semantic_and_questions(
    lines: list[str],
    report: ScanReport,
    existing: str | None,
    manual: dict[str, str],
    facts: ReportFacts,
    items: tuple[SemanticItem, ...],
) -> None:
    """追加 Delta 语义账本、历史风险抽样与十一问。"""
    delta_items = [item for item in items if item.delta]
    touched = [item for item in items if item.touched_historical]
    delta_rows = [
        "| SEM | Rule | Severity | Kind | Question | Location | Relation | Message |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in delta_items:
        finding = item.finding
        relation = (
            ", ".join(
                f"{label}={finding.evidence[key]}"
                for key, label in (
                    ("relation_direct_caller_count", "callers"),
                    ("relation_callee_count", "callees"),
                    ("relation_affected_test_count", "affected_tests"),
                )
                if key in finding.evidence
            )
            or "—"
        )
        delta_rows.append(
            f"| {item.item_id} | `{finding.code}` | `{finding.severity.upper()}` | `{finding.evidence['semantic_review_kind']}` | "
            f"`{finding.evidence.get('semantic_review_question', '')}` | `{escape(finding.path)}:{finding.line}` | "
            f"{escape(relation)} | {escape(finding.message)} |"
        )
    if len(delta_rows) == _TABLE_HEADER_ROWS:
        delta_rows.append("| — | — | — | — | — | — | — | 无本轮语义 Delta |")
    historical_counts = Counter(
        (item.finding.code, str(item.finding.evidence["semantic_review_kind"]))
        for item in items
        if not item.delta
    )
    baseline_instances = (
        sum(report.baseline.semantic_review_fingerprints.values())
        if report.baseline
        else 0
    )
    summary = [
        f"- 当前语义候选：{len(items)}",
        f"- 本轮 Delta：{len(delta_items)}",
        f"- 本轮触及的历史候选：{len(touched)}",
        f"- Git 基线语义候选实例：{baseline_instances}",
        *(
            f"- 历史 `{code}` / `{kind}`：{count}"
            for (code, kind), count in sorted(historical_counts.items())
        ),
    ]
    sample = _historical_sample(items)
    sample_rows = [
        "| SEM | Rule | Severity | Location | Message |",
        "|---|---|---|---|---|",
    ]
    sample_rows.extend(
        f"| {item.item_id} | `{item.finding.code}` | `{item.finding.severity.upper()}` | `{escape(item.finding.path)}:{item.finding.line}` | {escape(item.finding.message)} |"
        for item in sample
    )
    if len(sample_rows) == _TABLE_HEADER_ROWS:
        sample_rows.append("| — | — | — | — | 无 touched historical 抽样 |")
    lines.extend(
        [
            "## 5. 语义审计账本",
            "",
            *_auto("semantic_delta", delta_rows),
            "",
            *_auto("semantic_summary", summary),
            "",
        ]
    )
    test_items = _test_change_items(facts)
    test_auto = [
        "| TEST | Change | Case | Path | Assertions | Removed observable assertions | Static signals |",
        "|---|---|---|---|---:|---|---|",
    ]
    for item in test_items:
        signals = ", ".join(item.signals) if item.signals else "—"
        removed = (
            "; ".join(item.removed_assertions[:3]) if item.removed_assertions else "—"
        )
        test_auto.append(
            f"| {item.item_id} | `{item.change}` | `{escape(item.case)}` | `{escape(item.path)}` | "
            f"{item.before_assertions} → {item.after_assertions} | {escape(removed)} | {signals} |"
        )
    if len(test_auto) == _TABLE_HEADER_ROWS:
        test_auto.append("| — | — | 无需测试契约语义裁决 | — | 0 | — | — |")
    lines.extend(
        [
            "### 测试契约变化",
            "",
            *_auto("test_contract_changes", test_auto),
            "",
            "> 既有测试默认是稳定行为/契约基线。纯实现重构不得为了让测试通过而同步改断言；"
            "只有真实需求变化、Bug 修复导致旧行为失效、测试自身缺陷或保持行为不变的测试重构可以 JUSTIFIED。"
            "源码字符串、private helper、历史删除名称等实现耦合测试默认 BLOCKING；若它实际表达长期架构不变量，"
            "必须明确标记 STABLE_ARCHITECTURE_CONTRACT，并给出独立于本次实现的长期契约证据。",
            "",
            "| TEST | 类型 | 稳定行为/旧断言失效原因 | 需求/Bug/测试缺陷证据 | 可观察验证路径 | 状态 |",
            "|---|---|---|---|---|---|",
        ]
    )
    previous_tests = _existing_test_change_rows(existing)
    for item in test_items:
        row = previous_tests.get(
            item.item_id,
            ("PENDING", "PENDING", "PENDING", "PENDING", "PENDING"),
        )
        lines.append(
            f"| {item.item_id} | {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} |"
        )
    if not test_items:
        lines.append(
            "| — | TEST_REFACTOR | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | JUSTIFIED |"
        )
    lines.extend(
        [
            "",
            "### Historical 风险抽样",
            "",
            *_auto("semantic_history_sample", sample_rows),
            "",
        ]
    )
    lines.extend(
        [
            f"- 历史抽样结论：{manual['历史抽样结论'] if sample else 'NOT_APPLICABLE'}",
            f"- 历史抽样处理：{manual['历史抽样处理'] if sample else 'NOT_APPLICABLE'}",
            "",
            "> 本轮 Delta 必须逐项裁决；历史候选只做聚合 + 有限风险抽样。抽样发现 INVALID 时先修代码并重新 audit，不要求为了凑数量修改本来合理的历史代码。",
            "",
            "| SEM | Verdict | Reason | Action | Evidence |",
            "|---|---|---|---|---|",
        ]
    )
    previous_sem = _existing_sem_rows(existing)
    for item in delta_items:
        row = previous_sem.get(
            item.item_id, ("PENDING", "PENDING", "PENDING", "PENDING")
        )
        lines.append(f"| {item.item_id} | {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
    if not delta_items:
        lines.append(
            "| — | JUSTIFIED | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE |"
        )
    lines.extend(["", "## 6. 十一项架构拷问", ""])
    previous_questions = _existing_questions(existing)
    for index, title in enumerate(QUESTION_TITLES, 1):
        status, evidence, conclusion = previous_questions.get(
            f"Q{index}", ("PENDING", "PENDING", "PENDING")
        )
        lines.extend(
            [
                f"### {title}",
                "",
                f"- 状态：{status}",
                f"- 证据：{evidence}",
                f"- 结论：{conclusion}",
                "",
            ]
        )


def _append_reduction_and_final(
    lines: list[str],
    report: ScanReport,
    manual: dict[str, str],
    facts: ReportFacts,
    items: tuple[SemanticItem, ...],
) -> None:
    """追加 Reduction Pass 与只读最终门禁说明。"""
    counts = _counts(report)
    delta_count = sum(item.delta for item in items)
    reduction = [
        f"- 当前 Warning：{counts['warning']}",
        f"- 当前语义 Delta：{delta_count}",
        f"- 当前新增接口：{len(facts.additions)}",
        f"- 当前 TEST-CHANGE：{len(_test_change_items(facts))}",
        f"- 存量三档债务已减少：{facts.legacy_debt_reduced}",
    ]
    lines.extend(["## 7. Reduction Pass", "", *_auto("reduction_facts", reduction), ""])
    lines.extend(
        [
            f"- 检查范围：{manual['检查范围']}",
            f"- 可继续削减项：{manual['可继续削减项']}",
            f"- 已执行减法：{manual['已执行减法']}",
            f"- 保留项证据：{manual['保留项证据']}",
            f"- Reduction结论：{manual['Reduction结论']}",
            "",
            "## 8. 最终门禁",
            "",
            *_auto(
                "final_gate",
                [
                    "- 报告状态：`PENDING_VERIFY`",
                    "- 最终 PASS / REVIEW_REQUIRED / REJECT 只能由 `verify` 基于最新源码重新计算。",
                    "- `verify` 只读，不补报告、不修改代码、不把 BLOCKING 自动改成 JUSTIFIED。",
                ],
            ),
            "",
        ]
    )


def _append_commit(
    lines: list[str],
    report: ScanReport,
    revision: str,
    existing: str | None,
    facts: ReportFacts,
    items: tuple[SemanticItem, ...],
) -> None:
    """追加必须概括整个 patch 的可执行提交命令。"""
    full_patch_files = (
        *facts.changed_files,
        *facts.test_changed_files,
        *facts.artifact_changed_files,
    )
    added = sum(item.added for item in full_patch_files)
    removed = sum(item.removed for item in full_patch_files)
    semantic_delta = sum(item.delta for item in items)
    current_digest = _report_digest(report, facts, items)
    command = _existing_commit_command(existing, current_digest)
    scope = [
        f"- 完整 patch：`{revision} → {report.comparison_target}`",
        f"- 文件变化：{len(full_patch_files)} 个（+{added} / -{removed}）",
        f"- 生产定义接口：新增 {facts.interface_added_definitions} / 删除 {facts.interface_removed_definitions} / 净变化 {facts.interface_definition_net}",
        f"- 普通 C/E/W Delta：{len(facts.quality_deltas)}",
        f"- 语义 Delta：{semantic_delta}",
        f"- 测试契约变化：{len(_test_change_items(facts))}",
    ]
    lines.extend(
        [
            "## 9. 提交 Commit",
            "",
            *_auto("commit_scope", scope),
            "",
            "> 下方命令必须概括上述整个 patch，不得只描述某个文件、单条 finding 或最后一次局部修复；主题与摘要应反映最终 Reduction Pass 后的整体交付。",
            "",
            "```bash",
            *command.splitlines(),
            "```",
            "",
        ]
    )


def _render_compact_report(
    report: ScanReport,
    revision: str,
    existing: str | None,
    facts: ReportFacts,
    items: tuple[SemanticItem, ...],
) -> str:
    """使用同一份不可变事实快照渲染报告。"""
    manual = _manual_values(existing)
    lines = [
        "---",
        f"report_schema: {REPORT_SCHEMA}",
        f"baseline_revision: {revision}",
        f"change_digest: {_report_digest(report, facts, items)}",
        f"tool_status: {code_status(report)}",
        "final_status: PENDING_VERIFY",
        "---",
        "",
        REPORT_TITLE,
        "",
    ]
    _append_metadata_and_changes(lines, report, revision, manual, facts)
    _append_static_and_additions(lines, report, existing, facts)
    _append_semantic_and_questions(lines, report, existing, manual, facts, items)
    _append_reduction_and_final(lines, report, manual, facts, items)
    _append_commit(lines, report, revision, existing, facts, items)
    return "\n".join(lines)


def render_compact_report(
    report: ScanReport, *, revision: str, existing: str | None = None
) -> str:
    """
    生成固定九段紧凑报告，并保留仍存在对象的人工裁决与有效 commit。

    Args:
        report: 当前完整生产扫描报告。
        revision: 绑定报告的 Git baseline revision。
        existing: 可选旧报告，用于保留仍有效的人工字段。

    Returns:
        固定九段 schema 的 Markdown 审计报告。
    """
    facts = build_report_facts(report, revision)
    items = _semantic_items(report, facts)
    return _render_compact_report(report, revision, existing, facts, items)


def _has_placeholder(value: str) -> bool:
    """判断模型字段是否仍是占位或空话。"""
    normalized = value.strip()
    return not normalized or any(
        marker.lower() in normalized.lower() for marker in PLACEHOLDERS
    )


def _auto_blocks(text: str) -> dict[str, str]:
    """提取全部机器自动事实块。"""
    return {
        match.group("name"): match.group("body") for match in AUTO_BLOCK.finditer(text)
    }


def _semantic_verdict_issues(item: SemanticItem, verdict: str) -> list[str]:
    """按候选类型限制可用 verdict，避免万能 JUSTIFIED。"""
    finding = item.finding
    if verdict not in VALID_SEMANTIC_VERDICTS:
        return [f"{item.item_id} verdict 非法：{verdict}"]
    unauthorized_heuristic = finding.code == "QG178" and not bool(
        finding.evidence["authorized_by_baseline"]
    )
    if unauthorized_heuristic and verdict != "BLOCKING":
        return [f"{item.item_id} 是未获 Git 基线授权的 QG178，只能 BLOCKING。"]
    allowed = {
        "QG020": {"JUSTIFIED_NARROW_SUPPRESSION", "BLOCKING"},
        "QG128": {"JUSTIFIED_DEPENDENCY_CONTRACT", "JUSTIFIED_PROTOCOL", "BLOCKING"},
        "QG144": {"JUSTIFIED_EXPLICIT_UNION", "JUSTIFIED_PROTOCOL", "BLOCKING"},
        "QG191": {"JUSTIFIED_PROTOCOL", "BLOCKING"},
    }
    if finding.code not in allowed or verdict in allowed[finding.code]:
        return []
    return [f"{item.item_id} {finding.code} verdict 与该规则契约不匹配。"]


def _header_and_auto_issues(
    text: str,
    report: ScanReport,
    revision: str,
    facts: ReportFacts,
    items: tuple[SemanticItem, ...],
) -> list[str]:
    """返回报告身份、源码 digest 与机器事实区块问题。"""
    issues: list[str] = []
    headings = tuple(line for line in text.splitlines() if line.startswith("## "))
    if text.count(REPORT_TITLE) != 1 or headings != REQUIRED_SECTIONS:
        issues.append("审计报告必须且只能包含固定九个二级章节，并保持名称与顺序不变。")
    metadata = front_matter(text)
    expected = {
        "report_schema": REPORT_SCHEMA,
        "baseline_revision": revision,
        "change_digest": _report_digest(report, facts, items),
    }
    labels = {
        "report_schema": "报告 schema 不匹配。",
        "baseline_revision": "报告 Git baseline 已过期。",
        "change_digest": "报告 change_digest 与当前代码事实不一致，必须重新 audit。",
    }
    for key, value in expected.items():
        if key not in metadata or metadata[key] != value:
            issues.append(labels[key])
    regenerated = _render_compact_report(report, revision, text, facts, items)
    if _auto_blocks(text) != _auto_blocks(regenerated):
        issues.append("机器自动事实区块被修改或已过期，必须重新 audit。")
    manual = _manual_values(text)
    for key in ("行为需求", "可验证场景", "设计边界与唯一所有者", "本轮非目标"):
        if _has_placeholder(manual[key]):
            issues.append(f"审计元数据人工字段未完成：{key}")
    return issues


def _addition_issues(text: str, facts: ReportFacts) -> list[str]:
    """返回新增接口复用、所有权、减法和证据问题。"""
    issues: list[str] = []
    rows = _existing_add_rows(text)
    for item in facts.additions:
        if item.item_id not in rows:
            issues.append(f"缺少 {item.item_id} 新增接口审判。")
            continue
        reuse, owner, necessity, evidence, status = rows[item.item_id]
        if status not in VALID_ADD_STATUS:
            issues.append(f"{item.item_id} 状态必须为 JUSTIFIED 或 BLOCKING。")
        values = (
            ("复用检索", reuse),
            ("所有权", owner),
            ("必要性", necessity),
            ("证据", evidence),
        )
        issues.extend(
            f"{item.item_id} {label}未完成。"
            for label, value in values
            if _has_placeholder(value)
        )
    return issues


def _semantic_issues(
    text: str, items: tuple[SemanticItem, ...]
) -> tuple[list[str], bool]:
    """返回本轮 Delta 语义候选、历史抽样问题与阻断状态。"""
    issues: list[str] = []
    rows = _existing_sem_rows(text)
    blocking = False
    for item in (candidate for candidate in items if candidate.delta):
        if item.item_id not in rows:
            issues.append(f"缺少 {item.item_id} 语义裁决。")
            continue
        verdict, reason, action, evidence = rows[item.item_id]
        issues.extend(_semantic_verdict_issues(item, verdict))
        blocking = blocking or verdict == "BLOCKING"
        values = (("Reason", reason), ("Action", action), ("Evidence", evidence))
        issues.extend(
            f"{item.item_id} {label} 未完成。"
            for label, value in values
            if _has_placeholder(value)
        )
    if _historical_sample(items):
        manual = _manual_values(text)
        for key in ("历史抽样结论", "历史抽样处理"):
            if _has_placeholder(manual[key]):
                issues.append(f"Historical 风险抽样未完成：{key}")
    return issues, blocking


def _test_contract_issues(text: str, facts: ReportFacts) -> tuple[list[str], bool]:
    """验证既有测试变化和实现耦合新增测试均有可证据化语义裁决。"""
    issues: list[str] = []
    rows = _existing_test_change_rows(text)
    blocking = False
    invalid_test_findings = [
        finding
        for audit in facts.test_audits
        for finding in audit.findings
        if finding.code in {"QG174", "QG175"}
    ]
    issues.extend(
        f"{finding.code}：{finding.path} 当前测试本身不可作为有效回归证据，必须先修复，不能语义豁免。"
        for finding in invalid_test_findings
    )
    for item in _test_change_items(facts):
        row = rows.get(item.item_id)
        if row is None:
            issues.append(f"缺少 {item.item_id} 测试契约裁决。")
            continue
        kind, reason, evidence, observable, status = row
        if kind not in VALID_TEST_CHANGE_KINDS:
            issues.append(f"{item.item_id} 类型非法：{kind}")
        if status not in VALID_TEST_CHANGE_STATUS:
            issues.append(f"{item.item_id} 状态必须为 JUSTIFIED 或 BLOCKING。")
        blocking = blocking or status == "BLOCKING"
        for label, value in (
            ("稳定行为/旧断言失效原因", reason),
            ("需求/Bug/测试缺陷证据", evidence),
            ("可观察验证路径", observable),
        ):
            if _has_placeholder(value):
                issues.append(f"{item.item_id} {label}未完成。")
        if kind == "IMPLEMENTATION_COUPLED" and status != "BLOCKING":
            issues.append(
                f"{item.item_id} 已认定为 IMPLEMENTATION_COUPLED，只能 BLOCKING；先把测试改回稳定行为/契约验证后再重新审计。"
            )
        if (
            item.signals
            and status == "JUSTIFIED"
            and kind != "STABLE_ARCHITECTURE_CONTRACT"
        ):
            issues.append(
                f"{item.item_id} 命中实现耦合静态信号；若要 JUSTIFIED，只能证明其为长期 STABLE_ARCHITECTURE_CONTRACT，否则应 BLOCKING。"
            )
    return issues, blocking


def _question_issues(
    text: str,
    facts: ReportFacts,
    items: tuple[SemanticItem, ...],
) -> tuple[list[str], bool]:
    """返回固定架构拷问问题及与语义/测试/关系事实的关联阻断状态。"""
    issues: list[str] = []
    questions = _existing_questions(text)
    blocking = False
    for index in range(1, len(QUESTION_TITLES) + 1):
        key = f"Q{index}"
        if key not in questions:
            issues.append(f"缺少 {key} 架构拷问。")
            continue
        status, evidence, conclusion = questions[key]
        if status not in VALID_QUESTION_STATUS:
            issues.append(f"{key} 状态非法：{status}")
        blocking = blocking or status == "BLOCKING"
        if _has_placeholder(evidence) or _has_placeholder(conclusion):
            issues.append(f"{key} 证据或结论未完成。")
    delta_items = [item for item in items if item.delta]
    rules = {item.finding.code for item in delta_items}
    q6_required = any(
        "Q6" in str(item.finding.evidence["semantic_review_question"])
        for item in delta_items
    )
    has_production_interface_changes = bool(
        facts.additions
        or facts.parameter_interface_changes
        or facts.interface_removed_definitions
        or facts.protocol_findings
    )
    if (
        has_production_interface_changes
        and "Q2" in questions
        and questions["Q2"][0] == "NOT_APPLICABLE"
    ):
        issues.append(
            "存在生产接口/协议变化时 Q2 不得 NOT_APPLICABLE；必须以 accepted design baseline 为默认契约，给出本轮明确需求授权与兼容性证据。"
        )
    if facts.additions and "Q3" in questions and questions["Q3"][0] == "NOT_APPLICABLE":
        issues.append("存在 ADD 时 Q3 不得 NOT_APPLICABLE。")
    if q6_required and "Q6" in questions and questions["Q6"][0] == "NOT_APPLICABLE":
        issues.append("存在 Q6 语义候选时 Q6 不得 NOT_APPLICABLE。")
    if (
        "QG178" in rules
        and "Q10" in questions
        and questions["Q10"][0] == "NOT_APPLICABLE"
    ):
        issues.append("存在 QG178 时 Q10 不得 NOT_APPLICABLE。")
    if (
        "QG191" in rules
        and "Q3" in questions
        and questions["Q3"][0] == "NOT_APPLICABLE"
    ):
        issues.append("存在 QG191 手工协议构造时 Q3 不得 NOT_APPLICABLE。")
    q11_required = bool(
        facts.test_audits or has_production_interface_changes or facts.changed_files
    )
    if q11_required and "Q11" in questions and questions["Q11"][0] == "NOT_APPLICABLE":
        issues.append(
            "存在生产或测试变化时 Q11 不得 NOT_APPLICABLE；必须说明稳定行为/接口契约的自动化回归覆盖，以及受影响既有测试是否先原样运行。"
        )
    return issues, blocking


def _reduction_issues(text: str) -> list[str]:
    """返回报告后减法审查未完成的问题。"""
    issues: list[str] = []
    manual = _manual_values(text)
    for key in ("检查范围", "可继续削减项", "已执行减法", "保留项证据"):
        if _has_placeholder(manual[key]):
            issues.append(f"Reduction Pass 未完成：{key}")
    if not manual["Reduction结论"].startswith("COMPLETE"):
        issues.append("Reduction Pass 结论必须以 COMPLETE 开头；报告填写不是流程终点。")
    return issues


def _commit_issues(text: str) -> list[str]:
    """验证提交章节包含整包 commit，且不通过命令覆盖贡献者 Git identity。"""
    commit_section = text.partition("## 9. 提交 Commit")[2]
    bash_blocks = re.findall(r"```bash\n(?P<body>.*?)\n```", commit_section, re.DOTALL)
    forbidden_identity = re.compile(
        r"(?:git\s+config\s+(?:--(?:global|local|system)\s+)?user\.(?:name|email)"
        r"|git\s+commit\b[^\n]*--author(?:=|\s)"
        r"|GIT_(?:AUTHOR|COMMITTER)_(?:NAME|EMAIL)=)",
        re.IGNORECASE,
    )
    if any(forbidden_identity.search(block) for block in bash_blocks):
        return [
            "QG984：提交命令不得为了对齐设计基线覆盖 Git author/committer identity；使用当前执行者身份。"
        ]
    matches = list(_COMMIT_BLOCK.finditer(text))
    if len(matches) != 1:
        return ["QG984：提交章节必须且只能包含一条可执行的多行 `git commit -m` 命令。"]
    command = matches[0].group("command").rstrip("\n")
    if _has_placeholder(command):
        return [
            "QG984：提交命令仍是占位内容；必须在最终 patch 冻结后重新概括整个 patch。"
        ]
    lines = command.splitlines()
    subject = lines[0] if lines else ""
    bullets = [line for line in lines[1:] if line.startswith("- ")]
    subject_ok = bool(
        re.search(
            r'^git commit -m "[a-z]+(?:\([^)]+\))?!?:\s*.*[\u4e00-\u9fff]', subject
        )
    )
    if not subject_ok:
        return [
            "QG984：提交主题必须采用 `type(scope): 中文主题`（scope 可省略）的可执行 `git commit -m` 形式。"
        ]
    if len(bullets) < _MIN_COMMIT_BULLETS:
        return [
            "QG984：提交摘要至少需要两条 `- ` 开头的中文要点，用于覆盖整个 patch 的主要变化。"
        ]
    if not all(_CHINESE.search(line) for line in bullets):
        return ["QG984：提交摘要每条都必须包含中文说明。"]
    if not command.endswith('"'):
        return ["QG984：多行 `git commit -m` 必须以同一个完整双引号参数结束。"]
    return []


def validate_compact_report(
    text: str, report: ScanReport, *, revision: str
) -> ReportValidation:
    """
    验证机器事实、ADD/SEM/十一问和 Reduction Pass 均与最新源码一致。

    Args:
        text: 待验证的完整 Markdown 报告。
        report: 当前最新生产扫描报告。
        revision: 当前报告必须绑定的 Git baseline revision。

    Returns:
        报告契约问题与语义 PASS/REJECT 状态。
    """
    facts = build_report_facts(report, revision)
    items = _semantic_items(report, facts)
    issues = _header_and_auto_issues(text, report, revision, facts, items)
    issues.extend(_addition_issues(text, facts))
    semantic_issues, semantic_blocking = _semantic_issues(text, items)
    issues.extend(semantic_issues)
    test_issues, test_blocking = _test_contract_issues(text, facts)
    issues.extend(test_issues)
    question_issues, question_blocking = _question_issues(text, facts, items)
    issues.extend(question_issues)
    issues.extend(_reduction_issues(text))
    issues.extend(_commit_issues(text))
    status = (
        "REJECT" if semantic_blocking or test_blocking or question_blocking else "PASS"
    )
    return ReportValidation(tuple(issues), status)
