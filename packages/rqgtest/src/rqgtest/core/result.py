"""Immutable, complete per-invocation result ledger."""

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True, slots=True)
class SampleResult:
    """One observable invocation, including failed and invalid observations."""

    name: str
    status: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Nonempty execution evidence; never discard failing samples."""

    samples: tuple[SampleResult, ...]
    invalid: str | None = None

    @property
    def passed(self) -> int:
        return sum(sample.status == "PASS" for sample in self.samples)

    @property
    def succeeded(self) -> bool:
        return (
            bool(self.samples) and not self.invalid and self.passed == len(self.samples)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total": len(self.samples),
            "succeeded": self.succeeded,
            "invalid": self.invalid,
            "results": [sample.to_dict() for sample in self.samples],
        }


class TestRunFailure(AssertionError):
    """A failed batch retains the full structured report."""

    def __init__(self, report: BatchResult) -> None:
        self.report = report
        super().__init__(f"TestUnit: {report.passed}/{len(report.samples)} PASS")
