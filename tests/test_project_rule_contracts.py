"""Standard four-interface Cases for batch 12."""

from __future__ import annotations
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.project_contracts import (
    CallableCompatibilityRule,
    FrozenSSEContractRule,
    HeaderDeltaRule,
    RouteDeltaRule,
)


class Batch12QG147Case(TestCase):
    """Verify authored QG147 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG147 Core rule without an adapter."""
        return CallableCompatibilityRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader("tests/cases/project-rule-input-oracle.pkl"):
            if record["case"] == "Batch12QG147Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((CallableCompatibilityRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader("tests/cases/project-rule-input-oracle.pkl"):
            if record["case"] == "Batch12QG147Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        code, truth = expected
        found = tuple(
            ((f.code, f.severity, f.path, f.symbol) for f in actual if f.code == code)
        )
        return truth == found


class Batch12QG148Case(TestCase):
    """Verify authored QG148 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG148 Core rule without an adapter."""
        return FrozenSSEContractRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader("tests/cases/project-rule-input-oracle.pkl"):
            if record["case"] == "Batch12QG148Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((FrozenSSEContractRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader("tests/cases/project-rule-input-oracle.pkl"):
            if record["case"] == "Batch12QG148Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        code, truth = expected
        found = tuple(
            ((f.code, f.severity, f.path, f.symbol) for f in actual if f.code == code)
        )
        return truth == found


class Batch12QG151Case(TestCase):
    """Verify authored QG151 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG151 Core rule without an adapter."""
        return RouteDeltaRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader("tests/cases/project-rule-input-oracle.pkl"):
            if record["case"] == "Batch12QG151Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((RouteDeltaRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader("tests/cases/project-rule-input-oracle.pkl"):
            if record["case"] == "Batch12QG151Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        code, truth = expected
        found = tuple(
            ((f.code, f.severity, f.path, f.symbol) for f in actual if f.code == code)
        )
        return truth == found


class Batch12QG152Case(TestCase):
    """Verify authored QG152 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG152 Core rule without an adapter."""
        return HeaderDeltaRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader("tests/cases/project-rule-input-oracle.pkl"):
            if record["case"] == "Batch12QG152Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((HeaderDeltaRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader("tests/cases/project-rule-input-oracle.pkl"):
            if record["case"] == "Batch12QG152Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        code, truth = expected
        found = tuple(
            ((f.code, f.severity, f.path, f.symbol) for f in actual if f.code == code)
        )
        return truth == found
