"""Public Profile selection behavior across portable and sealed repositories."""
from __future__ import annotations
from rqgtest import TestCase, Outcome
from runtime.src.project_profiles import resolve_profile_reference, get_project_profile

class ProfileResolveValidCase(TestCase):
    """Verify authorized identity, installed provenance, and generic fallback."""

    def get_enterpoint(self):
        """Use the actual public repository selection function."""
        return resolve_profile_reference

    def get_input_data(self):
        """Prepare portable or sealed repo facts for non-exception outcomes."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        _DATA = Path(__file__).resolve().parents[1] / 'tests/cases/profile-selection.json'
        while True:
            _case_value_1 = json.loads(_DATA.read_text(encoding='utf-8'))['cases']
            break
            break
        for row in _case_value_1:
            if row['mode'] == 'resolve' and 'error' not in row['expected']:
                _case_arg_2_row = row
                while True:
                    root = Path(tempfile.mkdtemp(prefix='rqg-profile-selection-v02-'))
                    atexit.register(shutil.rmtree, root, ignore_errors=True)
                    release = root / 'release'
                    repo = root / _case_arg_2_row.get('repo_name', 'repo')
                    repo.mkdir()
                    (release / 'runtime').mkdir(parents=True)
                    installed = bool(_case_arg_2_row.get('installed'))
                    (release / 'runtime' / 'RELEASE.lock').write_text('schema=repository-quality-guard/release-v1\nversion=0.20.1\n' + 'distribution=' + ('agents' if installed else 'skill') + '\n' + 'manifest_sha256=' + '0' * 64 + '\nprotected_file_count=0\n', encoding='utf-8')
                    if installed:
                        name = _case_arg_2_row.get('installed_name', 'alpha')
                        installed_root = release / 'installed'
                        installed_root.mkdir()
                        (installed_root / 'POLICY.json').write_text(json.dumps({'schema': 'repository-quality-guard/installed-policy-v1', 'profile_name': name, 'profile_version': '1' if name else '', 'profile_source': 'explicit-cli' if name else 'generic'}), encoding='utf-8')
                        profile = installed_root / 'profile'
                    else:
                        name = 'alpha'
                        profile = release / 'profiles' / name
                    if name:
                        profile.mkdir(parents=True)
                        (profile / 'profile.json').write_text(json.dumps({'schema': 'repository-quality-guard/profile-v1', 'name': name, 'entrypoint': 'extension.py'}), encoding='utf-8')
                        (profile / 'extension.py').write_text("from runtime.src.profile_api import QualityGuardProfile\nclass Profile(QualityGuardProfile):\n    name='alpha'\n", encoding='utf-8')
                    if _case_arg_2_row.get('marker'):
                        (repo / 'agents').mkdir()
                        (repo / 'agents' / 'react_agent.py').write_text('# not a Profile selection signal\n', encoding='utf-8')
                    if _case_arg_2_row['mode'] == 'lookup':
                        lookup = _case_arg_2_row['lookup']
                        name_or_path = str(profile) if lookup == 'path' else 'alpha' if lookup == 'name' else lookup
                        _case_value_2 = ((name_or_path,), {'release_root': release})
                        break
                    selection = _case_arg_2_row.get('select')
                    _case_value_2 = ((repo, str(selection) if selection is not None else None), {'release_root': release})
                    break
                    break
                yield _case_value_2

    def get_output_data(self):
        """Read independent name and source authority expectations."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        _DATA = Path(__file__).resolve().parents[1] / 'tests/cases/profile-selection.json'
        while True:
            _case_value_3 = json.loads(_DATA.read_text(encoding='utf-8'))['cases']
            break
            break
        for row in _case_value_3:
            if row['mode'] == 'resolve' and 'error' not in row['expected']:
                yield {key: value for key, value in row['expected'].items() if key in {'name', 'source'}}

    def equals(self, expected, actual):
        """Compare full declared public identity and authoritative selection source."""
        return {'name': actual.name, 'source': actual.source} == expected

