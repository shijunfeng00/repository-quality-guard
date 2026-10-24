"""Standard four-interface Cases for batch 15."""
from __future__ import annotations
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.interface_policy_rules import InterfaceChangeReviewRule, InterfaceDefinitionGrowthRule, InterfaceGrowthContext
from runtime.src.model import InterfaceChange, InterfaceSymbol
from runtime.src.project_profiles import StableMappingContract

class Batch15QG180Case(TestCase):
    """Verify authored QG180 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG180 Core rule without an adapter."""
        return InterfaceChangeReviewRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/interface_direct_v02-input-oracle.pkl'):
            if record['case'] == 'Batch15QG180Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/interface_direct_v02-input-oracle.pkl'):
            if record['case'] == 'Batch15QG180Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))
        return expected == found

class Batch15QG181Case(TestCase):
    """Verify authored QG181 true/false scenarios against its production evaluator."""

    def get_enterpoint(self):
        """Return the real QG181 Core rule without an adapter."""
        return InterfaceDefinitionGrowthRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/interface_direct_v02-input-oracle.pkl'):
            if record['case'] == 'Batch15QG181Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/interface_direct_v02-input-oracle.pkl'):
            if record['case'] == 'Batch15QG181Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require real Finding identity, location and expected status."""
        found = tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))
        return expected == found
