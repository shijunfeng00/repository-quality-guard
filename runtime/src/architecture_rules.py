from __future__ import annotations

from .config import GuardConfig
from .model import Definition, Finding
from .policy_common import RepositorySignals


def check_repository_architecture(
    definitions: list[Definition],
    signals: RepositorySignals,
    config: GuardConfig,
) -> list[Finding]:
    """
    检查单实现抽象、配置多源、重复 Prompt 和转发链。

    Args:
        definitions: 仓库内全部定义。
        signals: 跨模块信号汇总对象。
        config: 当前仓库质量配置。

    Returns:
        跨模块架构发现列表。
    """
    findings: list[Finding] = []
    findings.extend(check_single_implementation_abstracts(signals))
    findings.extend(check_duplicate_config_sources(signals))
    findings.extend(check_duplicate_config_defaults(signals))
    findings.extend(check_duplicate_prompts(signals))
    findings.extend(check_deprecated_unused(definitions))
    for name, locations in sorted(signals.module_constants.items()):
        if name in signals.referenced_names:
            continue
        for path, line, module in locations:
            findings.append(
                Finding(
                    code="QG188",
                    severity="warning",
                    confidence="medium",
                    path=str(path),
                    line=line,
                    column=1,
                    message=f"模块级常量 `{name}` 在仓库内没有静态读取、属性访问或显式导入。",
                    symbol=f"{module}.{name}" if module else name,
                    suggestion=(
                        "全仓确认没有真实调用方后删除；若它是仓库外公开 API 或框架动态入口，"
                        "应提供显式导出/注册证据，而不是仅以兼容名义保留。"
                    ),
                    evidence={"calls": 0, "references": 0, "kind": "module_constant"},
                )
            )
    return findings


def check_single_implementation_abstracts(signals: RepositorySignals) -> list[Finding]:
    """
    检查仓库内只有一个实现的抽象类。

    Args:
        signals: 跨模块静态信号。

    Returns:
        单实现抽象层发现列表。
    """
    findings: list[Finding] = []
    for name, (path, line, qualname) in signals.abstract_classes.items():
        implementations = signals.subclass_bases[name]
        if len(implementations) != 1:
            continue
        findings.append(
            Finding(
                code="QG088",
                severity="info",
                confidence="medium",
                path=str(path),
                line=line,
                column=1,
                message=f"抽象类 `{qualname}` 在仓库内只有一个实现。",
                symbol=qualname,
                suggestion="确认是否存在插件或仓库外实现；否则考虑删除无价值抽象层。",
                evidence={"implementation": implementations[0][2]},
            )
        )
    return findings


def check_duplicate_config_sources(signals: RepositorySignals) -> list[Finding]:
    """
    检查同一配置键在多个模块被直接读取。

    Args:
        signals: 跨模块静态信号。

    Returns:
        配置多源发现列表。
    """
    findings: list[Finding] = []
    for key, locations in signals.env_reads.items():
        modules = {str(path) for path, _, _ in locations}
        if key == "<dynamic>" or len(modules) <= 1:
            continue
        path, line, _ = locations[1]
        findings.append(
            Finding(
                code="QG101",
                severity="warning",
                confidence="high",
                path=str(path),
                line=line,
                column=1,
                message=f"配置键 `{key}` 在 {len(modules)} 个模块被直接读取。",
                symbol=key,
                suggestion="仅在唯一配置加载模块读取一次。",
                evidence={"readers": [f"{p}:{ln}:{name}" for p, ln, name in locations]},
            )
        )
    return findings


def check_duplicate_config_defaults(signals: RepositorySignals) -> list[Finding]:
    """
    检查同一配置键和默认值在多个位置重复定义。

    Args:
        signals: 跨模块静态信号。

    Returns:
        重复默认值发现列表。
    """
    findings: list[Finding] = []
    for (key, default), locations in signals.config_defaults.items():
        if len({str(path) for path, _ in locations}) <= 1:
            continue
        path, line = locations[1]
        findings.append(
            Finding(
                code="QG107",
                severity="warning",
                confidence="high",
                path=str(path),
                line=line,
                column=1,
                message=f"配置 `{key}` 的默认值 `{default}` 在多个模块重复定义。",
                symbol=key,
                suggestion="默认值只在唯一配置模型中声明。",
                evidence={"locations": [f"{p}:{ln}" for p, ln in locations]},
            )
        )
    return findings


def check_duplicate_prompts(signals: RepositorySignals) -> list[Finding]:
    """
    检查长 Prompt 文本在多个模块重复出现。

    Args:
        signals: 跨模块静态信号。

    Returns:
        重复 Prompt 发现列表。
    """
    findings: list[Finding] = []
    for text, locations in signals.prompt_literals.items():
        if len({str(path) for path, _, _ in locations}) <= 1:
            continue
        path, line, name = locations[1]
        findings.append(
            Finding(
                code="QG113",
                severity="info",
                confidence="high",
                path=str(path),
                line=line,
                column=1,
                message=f"长 Prompt `{name}` 在多个模块重复出现。",
                symbol=name,
                suggestion="提取唯一模板或公共契约，避免多份规则逐渐分叉。",
                evidence={
                    "characters": len(text),
                    "locations": [f"{p}:{ln}" for p, ln, _ in locations],
                },
            )
        )
    return findings


def check_deprecated_unused(definitions: list[Definition]) -> list[Finding]:
    """
    检查已标记弃用但仓库内无引用的定义。

    Args:
        definitions: 仓库内全部定义。

    Returns:
        可删除弃用定义候选列表。
    """
    findings: list[Finding] = []
    for definition in definitions:
        if not any(
            "deprecated" in decorator.lower() for decorator in definition.decorators
        ):
            continue
        if definition.calls + definition.references != 0:
            continue
        findings.append(
            Finding(
                code="QG098",
                severity="info",
                confidence="high",
                path=str(definition.path),
                line=definition.line,
                column=definition.column,
                message=f"弃用定义 `{definition.qualname}` 在仓库内已无静态引用。",
                symbol=definition.symbol_id,
                suggestion="确认无仓库外公共 API 后删除，不要永久保留兼容入口。",
            )
        )
    return findings
