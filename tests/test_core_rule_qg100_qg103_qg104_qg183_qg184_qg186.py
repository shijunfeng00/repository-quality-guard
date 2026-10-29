"""Direct public configuration and reflection rule regression Cases."""

from __future__ import annotations
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.private_boundary_rules import (
    PrivateReflectionRule,
    TypedReflectionRule,
    RuntimeContractPatchRule,
)
from runtime.src.module_quality_rules import (
    AuthoritativeConfigModuleRule,
    ConfigDefaultRule,
    DynamicConfigKeyRule,
)


class Batch8QG183Case(TestCase):
    """Compare QG183 authored TP/TN with the production rule."""

    def get_enterpoint(self):
        """Use the actual module quality or reflection evaluator."""
        return PrivateReflectionRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG183Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((PrivateReflectionRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG183Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding set including negatives and severity."""
        return expected == tuple(((f.code, f.severity) for f in actual))


class Batch8QG184Case(TestCase):
    """Compare QG184 authored TP/TN with the production rule."""

    def get_enterpoint(self):
        """Use the actual module quality or reflection evaluator."""
        return TypedReflectionRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG184Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((TypedReflectionRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG184Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding set including negatives and severity."""
        return expected == tuple(((f.code, f.severity) for f in actual))


class Batch8QG186Case(TestCase):
    """Compare QG186 authored TP/TN with the production rule."""

    def get_enterpoint(self):
        """Use the actual module quality or reflection evaluator."""
        return RuntimeContractPatchRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG186Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((RuntimeContractPatchRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG186Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding set including negatives and severity."""
        return expected == tuple(((f.code, f.severity) for f in actual))


class Batch8QG100Case(TestCase):
    """Compare QG100 authored TP/TN with the production rule."""

    def get_enterpoint(self):
        """Use the actual module quality or reflection evaluator."""
        return AuthoritativeConfigModuleRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG100Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((AuthoritativeConfigModuleRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG100Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding set including negatives and severity."""
        return expected == tuple(((f.code, f.severity) for f in actual))


class Batch8QG103Case(TestCase):
    """Compare QG103 authored TP/TN with the production rule."""

    def get_enterpoint(self):
        """Use the actual module quality or reflection evaluator."""
        return ConfigDefaultRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG103Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((ConfigDefaultRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG103Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding set including negatives and severity."""
        return expected == tuple(((f.code, f.severity) for f in actual))


class Batch8QG104Case(TestCase):
    """Compare QG104 authored TP/TN with the production rule."""

    def get_enterpoint(self):
        """Use the actual module quality or reflection evaluator."""
        return DynamicConfigKeyRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG104Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((DynamicConfigKeyRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core-rule-qg100-qg103-input-oracle.pkl"
        ):
            if record["case"] == "Batch8QG104Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding set including negatives and severity."""
        return expected == tuple(((f.code, f.severity) for f in actual))
