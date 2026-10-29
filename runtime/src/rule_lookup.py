"""按规则编号查询唯一 RULES.md 索引；不执行质量审计。"""

from __future__ import annotations

import re
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parents[2] / "references" / "RULES.md"
RULE_INPUT = re.compile(r"-?(QG\d{3}|RUF:FORMAT)\Z", re.IGNORECASE)
RULE_ROW = re.compile(r"^\| `(QG\d{3}|RUF:FORMAT)` \| (.+?) \| (.+?) \| (.+?) \|$")


def requested_codes(args: list[str]) -> list[str]:
    """解析去重的有序规则编号；拒绝非法输入。

    Args:
        args: 规则编号参数。

    Returns:
        有序且唯一的规则编号。
    """
    if not args:
        raise ValueError(
            "用法: python scripts/quality_guard.py evidence --rule QG123 --rule QG153"
        )
    codes: list[str] = []
    for argument in args:
        match = RULE_INPUT.fullmatch(argument)
        if match is None:
            raise ValueError(f"无效规则编号: {argument!r}；格式为 -QG123")
        code = match.group(1).upper()
        if code not in codes:
            codes.append(code)
    return codes


def render_rule_cards(codes: list[str], rules_text: str) -> str:
    """以单行规则事实生成查询结果，不合并规则的修复策略。

    Args:
        codes: 请求的规则编号。
        rules_text: 权威规则索引原文。

    Returns:
        各规则独立的问题、正确修复和禁止做法及源码行号。

    Raises:
        ValueError: 索引缺项、重复或单行合同不完整。
    """
    definitions: dict[str, tuple[int, str, str, str]] = {}
    for number, line in enumerate(rules_text.splitlines(), 1):
        if not line.startswith("| `QG") and not line.startswith("| `RUF:FORMAT`"):
            continue
        matched = RULE_ROW.fullmatch(line)
        if matched is None:
            raise ValueError(f"RULES.md:{number} 规则必须独占一行且填写三项说明")
        code, description, recommended, forbidden = matched.groups()
        if code in definitions:
            raise ValueError(f"RULES.md 存在重复规则定义: {code}")
        definitions[code] = (number, description, recommended, forbidden)

    missing = [code for code in codes if code not in definitions]
    if missing:
        raise ValueError("RULES.md 未定义规则: " + ", ".join(missing))

    output = [
        "# QG 规则修复卡",
        "",
        "规则来源：`references/RULES.md`，仅为静态规则定义；真实审计结论仍需源码与契约证据。",
        "",
    ]
    for code in codes:
        number, description, recommended, forbidden = definitions[code]
        output.extend(
            [
                f"## {code}",
                "",
                f"**检查内容**（`RULES.md:{number}`）：{description}",
                f"**正确修复**：{recommended}",
                f"**禁止做法**：{forbidden}",
                "",
            ]
        )
    return "\n".join(output).rstrip() + "\n"
