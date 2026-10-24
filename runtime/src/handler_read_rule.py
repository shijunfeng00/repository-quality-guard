"""Independent source-bound QG211 rule over normalized exception path facts."""

from __future__ import annotations

from dataclasses import dataclass

from .core_rule_contract import CoreQualityRule
from .model import Finding
from .topology_facts import RepositoryTopology


@dataclass(frozen=True)
class ExceptionReadRuleContext:
    """Bind the normalized topology to the source paths selected for scanning.

    Limit topology facts to paths actually selected for the current scan.
    """

    topology: RepositoryTopology
    audited_paths: frozenset[str]


class ExceptionHandlerReadRule(CoreQualityRule[ExceptionReadRuleContext]):
    """QG211: conditional handler reads after interrupted assignment.

    Consume existing graph evidence without synthesizing call or exception paths.
    """

    code = "QG211"

    def evaluate(self, context: ExceptionReadRuleContext) -> list[Finding]:
        """Report exception-handler reads before a possibly failing assignment.

        The scanner's topology already records potential handler-read witnesses.
        Only paths selected for this invocation contribute QG211 findings; the
        rule does not infer whether a particular RHS can raise at runtime.

        Args:
            context: Normalized exception-read witnesses and audited path set.

        Returns:
            Warning findings retaining the topology's source locations and
            evidence, or an empty list outside the selected scan scope.
        """
        return [
            Finding(
                code=self.code,
                severity="warning",
                confidence="medium",
                path=fact.path.as_posix(),
                line=fact.line,
                column=fact.column,
                symbol=fact.owner_symbol_id,
                message="异常处理器可能在局部赋值完成前读取变量。",
                suggestion="检查真实被捕获异常能否从赋值 RHS 抛出；只在证实的分支修复初始化顺序。",
                evidence={
                    "capability": fact.capability,
                    "source_sha256": fact.source_sha256,
                    "local_name": fact.local_name,
                    "assignment_line": fact.assignment_line,
                    "try_line": fact.try_line,
                    "handler_line": fact.handler_line,
                },
                source="source-bound-r1",
            )
            for fact in context.topology.exception_handler_reads
            if fact.path.as_posix() in context.audited_paths
        ]
