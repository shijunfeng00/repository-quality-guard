"""Independent exact file/class/function Profile policy scope contracts."""
from __future__ import annotations
from rqgtest import TestCase, Outcome
from runtime.src.project_profiles import QualityGuardProfile
from runtime.src.scoped_rule_policy import ScopedRuleMatcher

class ScopedProfilePublicMatcherCase(TestCase):
    """Prove authorized and unrelated source locations remain distinct."""

    def get_enterpoint(self):
        """Return real matcher method, not a test-owned callback."""
        return ScopedRuleMatcher.matches

    def get_input_data(self):
        """Supply authenticated lexical root, validated policy and authored locations."""
        import atexit
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.project_profiles import QualityGuardProfile
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.scoped_rule_policy import ScopedRuleMatcher
        _DATA = Path(__file__).resolve().parents[1] / 'tests' / 'cases'
        _case_value_1 = json.loads((_DATA / 'profile-scoped-disable-v1.json').read_text(encoding='utf-8'))
        document = _case_value_1
        for row in document['cases']:
            _case_arg_2_row = row
            _case_arg_2_document = document
            root = Path(tempfile.mkdtemp(prefix='rqg-scoped-migrated-'))
            for filename, source in (_case_arg_2_row['sources'] if 'sources' in _case_arg_2_row else _case_arg_2_document['source']).items():
                target = root / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
            profile = QualityGuardProfile(manifest={'name': 'scope-fixture', 'rules': {'scoped_disable': _case_arg_2_row['disable']}}).build()
            settings = GuardConfig().with_project_profile(profile)
            matcher = ScopedRuleMatcher(root, settings.profile_scoped_rule_disables)
            weakref.finalize(matcher, shutil.rmtree, root, ignore_errors=True)
            _case_value_2 = matcher
            matcher = _case_value_2
            for check in row['checks']:
                yield ((matcher, check['code'], check['path'], check['line']), {})

    def get_output_data(self):
        """Keep independent authored expectations for all source locations."""
        import atexit
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.project_profiles import QualityGuardProfile
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.scoped_rule_policy import ScopedRuleMatcher
        _DATA = Path(__file__).resolve().parents[1] / 'tests' / 'cases'
        _case_value_3 = json.loads((_DATA / 'profile-scoped-disable-v1.json').read_text(encoding='utf-8'))
        document = _case_value_3
        for row in document['cases']:
            for check in row['checks']:
                yield check['suppressed']

    def equals(self, expected, actual):
        """Require an exact boolean for the suppression decision."""
        return type(actual) is bool and actual is expected

class ScopedProfileMalformedPolicyCase(TestCase):
    """Reject each invalid scoped policy declaration with a visible exception."""

    def __init__(self):
        """Declare exception contract to the frozen independent framework."""
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        """Return actual Profile build entrypoint."""
        return QualityGuardProfile.build

    def get_input_data(self):
        """Supply invalid policies from the original frozen negative fixtures."""
        import atexit
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.project_profiles import QualityGuardProfile
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.scoped_rule_policy import ScopedRuleMatcher
        _DATA = Path(__file__).resolve().parents[1] / 'tests' / 'cases'
        _case_value_4 = json.loads((_DATA / 'profile-scoped-disable-v1.json').read_text(encoding='utf-8'))
        for row in _case_value_4['reject']:
            profile = QualityGuardProfile(manifest={'name': 'scope-fixture', 'rules': {'scoped_disable': row['disable']}})
            yield ((profile,), {})

    def get_output_data(self):
        """Every malformed policy must fail with the authored exception class."""
        import atexit
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.project_profiles import QualityGuardProfile
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.relation_graph import RepositoryRelationGraph
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.scoped_rule_policy import ScopedRuleMatcher
        _DATA = Path(__file__).resolve().parents[1] / 'tests' / 'cases'
        _case_value_5 = json.loads((_DATA / 'profile-scoped-disable-v1.json').read_text(encoding='utf-8'))
        for row in _case_value_5['reject']:
            yield ValueError

    def equals(self, expected, actual):
        """Require the exact published validation exception type."""
        return type(actual) is expected
