from __future__ import annotations

import ast
from dataclasses import dataclass

from .core_rule_contract import CoreQualityRule
import re

from .config import GuardConfig
from .facts import ModuleFacts
from .model import Finding
from .policy_common import (
    CONFIG_CALLS,
    CONFIG_FILE_CALLS,
    GENERIC_FUNCTION_NAMES,
    ParsedModule,
    RepositorySignals,
    call_name,
    is_boundary_module,
    is_compatibility_name,
)

MIN_CONFIG_SOURCES_FOR_FALLBACK = 2
MIN_POSITIONAL_ARGS_WITH_DEFAULT = 2
PROCESS_ENVIRONMENT_KEYS = {
    "HOME",
    "PATH",
    "PWD",
    "SHELL",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
}
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
                if all(
                    (
                        name.isupper(),
                        not all((name.startswith("__"), name.endswith("__"))),
                    )
                ):
                    signals.module_constants[name].append(
                        (parsed.facts.path, statement.lineno, parsed.facts.module)
                    )
    for node in parsed.nodes:
        loaded_name = ""
        if isinstance(getattr(node, "ctx", None), ast.Load):
            loaded_name = getattr(node, "id", getattr(node, "attr", ""))
        if loaded_name:
            signals.referenced_names.add(loaded_name)
        if isinstance(node, ast.ImportFrom):
            signals.referenced_names.update(
                alias.name for alias in node.names if alias.name != "*"
            )
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
                if all(
                    (isinstance(element, ast.Constant), isinstance(element.value, str))
                )
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
        if isinstance(item, (ast.Call, ast.BoolOp)) or (
            isinstance(item, ast.Constant) and isinstance(item.value, str)
        ):
            findings.extend(
                check_config_ast_node(facts, item, is_config_boundary, signals)
            )
    return findings


def check_config_ast_node(
    facts: ModuleFacts,
    node: ast.AST,
    is_config_boundary: bool,
    signals: RepositorySignals,
) -> list[Finding]:
    """Classify a configuration expression through the appropriate rule owner.

    Calls are checked for configuration-source fallback, string literals for
    hard-coded endpoints, and other AST nodes for fallback-source patterns.
    The boundary flag suppresses findings for intentional configuration entrypoints.

    Args:
        facts: Source-bound module facts used to construct findings.
        node: Expression or statement currently inspected.
        is_config_boundary: Whether the containing module owns configuration IO.
        signals: Repository-wide evidence of configuration sources and references.

    Returns:
        Findings for the matching expression category, without double dispatch."""
    if isinstance(node, ast.Call):
        return check_config_call(facts, node, is_config_boundary, signals)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return check_hardcoded_endpoint(facts, node)
    return ConfigFallbackSourceRule().evaluate(
        ConfigNodeRuleContext(facts, node, is_config_boundary, signals)
    )


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
    if name in CONFIG_FILE_CALLS:
        return AuthoritativeConfigModuleRule().evaluate(
            ConfigCallContext(facts, node, name, "", None, is_config_boundary)
        )
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
    default = config_default_value(node)
    if default is not None:
        signals.config_defaults[(key, default)].append((facts.path, node.lineno))
    context = ConfigCallContext(facts, node, name, key, default, is_config_boundary)
    findings = ConfigDefaultRule().evaluate(context)
    findings.extend(DynamicConfigKeyRule().evaluate(context))
    findings.extend(AuthoritativeConfigModuleRule().evaluate(context))
    return findings


@dataclass(frozen=True)
class ConfigCallContext:
    """One source-bound config read; repository signals were collected once.

    Fields: facts, node, name, key, default, is_config_boundary."""

    facts: ModuleFacts
    node: ast.Call
    name: str
    key: str
    default: str | None
    is_config_boundary: bool


class ConfigDefaultRule(CoreQualityRule[ConfigCallContext]):
    """QG103: a config read has an explicit fallback value.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG103"

    def evaluate(self, context: ConfigCallContext) -> list[Finding]:
        """Evaluate QG103 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.default is None:
            return []
        facts, node, key, default = (
            context.facts,
            context.node,
            context.key,
            context.default,
        )
        severity = configuration_default_severity(key)
        return [
            facts.make_finding(
                node,
                "QG103",
                f"读取配置 `{key}` 时提供默认值 {default}。",
                symbol=facts.module,
                severity=severity,
                confidence="high" if severity == "warning" else "medium",
                suggestion="必填配置缺失时直接失败；真实可选配置只在唯一配置模型中声明默认值。",
            )
        ]


