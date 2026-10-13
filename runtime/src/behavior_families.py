"""Group emitted QG findings by observable contract-failure families.

This module consumes *official* scan findings. It neither infers missing
contracts from names nor creates additional findings or semantic obligations.
Its labels describe the evidence class, NOT manual bug adjudication.
"""

from __future__ import annotations

from collections import defaultdict

from .model import Finding

# Multiple Core QG codes may represent one behavior family. Private Profile
# rules deliberately remain external and require explicit evidence contracts.
# A finding has
# exactly one family owner here, so a sample cannot be counted twice.
_FAMILY_RULES: tuple[tuple[str, frozenset[str]], ...] = (
    ("映射字段及缺省消费", frozenset({"QG003", "QG004", "QG157"})),
    ("异常吞没与降级返回", frozenset({"QG007", "QG214", "QG215"})),
    ("生产者与类型契约", frozenset({"QG209", "QG210", "QG211", "QG216"})),
    ("副作用及结果替代", frozenset({"QG213"})),
    ("原生编译及覆盖证明", frozenset({"QG208", "QG212"})),
)

# A source-bound observation can be exact without proving that the application's
# requirements forbid the observed behavior. Only codes with an explicitly
# verified static contract violation may occupy the first evidence class.
_PROVEN_CONTRACT = frozenset({"QG209", "QG211", "QG216", "QG212"})
_CONDITIONAL = frozenset({"QG157", "QG210", "QG213", "QG214", "QG215", "QG208"})


def family_rows(
    findings: tuple[Finding, ...] | list[Finding],
) -> list[tuple[str, int, int, int, int, int, str]]:
    """Summarize source-backed findings as proof, conditional, and unknown.

    Every counted item must come from the real scanner. No UNKNOWN is treated
    as a defect. 'Proven contract' means source-contract evidence, not that a
    production incident occurred. Results are deterministic across runs.
    """
    grouped: dict[str, list[Finding]] = defaultdict(list)
    owner = {code: family for family, codes in _FAMILY_RULES for code in codes}
    for item in findings:
        family = owner.get(item.code)
        if family is not None:
            grouped[family].append(item)
    rows = []
    for family, _ in _FAMILY_RULES:
        items = sorted(
            grouped[family], key=lambda x: (x.path, x.line, x.column, x.code)
        )
        samples = []
        seen_paths = set()
        for item in items:
            if item.path not in seen_paths:
                samples.append(f"{item.code}@{item.path}:{item.line}")
                seen_paths.add(item.path)
            if len(samples) == 3:
                break
        rows.append(
            (
                family,
                len(items),
                sum(item.code in _PROVEN_CONTRACT for item in items),
                sum(item.code in _CONDITIONAL for item in items),
                sum(item.code not in _PROVEN_CONTRACT | _CONDITIONAL for item in items),
                len({(item.path, item.line, item.column, item.code) for item in items}),
                "; ".join(samples) or "—",
            )
        )
    return rows
