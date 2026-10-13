"""离线批量查询 RULES.md 中的规则定义与修复方向；不执行质量审计。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parents[1] / "references" / "RULES.md"
RULE_INPUT = re.compile(r"-?(QG\d{3}|RUF:FORMAT)\Z", re.IGNORECASE)
RULE_ROW = re.compile(r"^\| `(QG\d{3}|RUF:FORMAT)` \| (.*) \|$")
RULE_SPAN = re.compile(r"`QG(\d{3})`(?:[–-]`QG(\d{3})`)?")


def _requested_codes(args: list[str]) -> list[str]:
    """解析一组编号；拒绝未知命令形式，不把输入解释成路径或查询。"""
    if not args:
        raise ValueError(
            "用法: python scripts/rule_lookup.py -QG123 -QG153 [更多规则编号]"
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
    """从唯一权威规则字典生成修复卡，不为缺失的规则编造建议。

    Args:
        codes: 已解析去重且保持用户输入顺序的规则编号。
        rules_text: 完整的 references/RULES.md 原文。

    Returns:
        包含真实规则来源、行号、全局修复原则与对应规则族的 Markdown。

    Raises:
        ValueError: 规则不存在、重复声明或修复章节结构非法。
    """
    lines = rules_text.splitlines()
    section_start = "## 修复方向契约"
    section_end = "### 常见规则族修复卡"
    if section_start not in lines or section_end not in lines:
        raise ValueError("RULES.md 缺少权威修复方向契约或规则族边界")
    start = lines.index(section_start)
    end = lines.index(section_end)
    if end <= start:
        raise ValueError("RULES.md 修复方向章节顺序错误")
    global_contract = "\n".join(lines[start:end]).strip()
    definitions: dict[str, tuple[int, str]] = {}
    families: list[tuple[int, list[str]]] = []
    for line_number, line in enumerate(lines, 1):
        matched = RULE_ROW.fullmatch(line)
        if matched is not None:
            code = matched.group(1)
            if code in definitions:
                raise ValueError(f"RULES.md 存在重复规则定义: {code}")
            definitions[code] = (line_number, matched.group(2))
        if line.startswith("| **") and line.endswith(" |"):
            columns = line[2:-2].split(" | ")
            if len(columns) == 4:
                families.append((line_number, columns))
    missing = [code for code in codes if code not in definitions]
    if missing:
        raise ValueError("RULES.md 未定义规则: " + ", ".join(missing))

    output = [
        "# QG 规则修复卡",
        "",
        "规则来源：`references/RULES.md`。以下为原始规则字典投影，不代表扫描结果一定正确。",
        "",
        "不要因为扫描器给出 CRITICAL 就放弃核验源码；规则与真实契约冲突时应登记规则缺陷。",
        "",
        global_contract,
    ]
    for code in codes:
        number, description = definitions[code]
        output.extend(
            [
                "",
                f"## {code}",
                "",
                f"**规则定义**（`RULES.md:{number}`）：{description}",
            ]
        )
        selected = 0
        for family_number, columns in families:
            family_description = columns[0]
            if code.startswith("QG"):
                target_number = int(code[2:])
                matches = any(
                    int(span.group(1))
                    <= target_number
                    <= int(span.group(2) or span.group(1))
                    for span in RULE_SPAN.finditer(family_description)
                )
            else:
                matches = f"`{code}`" in family_description
            if not matches:
                continue
            selected += 1
            title, issue, recommended, forbidden = columns
            output.extend(
                [
                    "",
                    f"**修复方向**（`RULES.md:{family_number}`）：{title}",
                    "",
                    f"- 真实风险：{issue}",
                    f"- 正确修复：{recommended}",
                    f"- 禁止做法：{forbidden}",
                ]
            )
        if not selected:
            output.extend(
                ["", "未找到专用修复卡；仍必须执行 `RULES.md` 的全局《修复方向契约》。"]
            )
    return "\n".join(output) + "\n"


def main(args: list[str] | None = None) -> int:
    """执行离线规则查询，成功只输出 Markdown，失败拒绝部分成功。

    Args:
        args: 命令行中的规则编号；None 时使用进程参数。

    Returns:
        成功或显示帮助为 0；输入和规则字典错误为 2。
    """
    raw_args = sys.argv[1:] if args is None else args
    if raw_args in (["--help"], ["-h"]):
        print("用法: python scripts/rule_lookup.py -QG123 -QG153 [更多规则编号]")
        return 0
    try:
        requested = _requested_codes(raw_args)
        markdown = render_rule_cards(requested, RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[QG rules] {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
