"""离线批量查询 RULES.md 中的规则定义与修复方向；不执行质量审计。"""

from __future__ import annotations

import re
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parents[2] / "references" / "RULES.md"
RULE_INPUT = re.compile(r"-?(QG\d{3}|RUF:FORMAT)\Z", re.IGNORECASE)
RULE_ROW = re.compile(r"^\| `(QG\d{3}|RUF:FORMAT)` \| (.*) \|$")
RULE_SECTION = re.compile(r"^### (QG\d{3}(?: / QG\d{3})*) · (.+)$")
RULE_SPAN = re.compile(r"`QG(\d{3})`(?:[–-]`QG(\d{3})`)?")


def requested_codes(args: list[str]) -> list[str]:
    """解析有序且去重的规则编号，拒绝未知 CLI 形式。

    Args:
        args: 用户输入的编号列表，形如 QG003 或 -QG003。

    Returns:
        只包含有效唯一规则编号的字符串列表。
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
    # The source rulebook contains both indexed table entries and authoritative
    # stand-alone source-bound rule chapters (e.g. QG210/QG213). Treat both as
    # definitions, without inferring rule existence from loose text mentions.
    chapters: dict[str, tuple[int, str, str]] = {}
    for index, line in enumerate(lines):
        chapter_match = RULE_SECTION.fullmatch(line)
        if chapter_match is None:
            continue
        end_index = index + 1
        while end_index < len(lines) and not lines[end_index].startswith(
            ("## ", "### ")
        ):
            end_index += 1
        content = "\n".join(lines[index + 1 : end_index]).strip()
        if not content:
            raise ValueError(f"RULES.md 规则章节没有契约正文: {line}")
        for code in chapter_match.group(1).split(" / "):
            if code in chapters:
                raise ValueError(f"RULES.md 存在重复规则章节: {code}")
            chapters[code] = (index + 1, chapter_match.group(2), content)
            # Indexed table definitions take precedence; standalone chapters
            # supply an identity only for genuinely unindexed rules.
            if code not in definitions:
                definitions[code] = (index + 1, chapter_match.group(2))
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
        if code in chapters:
            chapter_line, chapter_title, chapter_content = chapters[code]
            output.extend(
                [
                    "",
                    f"**专项契约原文**（`RULES.md:{chapter_line}`）：{chapter_title}",
                    "",
                    chapter_content,
                ]
            )
        for heading_line, heading in enumerate(lines, 1):
            if not heading.startswith("#### QG"):
                continue
            label = heading.split("·", 1)[0]
            tagged = set(re.findall(r"QG\d{3}", label))
            if code not in tagged:
                continue
            detail: list[str] = []
            for following in lines[heading_line:]:
                if following.startswith("#"):
                    break
                detail.append(following)
            output.extend(
                [
                    "",
                    f"**业务 Cases 裁决**（`RULES.md:{heading_line}`）：{heading}",
                    "",
                    "\n".join(detail).strip(),
                ]
            )
    return "\n".join(output) + "\n"
