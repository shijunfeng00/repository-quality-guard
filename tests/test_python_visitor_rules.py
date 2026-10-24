"""Independent real production visitor regression on frozen AST programs."""
from __future__ import annotations
from rqgtest import PickleDataLoader
from dataloaders.python_visitor_contexts import PythonVisitorContextLoader
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.visitor_rule_objects import BroadExceptionRule, FallbackImportRule, GetattrDefaultContractRule, HasattrContractRule, ManualMappingProjectionRule, MappingAccessRule, MappingPopDefaultRule, MappingSetdefaultRule, MissingMappingKeyRule, NestedDefinitionVisibilityRule, SuppressedExceptionRule, SwallowedContractExceptionRule, UnknownMappingOwnerRule, UnsupportedCompatibilityClaimRule

class Batch9QG007Case(TestCase):
    """Verify exact QG007 source findings against authored exception cases."""

    def get_enterpoint(self):
        """Return the production rule evaluator, without calling it here."""
        return BroadExceptionRule().evaluate

    def get_input_data(self):
        """Yield AST contexts built by the independent fixture loader."""
        yield from PythonVisitorContextLoader(
            'tests/cases/python-visitor-rule-cases.json', 'QG007'
        )

    def get_output_data(self):
        """Yield independently authored Finding oracles in fixture order."""
        for document in JSONDataLoader('tests/cases/python-visitor-rule-cases.json'):
            for row in document['cases']:
                if row['rule'] == 'QG007':
                    yield tuple(tuple(item) for item in row['expected'])

    def equals(self, expected, actual):
        """Require the Finding code, severity, position, and scope to match."""
        observed = tuple(
            (finding.code, finding.severity, finding.line, finding.symbol)
            for finding in actual
        )
        return expected == observed

class Batch9QG009Case(TestCase):
    """Require exact authored QG009 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG009 visitor evaluator, without a wrapper."""
        return FallbackImportRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG009Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG009Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG006Case(TestCase):
    """Require exact authored QG006 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG006 visitor evaluator, without a wrapper."""
        return GetattrDefaultContractRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG006Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG006Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG005Case(TestCase):
    """Require exact authored QG005 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG005 visitor evaluator, without a wrapper."""
        return HasattrContractRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG005Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG005Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG153Case(TestCase):
    """Require exact authored QG153 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG153 visitor evaluator, without a wrapper."""
        return ManualMappingProjectionRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG153Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG153Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG004Case(TestCase):
    """Require exact authored QG004 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG004 visitor evaluator, without a wrapper."""
        return MappingAccessRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG004Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG004Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG025Case(TestCase):
    """Require exact authored QG025 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG025 visitor evaluator, without a wrapper."""
        return MappingPopDefaultRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG025Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG025Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG012Case(TestCase):
    """Require exact authored QG012 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG012 visitor evaluator, without a wrapper."""
        return MappingSetdefaultRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG012Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG012Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG003Case(TestCase):
    """Require exact authored QG003 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG003 visitor evaluator, without a wrapper."""
        return MissingMappingKeyRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG003Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG003Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG154Case(TestCase):
    """Require exact authored QG154 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG154 visitor evaluator, without a wrapper."""
        return NestedDefinitionVisibilityRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG154Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG154Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG024Case(TestCase):
    """Require exact authored QG024 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG024 visitor evaluator, without a wrapper."""
        return SuppressedExceptionRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG024Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG024Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG023Case(TestCase):
    """Require exact authored QG023 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG023 visitor evaluator, without a wrapper."""
        return SwallowedContractExceptionRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG023Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG023Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG026Case(TestCase):
    """Require exact authored QG026 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG026 visitor evaluator, without a wrapper."""
        return UnknownMappingOwnerRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG026Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG026Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))

class Batch9QG158Case(TestCase):
    """Require exact authored QG158 Finding source location and identity."""

    def get_enterpoint(self):
        """Return the production QG158 visitor evaluator, without a wrapper."""
        return UnsupportedCompatibilityClaimRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG158Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/python_visitor_rules-input-oracle.pkl'):
            if record['case'] == 'Batch9QG158Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare complete observed Finding tuples against frozen ground truth."""
        return expected == tuple(((finding.code, finding.severity, finding.line, finding.symbol) for finding in actual))
