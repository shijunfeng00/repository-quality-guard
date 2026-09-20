"""把静态 findings 路由到固定语义审判问题，而不改变严重等级语义。"""

from __future__ import annotations

import json
from dataclasses import replace

from .model import Finding

_FALLBACK_CODES = frozenset(
    {"QG003", "QG004", "QG005", "QG006", "QG025", "QG062", "QG066", "QG103"}
)
_REVIEW_ROUTES = {
    "QG020": ("Q6", "suppression"),
    "QG128": ("Q4,Q5", "dependency-surface"),
    "QG144": ("Q2,Q6", "return-contract"),
    "QG178": ("Q10", "heuristic"),
    "QG191": ("Q3,Q5", "manual-protocol-construction"),
    "QG193": ("Q11", "affected-test-mutated"),
    "QG194": ("Q2,Q11", "interface-test-coverage-unknown"),
    "QG196": ("Q4,Q5,Q9", "giant-owner-worsening"),
    "QG197": ("Q3,Q4,Q5,Q9", "nested-helper-laundering"),
    "QG203": ("Q1,Q2,Q6,Q11", "release-identity-coupling-high-signal"),
    "QG205": ("Q1,Q2,Q6,Q11", "release-identity-coupling"),
}


def annotate_semantic_review(findings: list[Finding]) -> list[Finding]:
    """为需要语义裁决的 finding 写入稳定 evidence 路由。

    Args:
        findings: 当前生产扫描结果。

    Returns:
        保持 finding 位置与 severity 不变、仅补充 review evidence 的新列表。
    """
    annotated: list[Finding] = []
    for finding in findings:
        if finding.code in _REVIEW_ROUTES:
            route = _REVIEW_ROUTES[finding.code]
        elif finding.code in _FALLBACK_CODES:
            route = ("Q6", "fallback-contract")
        else:
            annotated.append(finding)
            continue
        evidence = dict(finding.evidence)
        evidence["semantic_review_required"] = True
        evidence["semantic_review_question"] = route[0]
        evidence["semantic_review_kind"] = route[1]
        annotated.append(replace(finding, evidence=evidence))
    return annotated


def semantic_review_candidates(findings: list[Finding]) -> list[Finding]:
    """返回已经由静态规则标记为需要语义裁决的 findings。

    Args:
        findings: 当前生产扫描结果。

    Returns:
        按路径、行号和规则稳定排序的候选列表。
    """
    return sorted(
        (
            finding
            for finding in findings
            if "semantic_review_required" in finding.evidence
            and finding.evidence["semantic_review_required"] is True
        ),
        key=lambda item: (item.path, item.line, item.code, item.symbol),
    )


def semantic_review_key(finding: Finding) -> str:
    """生成跨行号移动稳定、允许重复计数的语义候选身份键。

    Args:
        finding: 已标记语义审查路由的 finding。

    Returns:
        由路径、规则、owner、消息语义和规则特征组成的稳定字符串。
    """
    evidence = finding.evidence
    relevant = {
        key: evidence[key]
        for key in (
            "semantic_review_kind",
            "receiver",
            "explicit_default",
            "mechanism",
            "detail",
            "text",
            "parameter",
            "accessed_fields",
            "shapes",
            "target",
            "relation_direct_caller_count",
            "relation_callee_count",
            "relation_affected_test_count",
            "test_change",
            "affected_by_count",
            "interface_count",
            "max_direct_caller_count",
            "max_transitive_dependent_count",
        )
        if key in evidence
    }
    if finding.code in {"QG203", "QG205"}:
        if "token" in evidence:
            relevant["identity_tokens"] = [str(evidence["token"])]
        elif "tokens" in evidence:
            relevant["identity_tokens"] = [str(item) for item in evidence["tokens"]]
        elif "matches" in evidence:
            relevant["identity_tokens"] = [
                {
                    "kind": str(item["kind"]) if "kind" in item else "",
                    "token": str(item["token"]) if "token" in item else "",
                }
                for item in evidence["matches"]
                if isinstance(item, dict)
            ]
    payload = {
        "path": finding.path,
        "code": finding.code,
        "symbol": finding.symbol,
        "message": " ".join(finding.message.split()),
        "evidence": relevant,
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
