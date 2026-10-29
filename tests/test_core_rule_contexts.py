"""Legacy Batch6 ground-truth scenarios directly invoking real Core rule owners."""

from __future__ import annotations
from rqgtest import PickleDataLoader
from dataloaders.core_rule_contexts import CoreRuleContextLoader
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.state_boundary_rules import StateBoundaryEvaluator
from runtime.src.config import GuardConfig
from runtime.src.call_chain_rules import SingleUseChainRule
from runtime.src.architecture_rules import (
    SingleImplementationAbstractRule,
    DuplicateConfigSourcesRule,
    DuplicateConfigDefaultsRule,
    DuplicatePromptTextRule,
    UnusedDeprecatedDefinitionRule,
    UnusedModuleConstantRule,
)
from runtime.src.class_quality_rules import (
    SinglePublicMethodClassRule,
    PrivateHelperDominanceRule,
    StaticMethodDominanceRule,
    UnusedInstanceStateRule,
    SingleFieldDataclassRule,
    PassThroughMemberRule,
    ConstructorDependencyRule,
    FieldLocalityRule,
)
from runtime.src.agent_rules import (
    UndeclaredStateReadRule,
    UndeclaredStateWriteRule,
    NestedSequenceStateWriteRule,
    StateScopeEscapeRule,
    UnscopedStateUpdateEntryRule,
    StateMergeAmbiguityRule,
    InconsistentStateWriteTypeRule,
    DirectToolStateMutationRule,
    BareToolStringRule,
    UndocumentedToolResultRule,
    MixedHistoryTraceRule,
    FailureHistoryPollutionRule,
)
from runtime.src.state_boundary_rules import DynamicExecutionBoundaryRule


