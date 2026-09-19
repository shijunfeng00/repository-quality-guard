from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Severity = Literal["info", "warning", "error", "critical"]
Confidence = Literal["low", "medium", "high"]


@dataclass(slots=True, frozen=True)
class Finding:
    """
    表示一条可定位、可序列化的代码质量发现。

    发现由规则代码、严重级别、置信度、源码位置和修复建议共同组成。
    """

    code: str
    severity: Severity
    confidence: Confidence
    path: str
    line: int
    column: int
    message: str
    symbol: str = ""
    suggestion: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    source: str = "quality-guard"

    @property
    def fingerprint(self) -> str:
        """
        生成跨报告稳定的问题指纹。

        Returns:
            由来源、规则、路径、符号和规范化问题语义摘要组成的稳定字符串。
        """
        stable_symbol = self.symbol or f"line:{self.line}"
        normalized_message = " ".join(self.message.split())
        stable_message = re.sub(
            r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])",
            "#",
            normalized_message,
        )
        message_digest = hashlib.sha256(stable_message.encode("utf-8")).hexdigest()[:16]
        return f"{self.source}:{self.code}:{self.path}:{stable_symbol}:message:{message_digest}"

    def to_dict(self) -> dict[str, Any]:
        """
        将发现转换为可写入 JSON 的字典。

        Returns:
            包含全部字段及稳定指纹的字典。
        """
        result = asdict(self)
        result["fingerprint"] = self.fingerprint
        return result


@dataclass(slots=True)
class Definition:
    """
    保存类、函数或方法的静态结构与使用统计。

    该模型同时承载复杂度、调用次数和 docstring 完整性等规则输入。
    """

    module: str
    qualname: str
    name: str
    kind: Literal["function", "method", "class"]
    path: Path
    line: int
    end_line: int
    column: int
    code_line_count: int
    decorators: tuple[str, ...] = ()
    bases: tuple[str, ...] = ()
    direct_method_count: int = 0
    public_method_count: int = 0
    tiny_method_count: int = 0
    parameter_count: int = 0
    parameter_names: tuple[str, ...] = ()
    boolean_flag_count: int = 0
    branch_count: int = 0
    max_nesting: int = 0
    wrapper_target: str = ""
    body_fingerprint: str = ""
    docstring_text: str = ""
    docstring_line_count: int = 0
    docstring_sections: tuple[str, ...] = ()
    documented_parameters: tuple[str, ...] = ()
    calls: int = 0
    references: int = 0

    @property
    def lines(self) -> int:
        """
        返回排除 docstring 后的代码行数。

        Returns:
            用于结构规则判断的实际代码行数。
        """
        return self.code_line_count

    @property
    def physical_lines(self) -> int:
        """
        返回定义在源文件中的完整物理跨度。

        Returns:
            从定义行到结束行的总行数，包含 docstring。
        """
        return self.end_line - self.line + 1

    @property
    def symbol_id(self) -> str:
        """
        生成模块内唯一的定义标识。

        Returns:
            模块名与限定名称拼接后的符号标识。
        """
        return f"{self.module}.{self.qualname}" if self.module else self.qualname

    @property
    def has_docstring(self) -> bool:
        """
        判断定义是否存在非空 docstring。

        Returns:
            存在非空 docstring 时返回 True，否则返回 False。
        """
        return bool(self.docstring_text)

    def has_complete_docstring(self, min_lines: int, require_sections: bool) -> bool:
        """
        按当前配置判断 docstring 是否满足完整结构规范。

        Args:
            min_lines: docstring 至少需要包含的有效内容行数。
            require_sections: 函数和方法是否必须完整说明参数与返回值。

        Returns:
            当前 docstring 满足配置要求时返回 True，否则返回 False。
        """
        if self.docstring_line_count < min_lines:
            return False
        if self.kind == "class" or not require_sections:
            return True
        documented = set(self.documented_parameters)
        parameters_complete = all(name in documented for name in self.parameter_names)
        return parameters_complete and bool(
            {"Returns", "Yields"} & set(self.docstring_sections)
        )

    @property
    def externally_invoked(self) -> bool:
        """
        判断定义是否可能由框架或协议动态调用。

        Returns:
            命中特殊方法、访问器或框架装饰器时返回 True，否则返回 False。
        """
        external_markers = {
            "abstractmethod",
            "callback",
            "command",
            "event",
            "fixture",
            "property",
            "route",
            "signal",
            "task",
            "validator",
        }
        if self.name.startswith("__") and self.name.endswith("__"):
            return True
        if self.name.startswith("visit_") and any(
            base.endswith("NodeVisitor") for base in self.bases
        ):
            return True
        return any(
            marker in decorator.lower()
            for marker in external_markers
            for decorator in self.decorators
        )


