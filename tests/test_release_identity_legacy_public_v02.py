"""Real QG203/QG205 release-token evidence from legacy independent source fixtures."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.release_identity_rules import release_identity_findings

class ReleaseIdentityLegacyPublicCase(TestCase):
    """Test distinct release identity paths, tokens, digests and Git-tag boundaries."""

    def get_enterpoint(self):
        """Return the genuine production identity scanner without dispatch wrappers."""
        return release_identity_findings

    def get_input_data(self):
        """Build independent authored repository source facts for each legacy record."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/release-identity-legacy-public-v02.json')))['cases']:
            if 'custom_excludes' not in row and 'annotate' not in row:
                _case_arg_1_row = row
                root = Path(tempfile.mkdtemp(prefix='rqg-release-unit-'))
                atexit.register(shutil.rmtree, root, ignore_errors=True)
                if _case_arg_1_row['git']:
                    subprocess.run(('git', 'init', '-q'), cwd=root, check=True)
                    subprocess.run(('git', 'config', 'user.name', 'Fixture'), cwd=root, check=True)
                    subprocess.run(('git', 'config', 'user.email', 'fixture@example.invalid'), cwd=root, check=True)
                for relative, content in _case_arg_1_row['files'].items():
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding='utf-8')
                if 'git_tag' in _case_arg_1_row:
                    subprocess.run(('git', 'add', '.'), cwd=root, check=True)
                    subprocess.run(('git', 'commit', '-qm', 'baseline'), cwd=root, check=True)
                    subprocess.run(('git', 'tag', _case_arg_1_row['git_tag']), cwd=root, check=True)
                _case_value_1 = ((root, GuardConfig()), {})
                yield _case_value_1

    def get_output_data(self):
        """Preserve the frozen independent expected evidence projections per record."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/release-identity-legacy-public-v02.json')))['cases']:
            if 'custom_excludes' not in row and 'annotate' not in row:
                yield (row['expected'], row['observe'], tuple(row['codes']))

    def equals(self, expected, actual):
        """Match the original observation contract and its complete selected Finding set."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        truth, mode, codes = expected
        _case_arg_1_findings = actual
        _case_arg_1_mode = mode
        _case_arg_1_codes = codes
        while True:
            selected = [finding for finding in _case_arg_1_findings if finding.code in _case_arg_1_codes]
            if _case_arg_1_mode == 'codes-by-path':
                _case_value_1 = sorted(([finding.code, finding.path] for finding in selected))
                break
            if _case_arg_1_mode == 'code-severity-path':
                _case_value_1 = sorted(([finding.code, finding.severity, finding.path] for finding in selected))
                break
            if _case_arg_1_mode == 'severity-mechanism':
                _case_value_1 = sorted(([finding.code, finding.severity, finding.evidence['mechanism']] for finding in selected))
                break
            if _case_arg_1_mode == 'git-tags':
                _case_value_1 = sorted({tag for finding in selected for tag in finding.evidence['git_tags_checked']})
                break
            if _case_arg_1_mode == 'token-kinds':
                _case_value_1 = sorted({token['kind'] for finding in selected for token in finding.evidence['matches']})
                break
            if _case_arg_1_mode == 'tokens':
                _case_value_1 = sorted({token['token'] for finding in selected for token in finding.evidence['matches']})
                break
            raise ValueError('Unsupported original release observation contract: ' + _case_arg_1_mode)
            break
        return _case_value_1 == truth

class ReleaseConfigExclusionCase(TestCase):
    """Retain generated release artifacts in public exclusion configuration."""

    def get_enterpoint(self):
        """Return the actual TOML configuration loader."""
        from runtime.src.config import GuardConfig
        return GuardConfig.load

    def get_input_data(self):
        """Create a declared repository config with its source-authored excluded paths."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/release-identity-legacy-public-v02.json')))['cases']:
            if 'custom_excludes' in row:
                _case_arg_2_row = row
                root = Path(tempfile.mkdtemp(prefix='rqg-config-unit-'))
                atexit.register(shutil.rmtree, root, ignore_errors=True)
                for relative, content in _case_arg_2_row['files'].items():
                    file = root / relative
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_text(content, encoding='utf-8')
                _case_value_2 = ((root,), {})
                yield _case_value_2

    def get_output_data(self):
        """Compare the independently declared configured path membership."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/release-identity-legacy-public-v02.json')))['cases']:
            if 'custom_excludes' in row:
                yield tuple((tuple(item) for item in row['expected']))

    def equals(self, expected, actual):
        """Require explicit source-authored exclusions, not a hardcoded pass."""
        return expected == tuple(((name, name in actual.exclude) for name, ignored in expected))

class ReleaseIdentitySemanticRouteCase(TestCase):
    """Demand semantic review of both independently declared release identity types."""

    def get_enterpoint(self):
        """Call the real production Finding semantic classifier without a wrapper."""
        from runtime.src.semantic_review import annotate_semantic_review
        return annotate_semantic_review

    def get_input_data(self):
        """Pass two authored release identity Findings into the true classifier."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/release-identity-legacy-public-v02.json')))['cases']:
            if 'annotate' in row:
                from runtime.src.model import Finding
                findings = [Finding('QG203', 'critical', 'high', 'artifact_v2.8-rc3.txt', 1, 1, 'identity'), Finding('QG205', 'info', 'medium', 'artifact_v2.8-rc3.txt', 1, 1, 'identity')]
                _case_value_3 = ((findings,), {})
                yield _case_value_3

    def get_output_data(self):
        """Preserve exact independently authored semantic review flags."""
        import atexit
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/release-identity-legacy-public-v02.json')))['cases']:
            if 'annotate' in row:
                yield tuple((tuple(item) for item in row['expected']))

    def equals(self, expected, actual):
        """Reject missing or unexpected routed Finding identities and flags."""
        return expected == tuple(sorted(((f.code, f.evidence['semantic_review_required']) for f in actual)))
