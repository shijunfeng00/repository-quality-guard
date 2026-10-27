"""Closed public Case contract: metadata comes only from the base constructor."""
from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Literal


class Outcome(Enum):
    """Expected observable result of a production invocation."""

    RETURN = "return"
    EXCEPTION = "exception"


@dataclass(frozen=True, slots=True)
class CaseConfig:
    """Scheduling policy; the runner owns processes and execution.

    pool: execute on the shared, CPU-sized process pool (the default).
    main: execute in the coordinator process when process locality is required.
    """

    process: Literal["pool", "main"] = "pool"

    def __post_init__(self) -> None:
        if self.process not in ("pool", "main"):
            raise ValueError("CaseConfig.process must be 'pool' or 'main'")


class _DirectLeafMeta(ABCMeta):
    """Only the two public Case roots may directly implement ITestCase.

    User Case definitions must be concrete direct leaves of either root.
    The root interface is not a user-extensible third Case category.
    """

    def __new__(mcls, name, bases, namespace, **kwargs):
        result = super().__new__(mcls, name, bases, namespace, **kwargs)
        if result.__module__ == __name__ and name == "ITestCase":
            return result
        if result.__module__ == __name__ and name in (
            "TestCase", "StatefulTestCase"
        ) and bases == (ITestCase,):
            return result
        if len(bases) != 1 or bases[0] not in (TestCase, StatefulTestCase):
            raise TypeError(
                "Case must directly inherit TestCase or StatefulTestCase; "
                "direct ITestCase inheritance is reserved for the two standard roots"
            )
        if result.__abstractmethods__:
            raise TypeError("Case must implement all four abstract methods")
        return result


class ITestCase(ABC, metaclass=_DirectLeafMeta):
    """Sole abstract contract, including immutable execution metadata."""

    __slots__ = ("_case_type", "_case_config")

    def __init__(
        self, *, type: Outcome = Outcome.RETURN, config: CaseConfig | None = None
    ) -> None:
        if not isinstance(type, Outcome):
            raise TypeError("Case type must be Outcome.RETURN or Outcome.EXCEPTION")
        if config is not None and not isinstance(config, CaseConfig):
            raise TypeError("Case config must be CaseConfig or None")
        object.__setattr__(self, "_case_type", type)
        object.__setattr__(self, "_case_config", config or CaseConfig())

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Case metadata and state are immutable; use super().__init__")

    @property
    def type(self) -> Outcome:
        """Read-only expected outcome, declared on Case construction."""
        return self._case_type

    @property
    def config(self) -> CaseConfig:
        """Read-only scheduler requirements, declared by the Case."""
        return self._case_config

    @abstractmethod
    def get_enterpoint(self) -> Callable[..., Any]:
        """Return the real production callable or class, without invoking it."""

    @abstractmethod
    def get_input_data(self) -> Any:
        """Return an invocation or iterable of independent/ordered invocations."""

    @abstractmethod
    def get_output_data(self) -> Any:
        """Provide independently determined expected outputs or exceptions."""

    @abstractmethod
    def equals(self, expected: Any, actual: Any) -> bool:
        """Compare each independently specified oracle with one observation."""


class TestCase(ITestCase):
    """Execute independent inputs; classes/constructors are normal callables."""


class StatefulTestCase(ITestCase):
    """Execute an ordered input sequence while retaining the production state.

    First (args, kwargs) optionally creates a class instance or function-factory
    product. Subsequent (method_or_None, args, kwargs) tuples invoke the same
    retained target. Every operation has exactly one independent output oracle.
    """
