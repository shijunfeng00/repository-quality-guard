"""Standard four-interface Cases for batch 13."""
from __future__ import annotations
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.multilang_rule_objects import CompositionContext, CppChangedComplexityRule, CppDeltaContext, CssDuplicateContext, DuplicateCssDeclarationRule, DuplicateHtmlIdRule, DuplicateScriptDeclarationRule, DuplicateScriptShapeRule, DynamicCompositionSizeRule, ExplicitAnyEscapeRule, FeatureDependencyCycleRule, GiantScriptOwnerGrowthRule, HtmlDuplicateContext, NestedHelperGrowthRule, ScriptDeltaContext, ScriptDuplicateContext, ScriptShapeContext

class Batch13QG195Case(TestCase):
    """Verify authored QG195 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG195 Core rule without an adapter."""
        return DuplicateScriptDeclarationRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG195Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG195Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.confidence, f.path, f.line, f.symbol, f.source) for f in actual))
        return expected == found

class Batch13QG200Case(TestCase):
    """Verify authored QG200 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG200 Core rule without an adapter."""
        return DuplicateScriptShapeRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG200Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG200Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.confidence, f.path, f.line, f.symbol, f.source) for f in actual))
        return expected == found

class Batch13QG198Case(TestCase):
    """Verify authored QG198 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG198 Core rule without an adapter."""
        return DynamicCompositionSizeRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG198Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG198Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.confidence, f.path, f.line, f.symbol, f.source) for f in actual))
        return expected == found

class Batch13QG199Case(TestCase):
    """Verify authored QG199 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG199 Core rule without an adapter."""
        return FeatureDependencyCycleRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG199Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG199Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.confidence, f.path, f.line, f.symbol, f.source) for f in actual))
        return expected == found

class Batch13QG207Case(TestCase):
    """Verify authored QG207 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG207 Core rule without an adapter."""
        return ExplicitAnyEscapeRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG207Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG207Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.confidence, f.path, f.line, f.symbol, f.source) for f in actual))
        return expected == found

class Batch13QG201Case(TestCase):
    """Verify authored QG201 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG201 Core rule without an adapter."""
        return DuplicateCssDeclarationRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG201Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG201Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.confidence, f.path, f.line, f.symbol, f.source) for f in actual))
        return expected == found

class Batch13QG202Case(TestCase):
    """Verify authored QG202 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG202 Core rule without an adapter."""
        return DuplicateHtmlIdRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG202Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG202Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.confidence, f.path, f.line, f.symbol, f.source) for f in actual))
        return expected == found

class Batch13QG196Case(TestCase):
    """Verify authored QG196 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG196 Core rule without an adapter."""
        return GiantScriptOwnerGrowthRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG196Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG196Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.confidence, f.path, f.line, f.symbol, f.source) for f in actual))
        return expected == found

class Batch13QG197Case(TestCase):
    """Verify authored QG197 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG197 Core rule without an adapter."""
        return NestedHelperGrowthRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG197Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG197Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.confidence, f.path, f.line, f.symbol, f.source) for f in actual))
        return expected == found

class Batch13QG204Case(TestCase):
    """Verify authored QG204 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG204 Core rule without an adapter."""
        return CppChangedComplexityRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG204Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/multilang-rule-input-oracle.pkl'):
            if record['case'] == 'Batch13QG204Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.confidence, f.path, f.line, f.symbol, f.source) for f in actual))
        return expected == found