class DynamicConfigKeyRule(CoreQualityRule[ConfigCallContext]):
    """QG104: config source key is not statically recovered.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG104"

    def evaluate(self, context: ConfigCallContext) -> list[Finding]:
        """Evaluate QG104 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.key != "<dynamic>":
            return []
        return [
            context.facts.make_finding(
                context.node,
                "QG104",
                "配置键由动态表达式生成，无法静态确认权威来源。",
                symbol=context.facts.module,
                severity="warning",
                suggestion="配置键使用稳定常量并集中声明。",
            )
        ]


class AuthoritativeConfigModuleRule(CoreQualityRule[ConfigCallContext]):
    """QG100: application modules must not read config from ad-hoc sources.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG100"

    def evaluate(self, context: ConfigCallContext) -> list[Finding]:
        """Evaluate QG100 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        if context.is_config_boundary:
            return []
        facts, node, name, key = context.facts, context.node, context.name, context.key
        if name in CONFIG_FILE_CALLS:
            return [
                facts.make_finding(
                    node,
                    "QG100",
                    f"非配置模块直接调用 `{name}` 读取配置文件。",
                    symbol=facts.module,
                    severity="warning",
                    suggestion="配置应有唯一权威来源，业务模块不要自行读取 YAML/JSON/TOML。",
                )
            ]
        return [
            facts.make_finding(
                node,
                "QG100",
                f"非配置模块直接读取 `{name}({key!r})`。",
                symbol=facts.module,
                severity="warning",
                suggestion="仅在唯一配置模块读取、转换和校验，消费方接收正式配置对象。",
            )
        ]


def configuration_default_severity(key: str) -> str:
    """
    根据配置键是否影响部署连接或鉴权决定默认值告警级别。

    Args:
        key: 环境变量或配置键。

    Returns:
        部署关键配置返回 warning，普通行为参数返回 info。
    """
    upper = key.upper()
    return (
        "warning"
        if any(marker in upper for marker in DEPLOYMENT_CONFIG_MARKERS)
        else "info"
    )


def check_hardcoded_endpoint(facts, node):
    """Detect a literal endpoint embedded in production source.

    Args:
        facts: Module facts identifying the literal's source location.
        node: Candidate string constant to be classified by HardcodedEndpointRule.

    Returns:
        Endpoint-policy findings; non-endpoint constants yield no findings."""
    return HardcodedEndpointRule().evaluate(ModuleLiteralContext(facts, node))


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
            isinstance(value, ast.Call)
            and call_name(value) in CONFIG_CALLS | CONFIG_FILE_CALLS
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
    # A large repeated literal is evidence of duplication regardless of its
    # variable identifier. No identifier implies that a string is a Prompt.
    for statement in parsed.tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        if not isinstance(value, (ast.Constant, ast.BinOp, ast.JoinedStr)):
            continue
        text = collect_string_text(value)
        if len(text) >= config.duplicate_prompt_min_chars:
            signals.prompt_literals[text].append(
                (
                    parsed.facts.path,
                    statement.lineno,
                    next(iter(assignment_names(statement)), ""),
                )
            )
    findings: list[Finding] = []
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


def check_module_state_rules(
    parsed: ParsedModule, config: GuardConfig
) -> list[Finding]:
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
    """Run module-level state, declaration and wildcard-import rules in order.

    Args:
        facts: Facts for the module containing the top-level statement.
        statement: One AST statement from the module body, not a nested body.

    Returns:
        Ordered findings from state mutation, definition naming and wildcard imports."""
    findings = check_module_mutable_state(facts, statement)
    findings.extend(check_module_definition(facts, statement))
    findings.extend(
        WildcardImportRule().evaluate(ModuleStatementContext(facts, statement))
    )
    return findings


def check_module_mutable_state(facts, statement):
    """Check whether a module statement defines mutable shared state.

    Args:
        facts: Module facts used for source attribution and exceptions.
        statement: Candidate top-level assignment or declaration.

    Returns:
        Findings from MutableModuleStateRule; a harmless statement returns []."""
    return MutableModuleStateRule().evaluate(ModuleStatementContext(facts, statement))


