"""Production behavior-family counter contract; report rendering is not a unit test."""
from __future__ import annotations
import json
from pathlib import Path
from rqgtest import TestCase
from runtime.src.model import Finding
from runtime.src.behavior_families import family_rows


class BehaviorFamilyPublicCase(TestCase):
    """Count each independent authored Finding in the correct production family."""

    def get_enterpoint(self):
        """Return actual production grouping, not a report formatter wrapper."""
        return family_rows

    def get_input_data(self):
        """Supply independent source-bound findings to the target processor."""
        rows = json.loads((Path(__file__).parent / 'cases/finding-behavior-families.json').read_text(encoding='utf-8'))['cases']
        for row in rows:
            findings = [Finding(code=item[0], severity='warning', confidence='medium',
                                path=item[1], line=item[2], column=1, symbol='sample',
                                message='sample', suggestion='sample') for item in row['findings']]
            yield ((findings,), {})

    def get_output_data(self):
        """Use frozen independent family totals and per-family proof classes."""
        rows = json.loads((Path(__file__).parent / 'cases/finding-behavior-families.json').read_text(encoding='utf-8'))['cases']
        for row in rows:
            yield row['expected']

    def equals(self, expected, actual):
        """Compare full group counters, and require stable empty-family markers."""
        observed = {
            'total': sum(group[1] for group in actual),
            'groups': [list(group[1:6]) for group in actual],
            'empty_stable': all(group[1] == 0 and group[-1] == '—' for group in actual),
        }
        return observed == expected
