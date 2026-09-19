from __future__ import annotations

import ast
import re

from .ast_utils import dotted_name
from .config import GuardConfig
from .facts import ModuleFacts
from .model import Finding
from .policy_common import (
    CONFIG_CALLS,
    CONFIG_FILE_CALLS,
    GENERIC_FUNCTION_NAMES,
    PROMPT_NAME_MARKERS,
    PROMPT_NEGATIVE_MARKERS,
    ParsedModule,
    RepositorySignals,
    call_name,
    is_boundary_module,
    is_compatibility_name,
    make_finding,
)

MIN_CONFIG_SOURCES_FOR_FALLBACK = 2
MIN_POSITIONAL_ARGS_WITH_DEFAULT = 2
PROCESS_ENVIRONMENT_KEYS = {"HOME", "PATH", "PWD", "SHELL", "TEMP", "TMP", "TMPDIR", "USER"}
DEPLOYMENT_CONFIG_MARKERS = (
    "BASE_URL",
    "DATABASE",
    "DB_",
    "ENDPOINT",
    "HOST",
    "MODEL",
    "PASSWORD",
    "PORT",
    "SECRET",
    "SERVER",
    "TOKEN",
    "URL",
)


def _collect_module_constant_signals(
    parsed: ParsedModule,
    signals: RepositorySignals,
) -> None:
    """收集模块级常量定义及全仓静态引用证据。

    Args:
        parsed: 当前已解析模块。
        signals: 仓库级共享信号。

    Returns:
        None。
    """
    for statement in parsed.tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            for name in assignment_names(statement):
                if name.isupper() and not (name.startswith("__") and name.endswith("__")):
                    signals.module_constants[name].append(
                        (parsed.facts.path, statement.lineno, parsed.facts.module)
                    )
    for node in parsed.nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            signals.referenced_names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            signals.referenced_names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            signals.referenced_names.update(alias.name for alias in node.names if alias.name != "*")
    for statement in parsed.tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        if "__all__" not in assignment_names(statement):
            continue
        value = statement.value
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            signals.referenced_names.update(
                element.value
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )


def check_module_rules(
    parsed: ParsedModule,
    config: GuardConfig,
    signals: RepositorySignals,
) -> list[Finding]:
    """
    检查配置读取、Prompt、模块状态和兼容包袱。

    Args:
        parsed: 当前已解析模块。
        config: 当前仓库质量配置。
        signals: 跨模块信号汇总对象。

    Returns:
        模块级发现列表。
    """
    findings: list[Finding] = []
    _collect_module_constant_signals(parsed, signals)
    findings.extend(check_configuration_rules(parsed, config, signals))
    if config.enable_prompt_rules:
        findings.extend(check_prompt_rules(parsed, config, signals))
    findings.extend(check_module_state_rules(parsed, config))
    return findings


def check_configuration_rules(
    parsed: ParsedModule,
    config: GuardConfig,
    signals: RepositorySignals,
) -> list[Finding]:
    """
    检查配置读取位置、默认值和多源回退。

    Args:
        parsed: 当前已解析模块。
        config: 当前仓库质量配置。
        signals: 跨模块信号汇总对象。

    Returns:
        配置相关发现列表。
    """
    facts = parsed.facts
    is_config_module = any(
        marker in facts.module.lower() for marker in config.config_module_markers
    )
    is_config_boundary = is_config_module or is_boundary_module(
        facts.module, config.boundary_module_markers
    )
    findings: list[Finding] = []
    for item in parsed.nodes:
        findings.extend(check_config_ast_node(facts, item, is_config_boundary, signals))
    return findings


def check_config_ast_node(
    facts: ModuleFacts,
    node: ast.AST,
    is_config_boundary: bool,
    signals: RepositorySignals,
) -> list[Finding]:
    """
    检查单个 AST 节点中的配置质量问题。

    Args:
        facts: 当前模块静态事实。
        node: 待检查节点。
        is_config_boundary: 当前模块是否为配置或外部输入边界。
        signals: 跨模块信号汇总对象。

    Returns:
        当前节点产生的配置发现列表。
    """
    if isinstance(node, ast.Call):
        return check_config_call(facts, node, is_config_boundary, signals)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return check_hardcoded_endpoint(facts, node)
    if (
        isinstance(node, ast.BoolOp)
        and config_source_fallback_count(node) >= MIN_CONFIG_SOURCES_FOR_FALLBACK
    ):
        return [
            make_finding(
                facts,
                node,
                "QG106",
                "同一表达式在多个配置来源之间回退。",
                symbol=facts.module,
                severity="warning",
                suggestion="明确唯一权威来源；迁移逻辑不要永久留在运行时。",
            )
        ]
    return []