def check_module_definition(facts: ModuleFacts, statement: ast.stmt) -> list[Finding]:
    """Apply four distinct declaration policies to one top-level definition.

    Only functions, async functions and classes are eligible. The nested
    definitions are collected once and reused when checking legacy names,
    migration wrappers, generic names and nested definition structure.

    Args:
        facts: Module facts for Finding paths and source positions.
        statement: Candidate function/class AST declaration.

    Returns:
        Definition-policy findings, or [] for a non-definition statement."""
    if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return []
    context = DefinitionRuleContext(
        facts, statement, statement.name.lower(), nested_definitions(statement)
    )
    findings: list[Finding] = []
    for detector in (
        LegacyDefinitionNameRule(),
        MigrationEntryNameRule(),
        GenericDefinitionNameRule(),
        NestedDefinitionStructureRule(),
    ):
        findings.extend(detector.evaluate(context))
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


def check_utility_module(facts, config):
    """Check utility-module growth against project configuration.

    Args:
        facts: Parsed module with structural and source facts.
        config: Quality configuration defining the utility-module policy.

    Returns:
        Findings from UtilityModuleGrowthRule when the module crosses its boundary."""
    return UtilityModuleGrowthRule().evaluate(UtilityModuleContext(facts, config))


@dataclass(frozen=True)
class ModuleLiteralContext:
    """Carry typed rule inputs: facts, node.

    Fields: facts, node."""

    facts: ModuleFacts
    node: ast.Constant


@dataclass(frozen=True)
class ModuleStatementContext:
    """Carry typed rule inputs: facts, statement.

    Fields: facts, statement."""

    facts: ModuleFacts
    statement: ast.stmt


@dataclass(frozen=True)
class UtilityModuleContext:
    """Carry typed rule inputs: facts, config.

    Fields: facts, config."""

    facts: ModuleFacts
    config: GuardConfig


class HardcodedEndpointRule(CoreQualityRule[ModuleLiteralContext]):
    """Own QG102 detection without another module parse.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG102"

    def evaluate(self, context: ModuleLiteralContext) -> list[Finding]:
        """Evaluate QG102 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, node = context.facts, context.node
        if not looks_like_service_endpoint(node.value):
            return []
        return [
            facts.make_finding(
                node,
                "QG102",
                f"源码中硬编码服务地址或端口：{node.value!r}。",
                symbol=facts.module,
                severity="warning",
                suggestion="服务地址进入唯一配置对象。",
            )
        ]


class MutableModuleStateRule(CoreQualityRule[ModuleStatementContext]):
    """Own QG092 detection without another module parse.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG092"

    def evaluate(self, context: ModuleStatementContext) -> list[Finding]:
        """Evaluate QG092 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, statement = context.facts, context.statement
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            return []
        if not isinstance(statement.value, (ast.List, ast.Dict, ast.Set)):
            return []
        names = assignment_names(statement)
        if not any(not name.isupper() and not name.startswith("__") for name in names):
            return []
        return [
            facts.make_finding(
                statement,
                "QG092",
                "模块级可变容器可能形成隐式共享状态。",
                symbol=facts.module,
                severity="info",
                suggestion="常量使用不可变结构；运行时状态放入具有明确生命周期的对象。",
            )
        ]


class UtilityModuleGrowthRule(CoreQualityRule[UtilityModuleContext]):
    """Own QG091 detection without another module parse.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG091"

    def evaluate(self, context: UtilityModuleContext) -> list[Finding]:
        """Evaluate QG091 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, config = context.facts, context.config
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


@dataclass(frozen=True)
class DefinitionRuleContext:
    """Carry typed rule inputs: facts, statement, lowered, nested.

    Fields: facts, statement, lowered, nested."""

    facts: ModuleFacts
    statement: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    lowered: str
    nested: list[ast.AST]


@dataclass(frozen=True)
class ConfigNodeRuleContext:
    """Carry typed rule inputs: facts, node, is_config_boundary, signals.

    Fields: facts, node, is_config_boundary, signals."""

    facts: ModuleFacts
    node: ast.AST
    is_config_boundary: bool
    signals: RepositorySignals


