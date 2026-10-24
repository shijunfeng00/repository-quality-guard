"""Production Core rule ownership must be unique across the repository."""

from pathlib import Path

from rqgtest import TestCase
from runtime.src.core_rule_contract import duplicate_rule_owners


class CoreRuleOwnerInventoryCase(TestCase):
    """Check every actual CoreQualityRule declaration via the production inventory."""

    def get_enterpoint(self):
        """Return the production rule-owner inventory over actual source declarations."""
        return duplicate_rule_owners

    def get_input_data(self):
        """Supply the full production runtime source root to inspect."""
        root = Path(__file__).resolve().parents[1] / "runtime" / "src"
        return ((root,), {})

    def get_output_data(self):
        """Require no Core rule identifiers to have duplicate declared owners."""
        return {}

    def equals(self, ground_truth, output):
        """Compare the entire duplicate-owner mapping, not a sample of owners."""
        return ground_truth == output