def check_config_call(
    facts: ModuleFacts,
    node: ast.Call,
    is_config_boundary: bool,
    signals: RepositorySignals,
) -> list[Finding]:
    """
    检查环境变量及配置文件读取调用。

    Args:
        facts: 当前模块静态事实。
        node: 调用表达式。
        is_config_boundary: 当前模块是否为配置或外部输入边界。
        signals: 跨模块信号汇总对象。

    Returns:
        配置调用发现列表。
    """
    name = call_name(node)
    if name in CONFIG_CALLS:
        return check_environment_read(facts, node, name, is_config_boundary, signals)
    if name in CONFIG_FILE_CALLS and not is_config_boundary:
        return [
            make_finding(
                facts,
                node,
                "QG100",
                f"非配置模块直接调用 `{name}` 读取配置文件。",
                symbol=facts.module,
                severity="warning",
                suggestion="配置应有唯一权威来源，业务模块不要自行读取 YAML/JSON/TOML。",
            )
        ]
    return []


def check_environment_read(
    facts: ModuleFacts,
    node: ast.Call,
    name: str,
    is_config_boundary: bool,
    signals: RepositorySignals,
) -> list[Finding]:
    """
    检查单次环境配置读取的键、默认值和所在模块。

    Args:
        facts: 当前模块静态事实。
        node: 配置读取调用。
        name: 调用目标名称。
        is_config_boundary: 当前模块是否为配置或外部输入边界。
        signals: 跨模块信号汇总对象。

    Returns:
        环境配置读取发现列表。
    """
    key = (
        node.args[0].value
        if node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        else "<dynamic>"
    )
    if key in PROCESS_ENVIRONMENT_KEYS:
        return []
    signals.env_reads[key].append((facts.path, node.lineno, name))
    findings: list[Finding] = []
    default = config_default_value(node)
    if default is not None:
        signals.config_defaults[(key, default)].append((facts.path, node.lineno))
        findings.append(
            make_finding(
                facts,
                node,
                "QG103",
                f"读取配置 `{key}` 时提供默认值 {default}。",
                symbol=facts.module,
                severity=configuration_default_severity(key),
                confidence="high" if configuration_default_severity(key) == "warning" else "medium",
                suggestion="必填配置缺失时直接失败；真实可选配置只在唯一配置模型中声明默认值。",
            )
        )
    if key == "<dynamic>":
        findings.append(
            make_finding(
                facts,
                node,
                "QG104",
                "配置键由动态表达式生成，无法静态确认权威来源。",
                symbol=facts.module,
                severity="warning",
                suggestion="配置键使用稳定常量并集中声明。",
            )
        )
    if not is_config_boundary:
        findings.append(
            make_finding(
                facts,
                node,
                "QG100",
                f"非配置模块直接读取 `{name}({key!r})`。",
                symbol=facts.module,
                severity="warning",
                suggestion="仅在唯一配置模块读取、转换和校验，消费方接收正式配置对象。",
            )
        )
    return findings


def configuration_default_severity(key: str) -> str:
    """
    根据配置键是否影响部署连接或鉴权决定默认值告警级别。

    Args:
        key: 环境变量或配置键。

    Returns:
        部署关键配置返回 warning，普通行为参数返回 info。
    """
    upper = key.upper()
    return "warning" if any(marker in upper for marker in DEPLOYMENT_CONFIG_MARKERS) else "info"


def check_hardcoded_endpoint(facts: ModuleFacts, node: ast.Constant) -> list[Finding]:
    """
    检查硬编码服务地址或端口。

    Args:
        facts: 当前模块静态事实。
        node: 字符串常量节点。

    Returns:
        服务端点发现列表。
    """
    if not looks_like_service_endpoint(node.value):
        return []
    return [
        make_finding(
            facts,
            node,
            "QG102",
            f"源码中硬编码服务地址或端口：{node.value!r}。",
            symbol=facts.module,
            severity="warning",
            suggestion="服务地址进入唯一配置对象。",
        )
    ]


def constant_string(node: ast.expr) -> str:
    """
    提取字符串常量。

    Args:
        node: 待解析表达式。

    Returns:
        字符串值；无法静态确定时返回 `<dynamic>`。
    """
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else "<dynamic>"
    )


def config_default_value(node: ast.Call) -> str | None:
    """
    提取配置读取调用中的默认值。

    Args:
        node: 配置读取调用。

    Returns:
        默认值的源码文本；未提供默认值时返回 None。
    """
    if len(node.args) >= MIN_POSITIONAL_ARGS_WITH_DEFAULT:
        return ast.unparse(node.args[1])
    default_keyword = next(
        (keyword.value for keyword in node.keywords if keyword.arg == "default"), None
    )
    return ast.unparse(default_keyword) if default_keyword is not None else None


