"""Real public Profile mapping rule behavior for frozen and relative protocols."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.project_contracts import validate_project_contracts

class MappingQG146Case(TestCase):
    """Check immutable frozen mapping and relative key evidence QG146."""

    def get_enterpoint(self):
        """Return the published Profile-contract validator, without wrapping."""
        return validate_project_contracts

    def get_input_data(self):
        """Produce independent frozen/relative source snapshots for QG146."""
        import json
        from pathlib import Path
        from runtime.src.project_profiles import ProjectProfile, StableMappingContract
        for row in next(iter(JSONDataLoader('tests/cases/mapping-contracts-public.json')))['cases']:
            if row['rule'] == 'QG146':
                _case_arg_1_row = row
                frozen = _case_arg_1_row['mode'] == 'frozen'
                contract = StableMappingContract('api.py', 'get', keys=('id',) if frozen else (), relative_code='QG182' if _case_arg_1_row['rule'] == 'QG182' else 'QG146')
                profile = ProjectProfile(name='fixture', stable_mapping_contracts=(contract,))
                _case_value_1 = ((profile, {'api.py': _case_arg_1_row['base']}, {'api.py': _case_arg_1_row['target']}, {}), {})
                yield _case_value_1

    def get_output_data(self):
        """Use original authored Finding identities, without running the target."""
        import json
        from pathlib import Path
        from runtime.src.project_profiles import ProjectProfile, StableMappingContract
        for row in next(iter(JSONDataLoader('tests/cases/mapping-contracts-public.json')))['cases']:
            if row['rule'] == 'QG146':
                yield tuple((tuple(value) for value in row['expected']))

    def equals(self, expected, actual):
        """Check all actual Finding identities including severity and rule owner."""
        return expected == tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))

class MappingQG182Case(TestCase):
    """Check relative mapping interface additions/removals and unknowns QG182."""

    def get_enterpoint(self):
        """Return the public contract validation function directly."""
        return validate_project_contracts

    def get_input_data(self):
        """Supply independently authored relative protocol snapshots."""
        import json
        from pathlib import Path
        from runtime.src.project_profiles import ProjectProfile, StableMappingContract
        for row in next(iter(JSONDataLoader('tests/cases/mapping-contracts-public.json')))['cases']:
            if row['rule'] == 'QG182':
                _case_arg_2_row = row
                frozen = _case_arg_2_row['mode'] == 'frozen'
                contract = StableMappingContract('api.py', 'get', keys=('id',) if frozen else (), relative_code='QG182' if _case_arg_2_row['rule'] == 'QG182' else 'QG146')
                profile = ProjectProfile(name='fixture', stable_mapping_contracts=(contract,))
                _case_value_2 = ((profile, {'api.py': _case_arg_2_row['base']}, {'api.py': _case_arg_2_row['target']}, {}), {})
                yield _case_value_2

    def get_output_data(self):
        """Load the original source-authored complete Finding projections."""
        import json
        from pathlib import Path
        from runtime.src.project_profiles import ProjectProfile, StableMappingContract
        for row in next(iter(JSONDataLoader('tests/cases/mapping-contracts-public.json')))['cases']:
            if row['rule'] == 'QG182':
                yield tuple((tuple(value) for value in row['expected']))

    def equals(self, expected, actual):
        """Compare complete emitted rule identities, no filtering of extras."""
        return expected == tuple(((f.code, f.severity, f.path, f.symbol) for f in actual))
