"""QG208 coverage decisions; native compiler outcomes remain execution evidence."""

from __future__ import annotations

from dataclasses import dataclass

from .core_rule_contract import CoreQualityRule
from .model import Finding

_SOURCE = "quality-guard-multilang"


@dataclass(frozen=True, slots=True)
class NativeCoverageContext:
    """Summarize whether each original C++ translation unit was checked.

    Fields:
        unavailable: Translation units whose configured native toolchain could not run.
        missing: Units with no usable authoritative compiler build contract.
        translation_units: Full set of discovered original translation unit paths."""

    unavailable: tuple[str, ...]
    missing: tuple[str, ...]
    translation_units: tuple[str, ...]


class NativeCppCoverageRule(CoreQualityRule[NativeCoverageContext]):
    """QG208: distinguish missing build contracts from unavailable native execution.

    Coverage diagnostics are not equivalent to a proven C++ syntax failure;
    they describe which original translation units could not be evaluated."""

    code = "QG208"

    def evaluate(self, context: NativeCoverageContext) -> list[Finding]:
        """Report unverified native translation-unit coverage using measured input sets.

        Args:
            context: Unavailable toolchains, missing contracts and all discovered units.

        Returns:
            Ordered QG208 coverage findings without guessing compilation results."""
        native_unavailable = context.unavailable
        missing_cpp_contracts = context.missing
        cpp_translation_units = context.translation_units
        findings: list[Finding] = []
        if native_unavailable:
            findings.append(
                Finding(
                    code="QG208",
                    severity="info",
                    confidence="high",
                    path=native_unavailable[0],
                    line=1,
                    column=1,
                    message=(
                        f"{len(native_unavailable)} 个 C++ 翻译单元虽有编译命令，"
                        "但当前无法执行或无法证明与快照一致的原生语法检查。"
                    ),
                    suggestion="核对原始编译器、源码快照和构建环境，不得将此视为编译成功。",
                    evidence={
                        "capability": "NATIVE_CPP_SYNTAX_NOT_MEASURED",
                        "unavailable_paths": tuple(native_unavailable),
                        "semantic_proof": "NOT_MEASURED",
                    },
                    source=_SOURCE,
                )
            )
        if missing_cpp_contracts:
            findings.append(
                Finding(
                    code="QG208",
                    severity="info",
                    confidence="high",
                    path=missing_cpp_contracts[0],
                    line=1,
                    column=1,
                    message=(
                        f"{len(missing_cpp_contracts)}/{len(cpp_translation_units)} 个 C++ "
                        "翻译单元没有可认证的编译命令；当前静态检查不能证明其原生语义正确。"
                    ),
                    suggestion=(
                        "提供项目真实 toolchain 生成的 compile_commands.json；"
                        "在项目的正式编译器上运行实际构建和测试。"
                    ),
                    evidence={
                        "capability": "MISSING_AUTHORITATIVE_CPP_TU_CONTRACT",
                        "total_translation_units": len(cpp_translation_units),
                        "missing_translation_units": len(missing_cpp_contracts),
                        "missing_paths": missing_cpp_contracts,
                        "semantic_proof": "NOT_MEASURED",
                    },
                    source=_SOURCE,
                )
            )
        return findings