def config_source_fallback_count(node: ast.BoolOp) -> int:
    """
    统计 `or` 表达式中配置来源数量。

    Args:
        node: 布尔表达式。

    Returns:
        环境变量、配置对象或配置文件读取调用数量。
    """
    if not isinstance(node.op, ast.Or):
        return 0
    count = 0
    for value in node.values:
        if (
            (isinstance(value, ast.Call) and call_name(value) in CONFIG_CALLS | CONFIG_FILE_CALLS)
            or "config" in dotted_name(value).lower()
            or "settings" in dotted_name(value).lower()
        ):
            count += 1
    return count


def looks_like_service_endpoint(value: str) -> bool:
    """
    判断字符串是否像硬编码服务端点。

    Args:
        value: 待判断字符串。

    Returns:
        命中 HTTP 地址、IP:port 或 localhost:port 时返回 True。
    """
    if value.startswith(("http://", "https://")):
        remainder = value.split("://", 1)[1]
        return bool(remainder) and "example.com" not in value
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}", value):
        return True
    return bool(re.fullmatch(r"(?:localhost|127\.0\.0\.1):\d{2,5}", value))


def check_prompt_rules(
    parsed: ParsedModule,
    config: GuardConfig,
    signals: RepositorySignals,
) -> list[Finding]:
    """
    检查 Prompt 负面约束、碎片拼接和案例枚举。

    Args:
        parsed: 当前已解析模块。
        config: 当前仓库质量配置。
        signals: 跨模块信号汇总对象。

    Returns:
        Prompt 结构发现列表。
    """
    facts = parsed.facts
    findings: list[Finding] = []
    for statement in parsed.tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        names = assignment_names(statement)
        value = statement.value
        prompt_named = any(
            marker in name.lower() for marker in PROMPT_NAME_MARKERS for name in names
        )
        is_prompt_template = isinstance(value, (ast.Constant, ast.BinOp, ast.JoinedStr))
        if prompt_named and is_prompt_template:
            text = collect_string_text(value)
            negative_count = sum(text.count(marker) for marker in PROMPT_NEGATIVE_MARKERS)
            if negative_count >= config.prompt_constraint_threshold:
                findings.append(
                    make_finding(
                        facts,
                        statement,
                        "QG110",
                        f"Prompt 中累计 {negative_count} 个禁止/必须类约束。",
                        symbol=facts.module,
                        severity="info",
                        suggestion="把稳定约束下沉到 schema、状态契约和工具接口。",
                    )
                )
            fragment_count = count_string_fragments(value)
            if fragment_count >= config.prompt_fragment_threshold:
                findings.append(
                    make_finding(
                        facts,
                        statement,
                        "QG111",
                        f"Prompt 由 {fragment_count} 个字符串碎片拼接。",
                        symbol=facts.module,
                        severity="info",
                        suggestion="集中为少量可直接阅读的模板。",
                    )
                )
            if len(text) >= config.duplicate_prompt_min_chars:
                signals.prompt_literals[text].append(
                    (facts.path, statement.lineno, next(iter(names), ""))
                )
        suspicious_case_table = any(
            marker in name.lower()
            for name in names
            for marker in ("case", "fallback", "pattern", "prompt", "rule", "uncertainty")
        )
        if suspicious_case_table and isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            strings = [
                child.value
                for child in value.elts
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            ]
            if len(strings) >= config.case_literal_threshold:
                findings.append(
                    make_finding(
                        facts,
                        statement,
                        "QG112",
                        f"常量集合枚举 {len(strings)} 个字符串案例，可能是失败样例特判表。",
                        symbol=facts.module,
                        severity="info",
                        suggestion="确认它是稳定领域枚举，而不是 Prompt 或正则补丁。",
                    )
                )
    return findings


def assignment_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    """
    提取模块赋值语句中的目标名称。

    Args:
        node: 普通或注解赋值语句。

    Returns:
        直接名称目标集合。
    """
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {target.id for target in targets if isinstance(target, ast.Name)}


def collect_string_text(node: ast.expr | None) -> str:
    """
    拼接表达式中的全部字符串常量。

    Args:
        node: 待分析表达式。

    Returns:
        按 AST 遍历顺序拼接的字符串文本。
    """
    if node is None:
        return ""
    return "\n".join(
        item.value
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    )


def count_string_fragments(node: ast.expr | None) -> int:
    """
    统计表达式中的字符串常量数量。

    Args:
        node: 待检查表达式。

    Returns:
        字符串常量数量。
    """
    if node is None:
        return 0
    return sum(
        isinstance(item, ast.Constant) and isinstance(item.value, str) for item in ast.walk(node)
    )


def check_module_state_rules(parsed: ParsedModule, config: GuardConfig) -> list[Finding]:
    """
    检查模块级可变状态、动态导入、兼容路径和嵌套定义。

    Args:
        parsed: 当前已解析模块。
        config: 当前仓库质量配置。

    Returns:
        模块结构发现列表。
    """
    findings: list[Finding] = []
    for statement in parsed.tree.body:
        findings.extend(check_module_statement(parsed.facts, statement))
    findings.extend(check_utility_module(parsed.facts, config))
    return findings


