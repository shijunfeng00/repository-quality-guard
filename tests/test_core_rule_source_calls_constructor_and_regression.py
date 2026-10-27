"""Direct native graph, compiler and regression rule behavior checks."""
from __future__ import annotations
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.source_call_contracts import SourceBoundCallRule
from runtime.src.constructor_read_order import ConstructorReadBeforeWriteRule
from runtime.src.partial_commit_flow import PartialCommitRule
from runtime.src.native_cpp_rule_objects import NativeCppCoverageRule
from runtime.src.baseline_regression_rules import DocstringRegressionRule, QualityFingerprintRegressionRule

class Batch14QG209Case(TestCase):
    """Verify independent QG209 positive and negative production observations."""

    def get_enterpoint(self):
        """Return the actual public Core rule evaluator."""
        return SourceBoundCallRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG209Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG209Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare all Finding identities without filtering away extra results."""
        return expected == tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))

class Batch14QG210Case(TestCase):
    """Verify independent QG210 positive and negative production observations."""

    def get_enterpoint(self):
        """Return the actual public Core rule evaluator."""
        return ConstructorReadBeforeWriteRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG210Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG210Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare all Finding identities without filtering away extra results."""
        return expected == tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))

class Batch14QG213Case(TestCase):
    """Verify independent QG213 positive and negative production observations."""

    def get_enterpoint(self):
        """Return the actual public Core rule evaluator."""
        return PartialCommitRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG213Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG213Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare all Finding identities without filtering away extra results."""
        return expected == tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))

class Batch14QG208Case(TestCase):
    """Verify independent QG208 positive and negative production observations."""

    def get_enterpoint(self):
        """Return the actual public Core rule evaluator."""
        return NativeCppCoverageRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG208Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG208Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare all Finding identities without filtering away extra results."""
        return expected == tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))

class Batch14QG169Case(TestCase):
    """Verify independent QG169 positive and negative production observations."""

    def get_enterpoint(self):
        """Return the actual public Core rule evaluator."""
        return DocstringRegressionRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG169Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG169Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare all Finding identities without filtering away extra results."""
        return expected == tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))

class Batch14QG179Case(TestCase):
    """Verify independent QG179 positive and negative production observations."""

    def get_enterpoint(self):
        """Return the actual public Core rule evaluator."""
        return QualityFingerprintRegressionRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG179Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core-rule-source-calls-input-oracle.pkl'):
            if record['case'] == 'Batch14QG179Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare all Finding identities without filtering away extra results."""
        return expected == tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))
