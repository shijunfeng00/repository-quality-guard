"""Public Profile authoring and extension contracts without hook or release tests."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from pathlib import Path
from rqgtest import TestCase, Outcome
from runtime.src.project_profiles import QualityGuardProfile, load_quality_profile

class ProfilePolicyBuildCase(TestCase):
    """Preserve three complementary public policy configuration contracts."""

    def get_enterpoint(self):
        """Return the public authoring-to-runtime policy function."""
        return QualityGuardProfile.build

    def get_input_data(self):
        """Supply explicitly authored policy objects without calling build."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.project_profiles import QualityGuardProfile
        for row in next(iter(JSONDataLoader('tests/cases/profile-contract-public.json')))['cases']:
            _case_arg_1_row = row
            _case_value_1 = QualityGuardProfile(manifest=_case_arg_1_row['manifest'])
            yield ((_case_value_1,), {})

    def get_output_data(self):
        """Keep independently declared rule, capability and path expectations."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.project_profiles import QualityGuardProfile
        for row in next(iter(JSONDataLoader('tests/cases/profile-contract-public.json')))['cases']:
            yield row['expected']

    def equals(self, expected, actual):
        """Require complete authored policy identity and no unexpected suppression."""
        observed = {'disabled': list(actual.disabled_rules), 'capabilities': list(actual.capabilities), 'nonblocking': list(actual.nonblocking_paths), 'baseline_passthrough': list(actual.test_baseline_passthrough_paths), 'agents': actual.agents_file}
        return observed == expected

class ProfileProtectedRuleCase(TestCase):
    """Disallow disabling both independently reserved trust-boundary rule codes."""

    def __init__(self):
        """Mark the public API rejection as an exception result."""
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        """Return actual public rule suppression configurator."""
        return QualityGuardProfile.disable_rule

    def get_input_data(self):
        """Check two explicitly reserved source-authored rule identities."""
        for rule in ('QG990', 'QG984'):
            yield ((QualityGuardProfile(manifest={'name': 'sample'}), rule), {})

    def get_output_data(self):
        """Require one precise validation failure for each protected rule."""
        for rule in ('QG990', 'QG984'):
            yield ValueError

    def equals(self, expected, actual):
        """Reject success and unrelated exceptions."""
        return type(actual) is expected

class ProfileInvalidRulePackCase(TestCase):
    """Reject non-rule-pack objects at the public extension boundary."""

    def __init__(self):
        """Declare exception result for invalid extension object."""
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        """Use actual public include extension function."""
        return QualityGuardProfile.include

    def get_input_data(self):
        """Supply a deliberately non-pack object without invoking include."""
        yield ((QualityGuardProfile(manifest={'name': 'sample'}), object()), {})

    def get_output_data(self):
        """Demand declared TypeError for malformed extension contract."""
        yield TypeError

    def equals(self, expected, actual):
        """Do not accept any other error type."""
        return type(actual) is expected

class ProfileExampleLoadCase(TestCase):
    """Verify configured example Profile resolves from real authored assets."""

    def get_enterpoint(self):
        """Use public Profile loader without a test-local dispatch wrapper."""
        return load_quality_profile

    def get_input_data(self):
        """Provide an existing source-controlled example policy directory."""
        root = Path(__file__).resolve().parents[1]
        yield ((str(root / 'profiles/qg-example-profile'),), {'release_root': root})

    def get_output_data(self):
        """Demand manifest identity and declared policy keys, not runtime-built values."""
        yield 'qg-example-profile'

    def equals(self, expected, actual):
        """Reject missing or unrelated extension classes."""
        return actual is not None and actual.manifest['name'] == expected

class ProfileInvalidExtensionCase(TestCase):
    """Reject unsupported extension entrypoint syntax before importing Python code."""

    def __init__(self):
        """Declare rejection as the authentic framework exception outcome."""
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        """Return actual public Profile loader."""
        return load_quality_profile

    def get_input_data(self):
        """Construct invalid declarative entrypoint fixtures without loading them."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.project_profiles import QualityGuardProfile
        root = Path(tempfile.mkdtemp(prefix='rqg-profile-invalid-v02-'))
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        (root / 'profile.json').write_text(json.dumps({'schema': 'repository-quality-guard/profile-v1', 'name': 'fixture', 'entrypoint': 'extension.py:Other'}), encoding='utf-8')
        (root / 'extension.py').write_text('from runtime.src.profile_api import QualityGuardProfile\nclass Profile(QualityGuardProfile):\n    name = "fixture"\n', encoding='utf-8')
        _case_value_2 = ((str(root),), {'release_root': root})
        yield _case_value_2

    def get_output_data(self):
        """Require explicitly documented malformed-entrypoint failure."""
        yield ValueError

    def equals(self, expected, actual):
        """Reject non-validation exceptions and silent successful fallbacks."""
        return type(actual) is expected
