"""QG240 accepts authenticated shared DataLoader modules, never arbitrary helpers."""
from __future__ import annotations
import ast
from pathlib import Path
from rqgtest import TestCase
from runtime.src.case_contract_rules import case_contract_findings


class DataLoaderModuleProvenanceCase(TestCase):
    """Only a single directly authenticated DataLoader is legal in its directory."""

    def get_enterpoint(self):
        """Call the production source provenance rule."""
        return case_contract_findings

    def get_input_data(self):
        """Feed standalone source programs, including unauthorized lookalikes."""
        records = (
            ('from rqgtest import JSONDataLoader\nclass Legit(JSONDataLoader):\n    pass\n', ()),
            ('from rqgtest import PickleDataLoader\nclass Legit(PickleDataLoader):\n    pass\n', ()),
            ('class Impostor(JSONDataLoader):\n    pass\n', ('QG240', 'QG240')),
            ('from rqgtest import JSONDataLoader\nclass Legit(JSONDataLoader):\n    pass\ndef extra(): return 1\n', ('QG240', 'QG240')),
            ('from rqgtest import JSONDataLoader\nclass Legit(JSONDataLoader):\n    pass\nclass Extra: pass\n', ('QG240', 'QG240', 'QG240')),
        )
        for source, _ in records:
            yield ((Path('tests/dataloaders/inputs.py'), ast.parse(source)), {})

    def get_output_data(self):
        """Return independently authored positive and negative expected codes."""
        yield ()
        yield ()
        yield ('QG240', 'QG240')
        yield ('QG240', 'QG240', 'QG240')
        yield ('QG240', 'QG240', 'QG240')

    def equals(self, expected, actual):
        """Compare full rule-code multiplicity, including zero findings."""
        return tuple(item.code for item in actual) == expected
