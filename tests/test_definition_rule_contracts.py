"""Real definition owner regression tests with independent TP/TN data and Oracle."""

from __future__ import annotations
from dataloaders.definition_rule_contexts import DefinitionRuleContextLoader
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.definition_rule_objects import (
    BooleanModeMatrixRule,
    DeepNestingRule,
    DuplicateBodyRule,
    EphemeralHelperRule,
    FragmentedOwnerRule,
    HighBranchCountRule,
    ImportCycleRule,
    MissingDocstringRule,
    MissingReturnsSectionRule,
    ModuleLineLimitRule,
    OvergrownClassRule,
    OversizedFunctionRule,
    ShortDocstringRule,
    SmallLowUseClassRule,
    SuppressionMarkerRule,
    UndocumentedParametersRule,
    UnusedPrivateRule,
    WideFunctionInterfaceRule,
)


class DefinitionQG001Case(TestCase):
    """Validate native QG001 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG001 evaluator."""
        return EphemeralHelperRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG001"
        ):
            yield ((EphemeralHelperRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG001":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG002Case(TestCase):
    """Validate native QG002 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG002 evaluator."""
        return SmallLowUseClassRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG002"
        ):
            yield ((SmallLowUseClassRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG002":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG008Case(TestCase):
    """Validate native QG008 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG008 evaluator."""
        return OvergrownClassRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG008"
        ):
            yield ((OvergrownClassRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG008":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG013Case(TestCase):
    """Validate native QG013 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG013 evaluator."""
        return FragmentedOwnerRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG013"
        ):
            yield ((FragmentedOwnerRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG013":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG014Case(TestCase):
    """Validate native QG014 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG014 evaluator."""
        return OversizedFunctionRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG014"
        ):
            yield ((OversizedFunctionRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG014":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG015Case(TestCase):
    """Validate native QG015 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG015 evaluator."""
        return WideFunctionInterfaceRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG015"
        ):
            yield ((WideFunctionInterfaceRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG015":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG016Case(TestCase):
    """Validate native QG016 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG016 evaluator."""
        return DeepNestingRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG016"
        ):
            yield ((DeepNestingRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG016":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG017Case(TestCase):
    """Validate native QG017 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG017 evaluator."""
        return HighBranchCountRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG017"
        ):
            yield ((HighBranchCountRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG017":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG018Case(TestCase):
    """Validate native QG018 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG018 evaluator."""
        return BooleanModeMatrixRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG018"
        ):
            yield ((BooleanModeMatrixRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG018":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG019Case(TestCase):
    """Validate native QG019 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG019 evaluator."""
        return ModuleLineLimitRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG019"
        ):
            yield ((ModuleLineLimitRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG019":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG020Case(TestCase):
    """Validate native QG020 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG020 evaluator."""
        return SuppressionMarkerRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG020"
        ):
            yield ((SuppressionMarkerRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG020":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG021Case(TestCase):
    """Validate native QG021 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG021 evaluator."""
        return DuplicateBodyRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG021"
        ):
            yield ((DuplicateBodyRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG021":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG022Case(TestCase):
    """Validate native QG022 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG022 evaluator."""
        return ImportCycleRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG022"
        ):
            yield ((ImportCycleRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG022":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG027Case(TestCase):
    """Validate native QG027 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG027 evaluator."""
        return MissingDocstringRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG027"
        ):
            yield ((MissingDocstringRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG027":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG028Case(TestCase):
    """Validate native QG028 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG028 evaluator."""
        return ShortDocstringRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG028"
        ):
            yield ((ShortDocstringRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG028":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG029Case(TestCase):
    """Validate native QG029 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG029 evaluator."""
        return UndocumentedParametersRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG029"
        ):
            yield ((UndocumentedParametersRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG029":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG030Case(TestCase):
    """Validate native QG030 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG030 evaluator."""
        return MissingReturnsSectionRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG030"
        ):
            yield ((MissingReturnsSectionRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG030":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class DefinitionQG156Case(TestCase):
    """Validate native QG156 definition production behavior with frozen TP/TN."""

    def get_enterpoint(self):
        """Return the unwrapped production QG156 evaluator."""
        return UnusedPrivateRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-rule-cases.json", "QG156"
        ):
            yield ((UnusedPrivateRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Read the independent Finding code and severity predictions."""
        for row in next(iter(JSONDataLoader("tests/cases/definition-rule-cases.json")))[
            "cases"
        ]:
            if row["rule"] == "QG156":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Compare the entire Finding ledger, not just matching positives."""
        return expected == tuple(((item.code, item.severity) for item in actual))


class UncertaintyQG001Case(TestCase):
    """Verify QG001 confidence under explicit unresolved topology edges."""

    def get_enterpoint(self):
        """Return the real uncertainty-sensitive Core evaluator."""
        return EphemeralHelperRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-uncertainty-cases.json", "QG001"
        ):
            yield ((EphemeralHelperRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Use the independently frozen expected confidence levels."""
        for row in next(
            iter(JSONDataLoader("tests/cases/definition-uncertainty-cases.json"))
        )["cases"]:
            if row["rule"] == "QG001":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Require exact code, severity and confidence on every Finding."""
        return expected == tuple(
            ((item.code, item.severity, item.confidence) for item in actual)
        )


class UncertaintyQG013Case(TestCase):
    """Verify QG013 confidence under explicit unresolved topology edges."""

    def get_enterpoint(self):
        """Return the real uncertainty-sensitive Core evaluator."""
        return FragmentedOwnerRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-uncertainty-cases.json", "QG013"
        ):
            yield ((FragmentedOwnerRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Use the independently frozen expected confidence levels."""
        for row in next(
            iter(JSONDataLoader("tests/cases/definition-uncertainty-cases.json"))
        )["cases"]:
            if row["rule"] == "QG013":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Require exact code, severity and confidence on every Finding."""
        return expected == tuple(
            ((item.code, item.severity, item.confidence) for item in actual)
        )


class UncertaintyQG156Case(TestCase):
    """Verify QG156 confidence under explicit unresolved topology edges."""

    def get_enterpoint(self):
        """Return the real uncertainty-sensitive Core evaluator."""
        return UnusedPrivateRule.evaluate

    def get_input_data(self):
        """Supply native source-derived inputs for the declared rule."""
        for _rqg_args, _rqg_kwargs in DefinitionRuleContextLoader(
            "tests/cases/definition-uncertainty-cases.json", "QG156"
        ):
            yield ((UnusedPrivateRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Use the independently frozen expected confidence levels."""
        for row in next(
            iter(JSONDataLoader("tests/cases/definition-uncertainty-cases.json"))
        )["cases"]:
            if row["rule"] == "QG156":
                yield tuple((tuple(item) for item in row["expected"]))

    def equals(self, expected, actual):
        """Require exact code, severity and confidence on every Finding."""
        return expected == tuple(
            ((item.code, item.severity, item.confidence) for item in actual)
        )
