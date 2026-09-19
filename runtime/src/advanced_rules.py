from __future__ import annotations

import ast

from .agent_rules import (
    check_agent_rules,
    check_state_write_types,
    collect_registered_tools,
    collect_state_contracts,
)
from .state_boundary_rules import StateBoundaryEvaluator
from .architecture_rules import check_repository_architecture
from .class_quality_rules import check_class_rules
from .config import GuardConfig
from .contract_rules import check_contract_rules, check_side_effect_rules
from .facts import ModuleFacts
from .model import Definition, Finding
from .module_quality_rules import check_module_rules
from .naming_rules import check_naming_rules
from .policy_common import (
    RepositorySignals,
    StateContract,
    collect_function_signatures,
    is_boundary_module,
    iter_scoped_definitions,
    parse_modules,
)
from .private_boundary_rules import check_private_boundary_rules
from .return_rules import check_return_rules
from .signature_rules import (
    check_call_safety_rules,
    check_positional_boolean_calls,
    check_signature_rules,
)


class AdvancedRuleEvaluator:
    """
    协调契约、架构、Agent 和调用安全类静态规则。

    该评估器只负责编排。具体判断分布在签名、契约、架构和 Agent
    专项模块中，避免把全部规则堆入单个超大文件。
    """

    def __init__(self, config: GuardConfig) -> None:
        """
        初始化高级规则评估器。

        Args:
            config: 当前仓库质量检查配置。

        Returns:
            None。
        """
        self.config = config
        self.signals = RepositorySignals()

    def evaluate(
        self,
        definitions: list[Definition],
        facts: list[ModuleFacts],
    ) -> list[Finding]:
        """
        执行全部高级模块级和仓库级规则。

        Args:
            definitions: 基础扫描器收集的代码定义。
            facts: 各 Python 模块的源码与静态事实。

        Returns:
            高级规则产生的发现列表。
        """
        parsed = parse_modules(facts)
        contracts = collect_state_contracts(parsed, self.config)
        collect_registered_tools(parsed, self.config, self.signals)
        state_boundary = StateBoundaryEvaluator(parsed, self.config)
        findings: list[Finding] = []
        for module in parsed:
            findings.extend(self._evaluate_module(module.facts, module.tree, contracts))
            findings.extend(check_module_rules(module, self.config, self.signals))
            findings.extend(check_private_boundary_rules(module))
            findings.extend(state_boundary.check_module(module))
            findings.extend(check_naming_rules(module.facts, module.tree))
        signatures = collect_function_signatures(parsed)
        findings.extend(check_positional_boolean_calls(parsed, signatures))
        findings.extend(check_state_write_types(self.signals))
        findings.extend(check_repository_architecture(definitions, self.signals, self.config))
        return findings

    def _evaluate_module(
        self,
        facts: ModuleFacts,
        tree: ast.Module,
        contracts: dict[str, StateContract],
    ) -> list[Finding]:
        """
        对单个模块执行函数和类级高级规则。

        Args:
            facts: 当前模块静态事实。
            tree: 当前模块 AST。
            contracts: 仓库状态类型契约。

        Returns:
            当前模块产生的高级规则发现列表。
        """
        findings: list[Finding] = []
        for node, qualname, class_name in iter_scoped_definitions(tree):
            if isinstance(node, ast.ClassDef):
                findings.extend(
                    check_class_rules(
                        facts,
                        node,
                        qualname,
                        self.config,
                        self.signals,
                    )
                )
                continue
            boundary = is_boundary_module(facts.module, self.config.boundary_module_markers)
            findings.extend(check_signature_rules(facts, node, qualname, self.config))
            findings.extend(check_return_rules(facts, node, qualname))
            if not boundary:
                findings.extend(check_contract_rules(facts, node, qualname))
            findings.extend(check_side_effect_rules(facts, node, qualname, allow_print=boundary))
            findings.extend(check_call_safety_rules(facts, node, qualname))
            findings.extend(
                check_agent_rules(
                    facts,
                    node,
                    qualname,
                    class_name,
                    contracts,
                    self.config,
                    self.signals,
                )
            )
        return findings