@dataclass(slots=True, frozen=True)
class Usage:
    """
    表示一次名称或属性的静态引用。

    该记录用于近似统计函数调用、类实例化和普通引用次数。
    """

    module: str
    path: Path
    line: int
    column: int
    target: str
    base: str
    is_call: bool
    owner_class: str = ""


InterfaceKind = Literal[
    "file",
    "class",
    "function",
    "global_variable",
    "method",
    "member_variable",
]
InterfaceChangeType = Literal["added", "removed", "modified"]


@dataclass(slots=True, frozen=True)
class InterfaceParameter:
    """
    表示函数或成员函数签名中的一个参数。

    参数模型保留名称、参数种类、类型注解和默认值，避免比较时只关注名称。
    """

    name: str
    kind: str
    annotation: str = ""
    has_default: bool = False
    default: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        将参数转换为 JSON 兼容字典。

        Returns:
            包含参数名称、种类、注解和默认值的字典。
        """
        return {
            "name": self.name,
            "kind": self.kind,
            "annotation": self.annotation,
            "has_default": self.has_default,
            "default": self.default,
        }


@dataclass(slots=True, frozen=True)
class InterfaceSymbol:
    """
    表示某个 Git 快照中的一个 Python 接口符号。

    接口符号覆盖文件、类、函数、全局变量、成员函数和成员变量。
    """

    kind: InterfaceKind
    path: str
    qualname: str
    line: int
    variant: int = 0
    parameters: tuple[InterfaceParameter, ...] = ()
    return_type: str = ""
    annotation: str = ""
    value: str = ""
    storage: str = ""
    decorators: tuple[str, ...] = ()
    bases: tuple[str, ...] = ()
    is_async: bool = False
    exports: tuple[str, ...] = ()
    code_lines: int = 0
    exposure: str = ""

    @property
    def key(self) -> str:
        """
        生成快照内稳定的接口符号键。

        Returns:
            由类型、路径、限定名称和重载序号组成的字符串。
        """
        return f"{self.kind}:{self.path}:{self.qualname}:{self.variant}"

    def comparable(self) -> dict[str, Any]:
        """
        返回用于判断接口是否改变的结构。

        Returns:
            排除源码位置后的接口声明字典。
        """
        return {
            "parameters": [item.to_dict() for item in self.parameters],
            "return_type": self.return_type,
            "annotation": self.annotation,
            "value": self.value,
            "storage": self.storage,
            "decorators": list(self.decorators),
            "bases": list(self.bases),
            "is_async": self.is_async,
            "exports": list(self.exports),
        }

    def to_dict(self) -> dict[str, Any]:
        """
        将接口符号转换为 JSON 兼容字典。

        Returns:
            包含接口声明和源码位置的完整字典。
        """
        return {
            "kind": self.kind,
            "path": self.path,
            "qualname": self.qualname,
            "line": self.line,
            "variant": self.variant,
            "code_lines": self.code_lines,
            "exposure": self.exposure,
            **self.comparable(),
        }


@dataclass(slots=True, frozen=True)
class InterfaceChange:
    """
    表示 Git 基线与当前工作区之间的一条接口变化。

    新增和删除变化只包含单侧符号，修改变化同时保存前后声明及字段差异。
    """

    change: InterfaceChangeType
    kind: InterfaceKind
    path: str
    symbol: str
    before: InterfaceSymbol | None = None
    after: InterfaceSymbol | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def breaking(self) -> bool:
        """
        判断该变化是否可能破坏已有调用方。

        Returns:
            删除或修改接口时返回 True，纯新增时返回 False。
        """
        return self.change in {"removed", "modified"}

    def to_dict(self) -> dict[str, Any]:
        """
        将接口变化转换为 JSON 兼容字典。

        Returns:
            包含变化类型、前后声明、差异详情和破坏性标记的字典。
        """
        return {
            "change": self.change,
            "kind": self.kind,
            "path": self.path,
            "symbol": self.symbol,
            "breaking": self.breaking,
            "before": self.before.to_dict() if self.before is not None else None,
            "after": self.after.to_dict() if self.after is not None else None,
            "details": self.details,
        }


@dataclass(slots=True, frozen=True)
class InterfaceDiffReport:
    """
    汇总两个 Git 快照之间的 Python 接口变化。

    默认比较 HEAD 提交和当前工作区，未暂存修改也会进入接口差异。
    """

    base: str
    target: str
    changes: tuple[InterfaceChange, ...]
    base_files: int
    target_files: int
    errors: tuple[str, ...] = ()
    contract_findings: tuple[Finding, ...] = ()
    api_scope: str = "all"
    private_min_lines: int = 20

    @property
    def has_changes(self) -> bool:
        """
        判断报告中是否存在接口变化。

        Returns:
            至少存在一条接口变化时返回 True。
        """
        return bool(self.changes)

    def summary(self) -> dict[str, Any]:
        """
        按变化类型和接口种类汇总数量。

        Returns:
            包含总数、破坏性数量及二维分类计数的字典。
        """
        by_change = dict.fromkeys(("added", "removed", "modified"), 0)
        by_kind = dict.fromkeys(
            (
                "file",
                "class",
                "function",
                "global_variable",
                "method",
                "member_variable",
            ),
            0,
        )
        for item in self.changes:
            by_change[item.change] += 1
            by_kind[item.kind] += 1
        return {
            "total": len(self.changes),
            "breaking": sum(item.breaking for item in self.changes),
            "by_change": by_change,
            "by_kind": by_kind,
        }

    def to_dict(self) -> dict[str, Any]:
        """
        将接口差异报告转换为 JSON 兼容字典。

        Returns:
            包含快照来源、汇总、错误和逐条变化的字典。
        """
        return {
            "base": self.base,
            "target": self.target,
            "base_files": self.base_files,
            "target_files": self.target_files,
            "summary": self.summary(),
            "errors": list(self.errors),
            "api_scope": self.api_scope,
            "private_min_lines": self.private_min_lines,
            "contract_findings": [item.to_dict() for item in self.contract_findings],
            "changes": [item.to_dict() for item in self.changes],
        }


@dataclass(slots=True)
class QualityBaseline:
    """保存指定 Git 基线的静态质量摘要。

    该摘要只保留严重级别、规则和文件维度计数，避免在最终报告中
    重复保存整份基线发现明细。
    """

    revision: str
    files_scanned: int
    definitions: int
    documented_definitions: int
    complete_docstrings: int
    docstring_definitions: int
    summary: dict[str, int]
    by_rule: dict[str, dict[str, int]]
    by_file: dict[str, dict[str, int]]
    finding_severities: dict[str, Severity] = field(default_factory=dict)
    semantic_review_fingerprints: dict[str, int] = field(default_factory=dict)

    @property
    def docstring_coverage(self) -> float:
        """返回基线 docstring 覆盖率。

        Returns:
            百分比数值；没有可统计定义时返回 100.0。
        """
        if self.docstring_definitions == 0:
            return 100.0
        return round(self.documented_definitions * 100 / self.docstring_definitions, 2)

    @property
    def complete_docstring_coverage(self) -> float:
        """返回基线完整 docstring 覆盖率。

        Returns:
            百分比数值；没有可统计定义时返回 100.0。
        """
        if self.docstring_definitions == 0:
            return 100.0
        return round(self.complete_docstrings * 100 / self.docstring_definitions, 2)

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 兼容结构。

        Returns:
            基线质量摘要字典。
        """
        return {
            "revision": self.revision,
            "files_scanned": self.files_scanned,
            "definitions": self.definitions,
            "docstrings": {
                "eligible": self.docstring_definitions,
                "documented": self.documented_definitions,
                "complete": self.complete_docstrings,
                "coverage_percent": self.docstring_coverage,
                "complete_coverage_percent": self.complete_docstring_coverage,
            },
            "summary": dict(self.summary),
            "by_rule": self.by_rule,
            "by_file": self.by_file,
            "finding_severities": dict(self.finding_severities),
            "semantic_review_fingerprints": dict(self.semantic_review_fingerprints),
        }