def check_module_statement(facts: ModuleFacts, statement: ast.stmt) -> list[Finding]:
    """
    检查单条模块顶层语句。

    Args:
        facts: 当前模块静态事实。
        statement: 模块顶层语句。

    Returns:
        当前语句产生的模块结构发现列表。
    """
    findings = check_module_mutable_state(facts, statement)
    findings.extend(check_module_definition(facts, statement))
    if isinstance(statement, ast.ImportFrom) and any(
        alias.name == "*" for alias in statement.names
    ):
        findings.append(
            make_finding(
                facts,
                statement,
                "QG093",
                "使用 `from ... import *`，符号来源和覆盖关系不透明。",
                symbol=facts.module,
                severity="warning",
                suggestion="显式导入实际使用的名称。",
            )
        )
    return findings


def check_module_mutable_state(facts: ModuleFacts, statement: ast.stmt) -> list[Finding]:
    """
    检查模块顶层可变容器。

    Args:
        facts: 当前模块静态事实。
        statement: 模块顶层语句。

    Returns:
        隐式共享状态发现列表。
    """
    if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
        return []
    if not isinstance(statement.value, (ast.List, ast.Dict, ast.Set)):
        return []
    names = assignment_names(statement)
    if not any(not name.isupper() and not name.startswith("__") for name in names):
        return []
    return [
        make_finding(
            facts,
            statement,
            "QG092",
            "模块级可变容器可能形成隐式共享状态。",
            symbol=facts.module,
            severity="info",
            suggestion="常量使用不可变结构；运行时状态放入具有明确生命周期的对象。",
        )
    ]


def check_module_definition(facts: ModuleFacts, statement: ast.stmt) -> list[Finding]:
    """
    检查顶层定义名称、迁移入口和嵌套定义。

    Args:
        facts: 当前模块静态事实。
        statement: 模块顶层语句。

    Returns:
        定义结构发现列表。
    """
    if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return []
    lowered = statement.name.lower()
    findings: list[Finding] = []
    if is_compatibility_name(lowered):
        findings.append(
            make_finding(
                facts,
                statement,
                "QG094",
                f"定义名 `{statement.name}` 表明存在兼容、旧版或回退路径。",
                symbol=statement.name,
                severity="info",
                suggestion="无真实兼容需求时删除旧入口和别名。",
            )
        )
    if any(marker in lowered for marker in ("migrate", "migration", "backfill")):
        findings.append(
            make_finding(
                facts,
                statement,
                "QG095",
                f"运行时模块包含迁移/回填入口 `{statement.name}`。",
                symbol=statement.name,
                severity="info",
                suggestion="一次性迁移放入独立脚本。",
            )
        )
    if lowered in GENERIC_FUNCTION_NAMES:
        findings.append(
            make_finding(
                facts,
                statement,
                "QG096",
                f"定义名 `{statement.name}` 过于宽泛，无法表达具体职责。",
                symbol=statement.name,
                severity="info",
                suggestion="使用说明输入、动作和产出的领域名称。",
            )
        )
    nested = nested_definitions(statement)
    if nested:
        findings.append(
            make_finding(
                facts,
                nested[0],
                "QG097",
                f"`{statement.name}` 内部定义了 {len(nested)} 个嵌套函数或类。",
                symbol=statement.name,
                severity="info",
                suggestion="确认闭包确有必要；可复用逻辑应提升到明确作用域。",
            )
        )
    return findings


def nested_definitions(statement: ast.stmt) -> list[ast.AST]:
    """
    返回函数内部定义的函数或类。

    Args:
        statement: 模块顶层定义。

    Returns:
        嵌套定义节点列表；类定义不继续检查。
    """
    if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    return [
        item
        for item in ast.walk(statement)
        if item is not statement
        and isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


def check_utility_module(facts: ModuleFacts, config: GuardConfig) -> list[Finding]:
    """
    检查无边界通用工具模块是否持续膨胀。

    Args:
        facts: 当前模块静态事实。
        config: 当前仓库质量配置。

    Returns:
        通用工具箱模块发现列表。
    """
    if facts.path.stem.lower() not in {"utils", "common", "helpers"}:
        return []
    if len(facts.definitions) < config.utility_module_definition_threshold:
        return []
    return [
        Finding(
            code="QG091",
            severity="info",
            confidence="medium",
            path=str(facts.path),
            line=1,
            column=1,
            message=f"通用模块 `{facts.path.name}` 包含 {len(facts.definitions)} 个定义。",
            symbol=facts.module,
            suggestion="按领域职责和依赖方向归位，避免无边界工具箱。",
            evidence={"definitions": len(facts.definitions)},
        )
    ]