class LegacyDefinitionNameRule(CoreQualityRule[DefinitionRuleContext]):
    """Own the production QG094 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG094"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG094 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, statement, lowered, _nested = (
            context.facts,
            context.statement,
            context.lowered,
            context.nested,
        )
        findings: list[Finding] = []
        if is_compatibility_name(lowered):
            findings.append(
                facts.make_finding(
                    statement,
                    "QG094",
                    f"定义名 `{statement.name}` 表明存在兼容、旧版或回退路径。",
                    symbol=statement.name,
                    severity="info",
                    suggestion="无真实兼容需求时删除旧入口和别名。",
                )
            )
        return findings


class MigrationEntryNameRule(CoreQualityRule[DefinitionRuleContext]):
    """Own the production QG095 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG095"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG095 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, statement, lowered, _nested = (
            context.facts,
            context.statement,
            context.lowered,
            context.nested,
        )
        findings: list[Finding] = []
        if any(marker in lowered for marker in ("migrate", "migration", "backfill")):
            findings.append(
                facts.make_finding(
                    statement,
                    "QG095",
                    f"运行时模块包含迁移/回填入口 `{statement.name}`。",
                    symbol=statement.name,
                    severity="info",
                    suggestion="一次性迁移放入独立脚本。",
                )
            )
        return findings


class GenericDefinitionNameRule(CoreQualityRule[DefinitionRuleContext]):
    """Own the production QG096 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG096"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG096 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, statement, lowered, _nested = (
            context.facts,
            context.statement,
            context.lowered,
            context.nested,
        )
        findings: list[Finding] = []
        if lowered in GENERIC_FUNCTION_NAMES:
            findings.append(
                facts.make_finding(
                    statement,
                    "QG096",
                    f"定义名 `{statement.name}` 过于宽泛，无法表达具体职责。",
                    symbol=statement.name,
                    severity="info",
                    suggestion="使用说明输入、动作和产出的领域名称。",
                )
            )
        return findings


class NestedDefinitionStructureRule(CoreQualityRule[DefinitionRuleContext]):
    """Own the production QG097 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG097"

    def evaluate(self, context: DefinitionRuleContext) -> list[Finding]:
        """Evaluate QG097 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, statement, _lowered, nested = (
            context.facts,
            context.statement,
            context.lowered,
            context.nested,
        )
        findings: list[Finding] = []
        if nested:
            findings.append(
                facts.make_finding(
                    nested[0],
                    "QG097",
                    f"`{statement.name}` 内部定义了 {len(nested)} 个嵌套函数或类。",
                    symbol=statement.name,
                    severity="info",
                    suggestion="确认闭包确有必要；可复用逻辑应提升到明确作用域。",
                )
            )
        return findings


class WildcardImportRule(CoreQualityRule[ModuleStatementContext]):
    """Own the production QG093 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG093"

    def evaluate(self, context: ModuleStatementContext) -> list[Finding]:
        """Evaluate QG093 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, statement = context.facts, context.statement
        findings: list[Finding] = []
        if isinstance(statement, ast.ImportFrom) and any(
            alias.name == "*" for alias in statement.names
        ):
            findings.append(
                facts.make_finding(
                    statement,
                    "QG093",
                    "使用 `from ... import *`，符号来源和覆盖关系不透明。",
                    symbol=facts.module,
                    severity="warning",
                    suggestion="显式导入实际使用的名称。",
                )
            )
        return findings


class ConfigFallbackSourceRule(CoreQualityRule[ConfigNodeRuleContext]):
    """Own the production QG106 Finding decision over previously computed facts.

    The rule evaluates its supplied analysis context and reports evidence-backed findings."""

    code = "QG106"

    def evaluate(self, context: ConfigNodeRuleContext) -> list[Finding]:
        """Evaluate QG106 using the supplied rule evidence.

        Args:
            context: Precollected typed analysis facts for this rule.

        Returns:
            Findings backed by the supplied evidence, or an empty list."""
        facts, node, _is_config_boundary, _signals = (
            context.facts,
            context.node,
            context.is_config_boundary,
            context.signals,
        )
        findings: list[Finding] = []
        if (
            isinstance(node, ast.BoolOp)
            and config_source_fallback_count(node) >= MIN_CONFIG_SOURCES_FOR_FALLBACK
        ):
            findings.append(
                facts.make_finding(
                    node,
                    "QG106",
                    "同一表达式在多个配置来源之间回退。",
                    symbol=facts.module,
                    severity="warning",
                    suggestion="明确唯一权威来源；迁移逻辑不要永久留在运行时。",
                )
            )
        return findings
