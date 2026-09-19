"""修改报告契约的固定 schema、正则和阈值。

本模块保存固定 schema、正则、阈值及无状态文本辅助逻辑；拆分目的是保持
报告验证器主体规模可审计，同时避免弱化任何既有检查。
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import ScanReport

REPORT_SCHEMA = "repository-quality-guard/v14"
REPORT_FILENAME = "修改说明.md"
REPORT_TITLE = "# 修改说明"
REQUIRED_SECTIONS = (
    "## 1. 审查结论与范围",
    "## 2. 变更事实与新增对象",
    "## 3. 新增文件、类、函数与接口全量披露",
    "## 4. 继承、父类与公共抽象",
    "## 5. 接口与契约 Before / After",
    "## 6. Critical / Error / Warning 与处理",
    "## 7. 验证命令与结果",
    "## 8. 架构与必要性审判",
    "## 9. 剩余风险与合并结论",
    "## 10. 提交 Commit",
)
AGENT_SEMANTIC_HEURISTIC_QUESTION = (
    "Q10. 是否在未获得明确授权时，使用正则、关键词、词表、阈值、硬编码规则或其他启发式逻辑，"
    "替代本应由明确协议、领域算法或语义模型负责的判断？"
)
COMMON_QUESTION_TITLES = (
    "Q1. 本轮可观察行为、失败场景与非目标是否清楚，修改是否只服务于已经证明的需求？",
    "Q2. 接口、参数、返回结构或协议变化是否逐项同步生产方、消费方、README、Prompt、配置与测试？",
    "Q3. 每个 ADD-* 是否先从自动接口目录与 BM25 候选检索既有能力，再核对 HEAD/父类/MRO/兄弟类/公共协作者并完成删除、内联、合并、复用，仍无法再砍？",
    "Q4. 每项职责是否位于唯一正确所有者，是否把调用方、infra、Tool、State、History、Trace 或相邻层职责塞进错误边界？",
    "Q5. 是否存在重复实现、父类公共能力下沉到子类，或多个子类共同能力本应上提到父类/公共协作者？",
    "Q6. 是否存在隐藏控制流、动态属性、默认兜底、契约猜测，或没有 before/after 证据却声称兼容与行为不变？",
    "Q7. 是否删除或弱化注释/docstring，或让实现、契约说明与可验证行为发生偏离？",
    "Q8. 是否破坏缓存、append-only、session/loop/cache_node、事务、并发、锁或资源生命周期顺序？",
    "Q9. Critical、Error、Warning 是否独立核算；新增或恶化项是否逐项处理，存量债务是否在不扩大范围下尽可能削减？",
    AGENT_SEMANTIC_HEURISTIC_QUESTION,
)
VALID_ITEM_STATUSES = frozenset({"FIXED", "JUSTIFIED", "BLOCKING", "NOT_APPLICABLE"})
VALID_FINAL_STATUSES = frozenset({"ACCEPT", "REVIEW_REQUIRED", "REJECT"})
STATUS_RANK = {"REJECT": 0, "REVIEW_REQUIRED": 1, "ACCEPT": 2}
PLACEHOLDER_PATTERNS = (
    "待填写",
    "TODO",
    "TBD",
    "PENDING",
    "自行确认",
    "后续处理",
    "以后优化",
    "其他同理",
    "详见上文",
    "应该没问题",
    "看起来没问题",
)
AUTO_BLOCK = re.compile(
    r"<!-- RQG:AUTO:BEGIN (?P<name>[a-z_]+) -->\n(?P<body>.*?)\n<!-- RQG:AUTO:END (?P=name) -->",
    re.DOTALL,
)
FRONT_MATTER = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)
ADDITION_MANUAL_ROW = re.compile(
    r"^\| (?P<id>ADD-\d{3,}) \| `?(?P<symbol>.*?)`? \| (?P<reason>.*?) \| "
    r"(?P<existing>.*?) \| (?P<alternative>.*?) \| (?P<evidence>.*?) \| "
    r"(?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
ADDED_COUNT_COPY = re.compile(
    r"^- 模型抄写新增数量：函数=(?P<functions>\d+)；变量=(?P<variables>\d+)；类=(?P<classes>\d+)$",
    re.MULTILINE,
)
ARTIFACT_MANUAL_ROW = re.compile(
    r"^\| (?P<id>ARTIFACT-\d{3,}) \| `(?P<path>.*?)` \| (?P<purpose>.*?) \| "
    r"(?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
FILE_MANUAL_ROW = re.compile(
    r"^\| (?P<id>FILE-\d{3,}) \| `(?P<path>.*?)` \| (?P<reason>.*?) \| "
    r"(?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
TEST_FILE_MANUAL_ROW = re.compile(
    r"^\| (?P<id>TEST-FILE-\d{3,}) \| `(?P<path>.*?)` \| (?P<purpose>.*?) \| "
    r"(?P<assertion>.*?) \| (?P<isolation>.*?) \| (?P<risk>.*?) \| "
    r"(?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
TEST_RISK_MANUAL_ROW = re.compile(
    r"^\| (?P<id>TEST-RISK-\d{3,}) \| (?P<reason>.*?) \| "
    r"(?P<verification>.*?) \| (?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
ARCH_MANUAL_ROW = re.compile(
    r"^\| (?P<id>ARCH-\d{3,}) \| (?P<reason>.*?) \| (?P<verification>.*?) \| "
    r"(?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
QUALITY_DELTA_ROW = re.compile(
    r"^\| (?P<id>DELTA-\d{3,}) \| (?P<reason>.*?) \| (?P<alternative>.*?) \| "
    r"(?P<risk>.*?) \| (?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
INTERFACE_REVIEW_ROW = re.compile(
    r"^\| (?P<id>INTERFACE-\d{3,}) \| `?(?P<symbol>.*?)`? \| "
    r"(?P<reason>.*?) \| (?P<compatibility>.*?) \| (?P<evidence>.*?) \| "
    r"(?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
PROTOCOL_REVIEW_ROW = re.compile(
    r"^\| (?P<id>PROTOCOL-\d{3,}) \| `?(?P<symbol>.*?)`? \| "
    r"(?P<fact>.*?) \| (?P<reason>.*?) \| (?P<impact>.*?) \| "
    r"(?P<evidence>.*?) \| (?P<status>[A-Z_]+) \|$",
    re.MULTILINE,
)
VALIDATION_ROW = re.compile(
    r"^\| `(?P<command>.+?)` \| (?P<purpose>.+?) \| (?P<exit>-?\d+) \| (?P<result>.+?) \|$",
    re.MULTILINE,
)
RISK_ROW = re.compile(
    r"^\| (?P<object>.+?) \| (?P<severity>CRITICAL|ERROR|WARNING|NONE) \| "
    r"(?P<reason>.+?) \| (?P<proposal>.+?) \|$",
    re.MULTILINE,
)
COMMIT_BLOCK = re.compile(r"```bash\n(?P<command>git commit -m \".*?\")\n```", re.DOTALL)
CHINESE = re.compile(r"[\u4e00-\u9fff]")
NUMSTAT_FIELDS = 3
MIN_MANUAL_TEXT = 12
MIN_VALIDATION_TEXT = 6
MIN_COMMIT_BULLETS = 2
MIN_SUMMARY_TEXT = 8
MIN_RISK_TEXT = 8
TEST_INTERFACE_PREVIEW = 4
MIN_DEBT_REASON_TEXT = 36
DELIVERY_ARTIFACT_SUFFIXES = (".patch", ".diff", ".log", ".zip", ".tar", ".tgz", ".tar.gz")
MIN_ADDITION_FACT_TEXT = 28
MIN_ADDITION_ALTERNATIVE_TEXT = 36
MIN_ADDITION_VERIFICATION_TEXT = 10
MIN_SUBTRACTION_REVIEW_TEXT = 24
SEVERITY_RANK = {"warning": 1, "error": 2, "critical": 3}
BASELINE_GATE_EXEMPT_CODES = frozenset({"QG161", "QG179", "QG180", "QG181", "QG182"})
INTERFACE_DEFINITION_KINDS = frozenset({"class", "function", "method"})
EVIDENCE_REFERENCE = re.compile(
    r"(?:FILE|ARTIFACT|ADD|ARCH|DELTA|INTERFACE|PROTOCOL|TEST-FILE|TEST-DELTA|TEST-RISK)-\d{3,}|QG\d{3}|无对应自动事实："
)
REQUIRED_ADDITION_REASON_MARKERS = ("失败=", "职责=", "边界=", "绝对必要性=", "不可替代=")
REQUIRED_EXISTING_MARKERS = (
    "接口目录=",
    "查询=",
    "候选=",
    "源码=",
    "HEAD=",
    "父类=",
    "MRO=",
    "兄弟类=",
    "公共能力=",
)
REQUIRED_ALTERNATIVE_MARKERS = ("删除=", "内联=", "合并=", "复用=")
REQUIRED_ADDITION_EVIDENCE_MARKERS = ("调用链=", "验证=", "语义裁决=")
REQUIRED_DELTA_REASON_MARKERS = ("事实=", "语义裁决=")
REQUIRED_DELTA_ALTERNATIVE_MARKERS = ("替代=", "处置=")
REQUIRED_DELTA_RISK_MARKERS = ("风险=", "关闭=")
REQUIRED_DEBT_REASON_MARKERS = ("原因=", "范围=", "最小方案=", "关闭条件=")
REQUIRED_TEST_PURPOSE_MARKERS = ("目的=", "覆盖=")
REQUIRED_TEST_ASSERTION_MARKERS = ("断言=", "弱化=")
REQUIRED_TEST_ISOLATION_MARKERS = ("Mock=", "生产路径=")
REQUIRED_TEST_RISK_MARKERS = ("风险=", "结论=")
REQUIRED_QUESTION_FACT_MARKER = "证据="
REQUIRED_QUESTION_CONCLUSION_MARKERS = ("处理=", "结论=")
QUESTION_BLOCK = re.compile(
    r"^### (?P<title>Q\d+\..+?)\n\n"
    r"- 状态：(?P<status>[A-Z_]+)\n"
    r"- 事实依据：(?P<fact>.+?)\n"
    r"- 处理与结论：(?P<conclusion>.+?)(?=\n\n### Q|\n\n## 9\.)",
    re.MULTILINE | re.DOTALL,
)


def question_titles(report: ScanReport) -> tuple[str, ...]:
    """返回通用审判及当前项目档案强制追加的问题。

    Args:
        report: 当前仓库扫描报告。

    Returns:
        按固定顺序排列的语义审判问题标题。
    """
    return COMMON_QUESTION_TITLES


def escape(value: str) -> str:
    """转义 Markdown 表格单元格。

    Args:
        value: 待写入表格单元格的原始文本。

    Returns:
        已转义竖线和换行的 Markdown 文本。
    """
    return value.replace("|", "\\|").replace("\n", "<br>")


def contains_placeholder(value: str) -> bool:
    """判断人工说明是否仍包含占位措辞。

    Args:
        value: 待验证的人工说明文本。

    Returns:
        忽略反引号符号名后仍命中占位词时返回 True。
    """
    prose = re.sub(r"`[^`]*`", "", value)
    return any(pattern.lower() in prose.lower() for pattern in PLACEHOLDER_PATTERNS)


def front_matter(text: str) -> dict[str, str]:
    """解析修改报告使用的简单 YAML front matter。

    Args:
        text: 完整修改报告文本。

    Returns:
        front matter 中的顶层字符串键值。
    """
    match = FRONT_MATTER.match(text)
    if match is None:
        return {}
    result: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def line_count(path: Path) -> int:
    """统计 UTF-8 文本文件行数。

    Args:
        path: 待统计的文本文件路径。

    Returns:
        文件行数；文件不可读或不是 UTF-8 文本时返回零。
    """
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return 0