class Batch6QG080Case(TestCase):
    """Check QG080 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG080 production evaluator."""
        return SinglePublicMethodClassRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG080Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((SinglePublicMethodClassRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG080Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG081Case(TestCase):
    """Check QG081 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG081 production evaluator."""
        return PrivateHelperDominanceRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG081Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((PrivateHelperDominanceRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG081Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG082Case(TestCase):
    """Check QG082 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG082 production evaluator."""
        return StaticMethodDominanceRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG082Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((StaticMethodDominanceRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG082Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG083Case(TestCase):
    """Check QG083 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG083 production evaluator."""
        return UnusedInstanceStateRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG083Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((UnusedInstanceStateRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG083Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG084Case(TestCase):
    """Check QG084 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG084 production evaluator."""
        return SingleFieldDataclassRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG084Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((SingleFieldDataclassRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG084Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG085Case(TestCase):
    """Check QG085 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG085 production evaluator."""
        return PassThroughMemberRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG085Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((PassThroughMemberRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG085Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG086Case(TestCase):
    """Check QG086 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG086 production evaluator."""
        return ConstructorDependencyRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG086Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((ConstructorDependencyRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG086Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG087Case(TestCase):
    """Check QG087 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG087 production evaluator."""
        return FieldLocalityRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG087Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((FieldLocalityRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG087Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG088Case(TestCase):
    """Check QG088 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG088 production evaluator."""
        return SingleImplementationAbstractRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG088Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((SingleImplementationAbstractRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG088Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG101Case(TestCase):
    """Check QG101 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG101 production evaluator."""
        return DuplicateConfigSourcesRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG101Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((DuplicateConfigSourcesRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG101Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG107Case(TestCase):
    """Check QG107 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG107 production evaluator."""
        return DuplicateConfigDefaultsRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG107Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((DuplicateConfigDefaultsRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG107Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG113Case(TestCase):
    """Check QG113 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG113 production evaluator."""
        return DuplicatePromptTextRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG113Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((DuplicatePromptTextRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG113Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG188Case(TestCase):
    """Check QG188 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG188 production evaluator."""
        return UnusedModuleConstantRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG188Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((UnusedModuleConstantRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG188Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG098Case(TestCase):
    """Check QG098 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG098 production evaluator."""
        return UnusedDeprecatedDefinitionRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG098Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((UnusedDeprecatedDefinitionRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG098Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG190Case(TestCase):
    """Check QG190 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG190 production evaluator."""
        return DynamicExecutionBoundaryRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG190Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((DynamicExecutionBoundaryRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG190Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG125Case(TestCase):
    """Check QG125 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG125 production evaluator."""
        return UndeclaredStateReadRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG125Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((UndeclaredStateReadRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG125Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG124Case(TestCase):
    """Check QG124 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG124 production evaluator."""
        return UndeclaredStateWriteRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG124Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((UndeclaredStateWriteRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG124Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG127Case(TestCase):
    """Check QG127 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG127 production evaluator."""
        return NestedSequenceStateWriteRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG127Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((NestedSequenceStateWriteRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG127Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG128Case(TestCase):
    """Check QG128 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG128 production evaluator."""
        return StateScopeEscapeRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG128Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((StateScopeEscapeRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG128Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG134Case(TestCase):
    """Check QG134 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG134 production evaluator."""
        return UnscopedStateUpdateEntryRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG134Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((UnscopedStateUpdateEntryRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG134Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG135Case(TestCase):
    """Check QG135 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG135 production evaluator."""
        return StateMergeAmbiguityRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG135Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((StateMergeAmbiguityRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG135Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG126Case(TestCase):
    """Check QG126 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG126 production evaluator."""
        return InconsistentStateWriteTypeRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG126Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((InconsistentStateWriteTypeRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG126Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG120Case(TestCase):
    """Check QG120 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG120 production evaluator."""
        return DirectToolStateMutationRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG120Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((DirectToolStateMutationRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG120Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG122Case(TestCase):
    """Check QG122 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG122 production evaluator."""
        return BareToolStringRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG122Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((BareToolStringRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG122Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG131Case(TestCase):
    """Check QG131 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG131 production evaluator."""
        return UndocumentedToolResultRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG131Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((UndocumentedToolResultRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG131Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG129Case(TestCase):
    """Check QG129 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG129 production evaluator."""
        return MixedHistoryTraceRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG129Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((MixedHistoryTraceRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG129Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG130Case(TestCase):
    """Check QG130 on originally authored positive and negative inputs."""

    def get_enterpoint(self):
        """Return the real QG130 production evaluator."""
        return FailureHistoryPollutionRule.evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG130Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield ((FailureHistoryPollutionRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG130Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Require exact Finding code and severity across the complete output."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG168Case(TestCase):
    """Preserve the original positive/negative QG168 boundary with true sources."""

    def get_enterpoint(self):
        """Return the real QG168 rule method without a wrapper."""
        return SingleUseChainRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in CoreRuleContextLoader(
            "tests/cases/core-rule-contexts.json", "QG168"
        ):
            yield ((SingleUseChainRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Use the original independently authored complete rule expectations."""
        for row in next(iter(JSONDataLoader("tests/cases/core-rule-contexts.json")))[
            "cases"
        ]:
            if row["rule"] == "QG168":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Reject missing or additional Findings, with precise severity."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )


class Batch6QG189Case(TestCase):
    """Preserve the original positive/negative QG189 boundary with true sources."""

    def get_enterpoint(self):
        """Return the real QG189 rule method without a wrapper."""
        return StateBoundaryEvaluator.check_module

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG189Case":
                _rqg_args, _rqg_kwargs = record["input"]
                yield (
                    (
                        StateBoundaryEvaluator(
                            [],
                            GuardConfig(
                                profile_capabilities=("state-write-boundary",),
                                profile_settings=(("state_boundary_type", "State"),),
                            ),
                        ),
                        *_rqg_args,
                    ),
                    _rqg_kwargs,
                )

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader(
            "tests/cases/core_rule_contexts-input-oracle.pkl"
        ):
            if record["case"] == "Batch6QG189Case":
                yield record["expected"]

    def equals(self, expected, actual):
        """Reject missing or additional Findings, with precise severity."""
        return expected == tuple(
            ((finding.code, finding.severity) for finding in actual)
        )