class ProfileResolveInvalidCase(TestCase):
    """Reject unauthorized installed Profile switching without an implicit fallback."""

    def __init__(self):
        """Select the framework's authentic raised-exception outcome."""
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        """Invoke the production Profile selection function directly."""
        return resolve_profile_reference

    def get_input_data(self):
        """Provide invalid installed-policy choices from fixed source rows."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        _DATA = Path(__file__).resolve().parents[1] / 'tests/cases/profile-selection.json'
        while True:
            _case_value_4 = json.loads(_DATA.read_text(encoding='utf-8'))['cases']
            break
            break
        for row in _case_value_4:
            if row['mode'] == 'resolve' and 'error' in row['expected']:
                _case_arg_5_row = row
                while True:
                    root = Path(tempfile.mkdtemp(prefix='rqg-profile-selection-v02-'))
                    atexit.register(shutil.rmtree, root, ignore_errors=True)
                    release = root / 'release'
                    repo = root / _case_arg_5_row.get('repo_name', 'repo')
                    repo.mkdir()
                    (release / 'runtime').mkdir(parents=True)
                    installed = bool(_case_arg_5_row.get('installed'))
                    (release / 'runtime' / 'RELEASE.lock').write_text('schema=repository-quality-guard/release-v1\nversion=0.20.1\n' + 'distribution=' + ('agents' if installed else 'skill') + '\n' + 'manifest_sha256=' + '0' * 64 + '\nprotected_file_count=0\n', encoding='utf-8')
                    if installed:
                        name = _case_arg_5_row.get('installed_name', 'alpha')
                        installed_root = release / 'installed'
                        installed_root.mkdir()
                        (installed_root / 'POLICY.json').write_text(json.dumps({'schema': 'repository-quality-guard/installed-policy-v1', 'profile_name': name, 'profile_version': '1' if name else '', 'profile_source': 'explicit-cli' if name else 'generic'}), encoding='utf-8')
                        profile = installed_root / 'profile'
                    else:
                        name = 'alpha'
                        profile = release / 'profiles' / name
                    if name:
                        profile.mkdir(parents=True)
                        (profile / 'profile.json').write_text(json.dumps({'schema': 'repository-quality-guard/profile-v1', 'name': name, 'entrypoint': 'extension.py'}), encoding='utf-8')
                        (profile / 'extension.py').write_text("from runtime.src.profile_api import QualityGuardProfile\nclass Profile(QualityGuardProfile):\n    name='alpha'\n", encoding='utf-8')
                    if _case_arg_5_row.get('marker'):
                        (repo / 'agents').mkdir()
                        (repo / 'agents' / 'react_agent.py').write_text('# not a Profile selection signal\n', encoding='utf-8')
                    if _case_arg_5_row['mode'] == 'lookup':
                        lookup = _case_arg_5_row['lookup']
                        name_or_path = str(profile) if lookup == 'path' else 'alpha' if lookup == 'name' else lookup
                        _case_value_5 = ((name_or_path,), {'release_root': release})
                        break
                    selection = _case_arg_5_row.get('select')
                    _case_value_5 = ((repo, str(selection) if selection is not None else None), {'release_root': release})
                    break
                    break
                yield _case_value_5

    def get_output_data(self):
        """Require the original explicit ValueError failure contract."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        _DATA = Path(__file__).resolve().parents[1] / 'tests/cases/profile-selection.json'
        while True:
            _case_value_6 = json.loads(_DATA.read_text(encoding='utf-8'))['cases']
            break
            break
        for row in _case_value_6:
            if row['mode'] == 'resolve' and 'error' in row['expected']:
                yield ValueError

    def equals(self, expected, actual):
        """Disallow any different error type or silent successful fallback."""
        return type(actual) is expected

class ProfileSealedLookupValidCase(TestCase):
    """Accept authorized sealed installed Profile by exact name and path."""

    def get_enterpoint(self):
        """Return the true public policy accessor."""
        return get_project_profile

    def get_input_data(self):
        """Build both previously authored authorized lookup forms."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        _DATA = Path(__file__).resolve().parents[1] / 'tests/cases/profile-selection.json'
        while True:
            _case_value_7 = json.loads(_DATA.read_text(encoding='utf-8'))['cases']
            break
            break
        for row in _case_value_7:
            if row['mode'] == 'lookup' and 'error' not in row['expected']:
                _case_arg_8_row = row
                while True:
                    root = Path(tempfile.mkdtemp(prefix='rqg-profile-selection-v02-'))
                    atexit.register(shutil.rmtree, root, ignore_errors=True)
                    release = root / 'release'
                    repo = root / _case_arg_8_row.get('repo_name', 'repo')
                    repo.mkdir()
                    (release / 'runtime').mkdir(parents=True)
                    installed = bool(_case_arg_8_row.get('installed'))
                    (release / 'runtime' / 'RELEASE.lock').write_text('schema=repository-quality-guard/release-v1\nversion=0.20.1\n' + 'distribution=' + ('agents' if installed else 'skill') + '\n' + 'manifest_sha256=' + '0' * 64 + '\nprotected_file_count=0\n', encoding='utf-8')
                    if installed:
                        name = _case_arg_8_row.get('installed_name', 'alpha')
                        installed_root = release / 'installed'
                        installed_root.mkdir()
                        (installed_root / 'POLICY.json').write_text(json.dumps({'schema': 'repository-quality-guard/installed-policy-v1', 'profile_name': name, 'profile_version': '1' if name else '', 'profile_source': 'explicit-cli' if name else 'generic'}), encoding='utf-8')
                        profile = installed_root / 'profile'
                    else:
                        name = 'alpha'
                        profile = release / 'profiles' / name
                    if name:
                        profile.mkdir(parents=True)
                        (profile / 'profile.json').write_text(json.dumps({'schema': 'repository-quality-guard/profile-v1', 'name': name, 'entrypoint': 'extension.py'}), encoding='utf-8')
                        (profile / 'extension.py').write_text("from runtime.src.profile_api import QualityGuardProfile\nclass Profile(QualityGuardProfile):\n    name='alpha'\n", encoding='utf-8')
                    if _case_arg_8_row.get('marker'):
                        (repo / 'agents').mkdir()
                        (repo / 'agents' / 'react_agent.py').write_text('# not a Profile selection signal\n', encoding='utf-8')
                    if _case_arg_8_row['mode'] == 'lookup':
                        lookup = _case_arg_8_row['lookup']
                        name_or_path = str(profile) if lookup == 'path' else 'alpha' if lookup == 'name' else lookup
                        _case_value_8 = ((name_or_path,), {'release_root': release})
                        break
                    selection = _case_arg_8_row.get('select')
                    _case_value_8 = ((repo, str(selection) if selection is not None else None), {'release_root': release})
                    break
                    break
                yield _case_value_8

    def get_output_data(self):
        """Compare against the independently specified authorized Profile identity."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        _DATA = Path(__file__).resolve().parents[1] / 'tests/cases/profile-selection.json'
        while True:
            _case_value_9 = json.loads(_DATA.read_text(encoding='utf-8'))['cases']
            break
            break
        for row in _case_value_9:
            if row['mode'] == 'lookup' and 'error' not in row['expected']:
                yield row['expected']

    def equals(self, expected, actual):
        """Require the authorized Profile and its original declared identity."""
        return {'name': actual.name} == expected

