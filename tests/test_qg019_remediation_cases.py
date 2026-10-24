"""QG019 module-size thresholds on production rule evaluation."""
from __future__ import annotations
from pathlib import Path
from rqgtest import TestCase
from runtime.src.config import GuardConfig
from runtime.src.definition_rule_objects import ModuleLineLimitRule, ModuleRuleContext
from runtime.src.facts import ModuleFacts

class QG019BoundaryCase(TestCase):
    """Check the exact accepted size and review thresholds using real Findings."""

    def get_enterpoint(self):
        """Use the actual QG019 rule rather than a fixture-local function."""
        return ModuleLineLimitRule().evaluate

    def get_input_data(self):
        """Build a real ModuleFacts context at the authored line-count boundary."""
        for case_id in ('qg019-1000', 'qg019-1001', 'qg019-2000', 'qg019-2001'):
            lines = int(case_id.removeprefix('qg019-'))
            facts = ModuleFacts(path=Path('fixture.py'), module='fixture', source='\n'.join(('value = 1' for _ in range(lines))))
            yield ((ModuleRuleContext(facts, GuardConfig(require_docstrings=False)),), {})

    def get_output_data(self):
        """Keep all four independent size/severity contracts explicit."""
        for case_id in ('qg019-1000', 'qg019-1001', 'qg019-2000', 'qg019-2001'):
            yield {'qg019-1000': (), 'qg019-1001': (('QG019', 'info', True),), 'qg019-2000': (('QG019', 'info', True),), 'qg019-2001': (('QG019', 'critical', False),)}[case_id]

    def equals(self, ground_truth, output):
        """Compare level and review gate for each emitted module-size Finding."""
        return tuple(((finding.code, finding.severity, 'semantic_review_required' in finding.evidence and finding.evidence['semantic_review_required'] is True) for finding in output)) == ground_truth
