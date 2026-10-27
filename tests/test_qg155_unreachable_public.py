"""Independent QG155 unreachable control-flow behavior contracts."""
from __future__ import annotations
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.visitor_rule_objects import UnreachableControlFlowRule

class QG155UnreachableCase(TestCase):
    """Check callable, conditional and loop cases against frozen source oracles."""

    def get_enterpoint(self):
        """Execute the real QG155 owner through the frozen TestUnit."""
        return UnreachableControlFlowRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/qg155-unreachable-input-oracle.pkl'):
            if record['case'] == 'QG155UnreachableCase':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/qg155-unreachable-input-oracle.pkl'):
            if record['case'] == 'QG155UnreachableCase':
                yield record['expected']

    def equals(self, expected, actual):
        """Check every Finding code, severity, path and source line exactly."""
        return expected == tuple(((f.code, f.severity, f.path, f.line) for f in actual))
