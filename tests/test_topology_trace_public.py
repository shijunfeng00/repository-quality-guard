"""Direct, independent bounded reachability and topology merge contracts."""
from __future__ import annotations
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.parameter_forwarding import trace_parameter_forwarding
from runtime.src.topology_facts import merge_topologies

class ParameterTraceReachabilityCase(TestCase):
    """Prove cycle termination and complete long-chain reachability independently."""

    def get_enterpoint(self):
        """Select real bounded parameter-flow algorithm."""
        return trace_parameter_forwarding

    def get_input_data(self):
        """Load independently frozen topology input and expected observation."""
        for record in PickleDataLoader('tests/cases/topology-trace-input-oracle.pkl'):
            if record['case'] == 'ParameterTraceReachabilityCase':
                yield record['input']

    def get_output_data(self):
        """Load independently frozen topology input and expected observation."""
        for record in PickleDataLoader('tests/cases/topology-trace-input-oracle.pkl'):
            if record['case'] == 'ParameterTraceReachabilityCase':
                yield record['expected']

    def equals(self, expected, actual):
        """Require every reachable indexed symbol and no invented alternatives."""
        return tuple(sorted(actual)) == tuple(sorted(expected))

class TopologyMergeDeduplicationCase(TestCase):
    """Require duplicate source-certified relations to remain singular."""

    def get_enterpoint(self):
        """Use real repository-level merge operation without wrappers."""
        return merge_topologies

    def get_input_data(self):
        """Load independently frozen topology input and expected observation."""
        for record in PickleDataLoader('tests/cases/topology-trace-input-oracle.pkl'):
            if record['case'] == 'TopologyMergeDeduplicationCase':
                yield record['input']

    def get_output_data(self):
        """Load independently frozen topology input and expected observation."""
        for record in PickleDataLoader('tests/cases/topology-trace-input-oracle.pkl'):
            if record['case'] == 'TopologyMergeDeduplicationCase':
                yield record['expected']

    def equals(self, expected, actual):
        """Compare all retained parameter facts with authored unique witness."""
        return tuple(((f.source_symbol_id, f.target_symbol_id, f.line) for f in actual.parameter_forwardings)) == expected