class ProfileSealedLookupInvalidCase(TestCase):
    """Disallow escaping an installed policy using unapproved Profile names."""

    def __init__(self):
        """Use genuine framework exception semantics instead of a wrapper."""
        super().__init__(type=Outcome.EXCEPTION)

    def get_enterpoint(self):
        """Invoke public installed policy lookup directly."""
        return get_project_profile

    def get_input_data(self):
        """Use fixed unauthorized policy lookups without running the API."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        _DATA = Path(__file__).resolve().parents[1] / 'tests/cases/profile-selection.json'
        while True:
            _case_value_10 = json.loads(_DATA.read_text(encoding='utf-8'))['cases']
            break
            break
        for row in _case_value_10:
            if row['mode'] == 'lookup' and 'error' in row['expected']:
                _case_arg_11_row = row
                while True:
                    root = Path(tempfile.mkdtemp(prefix='rqg-profile-selection-v02-'))
                    atexit.register(shutil.rmtree, root, ignore_errors=True)
                    release = root / 'release'
                    repo = root / _case_arg_11_row.get('repo_name', 'repo')
                    repo.mkdir()
                    (release / 'runtime').mkdir(parents=True)
                    installed = bool(_case_arg_11_row.get('installed'))
                    (release / 'runtime' / 'RELEASE.lock').write_text('schema=repository-quality-guard/release-v1\nversion=0.20.1\n' + 'distribution=' + ('agents' if installed else 'skill') + '\n' + 'manifest_sha256=' + '0' * 64 + '\nprotected_file_count=0\n', encoding='utf-8')
                    if installed:
                        name = _case_arg_11_row.get('installed_name', 'alpha')
                        installed_root = release / 'installed'
                        installed_root.mkdir()
                        (installed_root / 'POLICY.json').write_text(json.dumps({'schema': 'repository-quality-guard/installed-policy-v1', 'profile_name': name, 'profile_version': '1' if name else '', 'profile_source': 'explicit-cli' if name else 'generic'}), encoding='utf-8')
                        profile = installed_root / 'profile'
                    else:
                        name = 'alpha'
                        profile = release / 'profiles' / name
                    if name:
                        profile.mkdir(parents=True)
                        (profile / 'profile.json').write_text(json.dumps({'schema': 'repository-quality-guard/profile-v1', 'name': name, 'entrypoint': 'extension.py'}), encoding='utf-8')
                        (profile / 'extension.py').write_text("from runtime.src.profile_api import QualityGuardProfile\nclass Profile(QualityGuardProfile):\n    name='alpha'\n", encoding='utf-8')
                    if _case_arg_11_row.get('marker'):
                        (repo / 'agents').mkdir()
                        (repo / 'agents' / 'react_agent.py').write_text('# not a Profile selection signal\n', encoding='utf-8')
                    if _case_arg_11_row['mode'] == 'lookup':
                        lookup = _case_arg_11_row['lookup']
                        name_or_path = str(profile) if lookup == 'path' else 'alpha' if lookup == 'name' else lookup
                        _case_value_11 = ((name_or_path,), {'release_root': release})
                        break
                    selection = _case_arg_11_row.get('select')
                    _case_value_11 = ((repo, str(selection) if selection is not None else None), {'release_root': release})
                    break
                    break
                yield _case_value_11

    def get_output_data(self):
        """Require the authored identity-boundary ValueError."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        _DATA = Path(__file__).resolve().parents[1] / 'tests/cases/profile-selection.json'
        while True:
            _case_value_12 = json.loads(_DATA.read_text(encoding='utf-8'))['cases']
            break
            break
        for row in _case_value_12:
            if row['mode'] == 'lookup' and 'error' in row['expected']:
                yield ValueError

    def equals(self, expected, actual):
        """Reject silence and unrelated error types."""
        return type(actual) is expected