@dataclass(slots=True)
class ScanReport:
    """
    汇总一次仓库扫描的结果与 docstring 覆盖率。

    报告既可直接渲染为文本，也可序列化为 JSON 供 CI 或大模型消费。
    """

    root: Path
    files_scanned: int
    findings: list[Finding]
    definitions: int
    documented_definitions: int = 0
    complete_docstrings: int = 0
    docstring_definitions: int = 0
    interface_diff: InterfaceDiffReport | None = None
    scope_notes: tuple[str, ...] = ()
    focus_paths: tuple[str, ...] = ()
    project_name: str = ""
    profile_source: str = ""
    profile_capabilities: tuple[str, ...] = ()
    profile_metadata: dict[str, str] = field(default_factory=dict)
    baseline: QualityBaseline | None = None
    baseline_error: str = ""
    comparison_mode: str = ""
    comparison_target: str = "WORKTREE"
    quality_count_deltas: dict[str, int] = field(default_factory=dict)
    test_findings: list[Finding] = field(default_factory=list)
    test_files_scanned: int = 0
    test_definitions: int = 0
    test_baseline: QualityBaseline | None = None
    relation_graph: dict[str, Any] = field(default_factory=dict)
    multilang_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def docstring_coverage(self) -> float:
        """
        计算存在 docstring 的定义占比。

        Returns:
            百分比数值；没有定义时返回 100.0。
        """
        if self.docstring_definitions == 0:
            return 100.0
        return round(self.documented_definitions * 100 / self.docstring_definitions, 2)

    @property
    def complete_docstring_coverage(self) -> float:
        """
        计算满足完整结构规范的定义占比。

        Returns:
            百分比数值；没有定义时返回 100.0。
        """
        if self.docstring_definitions == 0:
            return 100.0
        return round(self.complete_docstrings * 100 / self.docstring_definitions, 2)

    def rule_summary(self) -> list[dict[str, Any]]:
        """
        按规则汇总发现数量、级别和置信度。

        Returns:
            由规则编号、数量及分级计数组成的列表。
        """
        rows: dict[str, dict[str, Any]] = {}
        for finding in self.findings:
            if finding.code not in rows:
                rows[finding.code] = {
                    "code": finding.code,
                    "count": 0,
                    "severity": {"info": 0, "warning": 0, "error": 0, "critical": 0},
                    "confidence": {"low": 0, "medium": 0, "high": 0},
                }
            row = rows[finding.code]
            row["count"] += 1
            row["severity"][finding.severity] += 1
            row["confidence"][finding.confidence] += 1
        return sorted(rows.values(), key=lambda row: (-row["count"], row["code"]))

    def file_hotspots(self) -> list[dict[str, Any]]:
        """
        按文件汇总发现数量和严重级别。

        Returns:
            按问题总数降序排列的文件热点列表。
        """
        rows: dict[str, dict[str, Any]] = {}
        for finding in self.findings:
            if finding.path not in rows:
                rows[finding.path] = {
                    "path": finding.path,
                    "count": 0,
                    "info": 0,
                    "warning": 0,
                    "error": 0,
                    "critical": 0,
                }
            row = rows[finding.path]
            row["count"] += 1
            row[finding.severity] += 1
        return sorted(rows.values(), key=lambda row: (-row["count"], row["path"]))

    def to_dict(self) -> dict[str, Any]:
        """
        将扫描报告转换为 JSON 兼容字典。

        Returns:
            包含统计摘要、docstring 覆盖率和问题明细的字典。
        """
        counts = {"info": 0, "warning": 0, "error": 0, "critical": 0}
        for finding in self.findings:
            counts[finding.severity] += 1
        return {
            "root": str(self.root),
            "files_scanned": self.files_scanned,
            "definitions": self.definitions,
            "scope_notes": list(self.scope_notes),
            "focus_paths": list(self.focus_paths),
            "project_name": self.project_name,
            "profile_source": self.profile_source,
            "profile_capabilities": list(self.profile_capabilities),
            "profile_metadata": dict(self.profile_metadata),
            "baseline": self.baseline.to_dict() if self.baseline is not None else None,
            "baseline_error": self.baseline_error,
            "comparison_mode": self.comparison_mode,
            "comparison_target": self.comparison_target,
            "quality_count_deltas": dict(self.quality_count_deltas),
            "relation_graph": dict(self.relation_graph),
            "multilang_summary": dict(self.multilang_summary),
            "test_summary": {
                "files_scanned": self.test_files_scanned,
                "definitions": self.test_definitions,
                "findings": {
                    severity: sum(
                        item.severity == severity for item in self.test_findings
                    )
                    for severity in ("critical", "error", "warning", "info")
                },
                "baseline": self.test_baseline.to_dict()
                if self.test_baseline is not None
                else None,
            },
            "docstrings": {
                "eligible": self.docstring_definitions,
                "documented": self.documented_definitions,
                "complete": self.complete_docstrings,
                "coverage_percent": self.docstring_coverage,
                "complete_coverage_percent": self.complete_docstring_coverage,
            },
            "summary": counts,
            "by_rule": self.rule_summary(),
            "hotspots": self.file_hotspots(),
            "findings": [finding.to_dict() for finding in self.findings],
            "interface_diff": (
                self.interface_diff.to_dict()
                if self.interface_diff is not None
                else None
            ),
        }
