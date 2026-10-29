"""Direct path and source-token release identity rules, not CI integration."""

from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import TestCase
from runtime.src.release_identity_rules import (
    ReleasePathIdentityRule,
    release_identity_findings,
)


class ReleasePathIdentityCase(TestCase):
    """Identify release version tokens present in authored source paths."""

    def get_enterpoint(self):
        """Return the production path token evaluator directly."""
        return ReleasePathIdentityRule.evaluate

    def get_input_data(self):
        """Feed the original explicit path metadata fixtures."""
        from pathlib import Path
        from runtime.src.release_identity_rules import ReleaseIdentityContext
        from runtime.src.config import GuardConfig
        import atexit
        import shutil
        import tempfile

        for row in next(
            iter(JSONDataLoader("tests/cases/release-identity-rules-public.json"))
        )["cases"]:
            if row["rule"] == "QG203":
                _case_arg_1_row = row
                while True:
                    if _case_arg_1_row["rule"] == "QG205":
                        root = Path(tempfile.mkdtemp(prefix="rqg-release-id-unit-"))
                        atexit.register(shutil.rmtree, root, ignore_errors=True)
                        target = root / _case_arg_1_row["path"]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        line = (
                            'version = "1.2.3"'
                            if _case_arg_1_row["token"]
                            else 'version = "stable"'
                        )
                        target.write_text(
                            "# regular fixture\n" + line + "\n", encoding="utf-8"
                        )
                        _case_value_1 = ((root, GuardConfig()), {})
                        break
                    context = ReleaseIdentityContext(
                        relative=_case_arg_1_row["path"],
                        path_token=_case_arg_1_row["token"],
                    )
                    _case_value_1 = ((context,), {})
                    break
                    break
                _rqg_args, _rqg_kwargs = _case_value_1
                yield ((ReleasePathIdentityRule(), *_rqg_args), _rqg_kwargs)

    def get_output_data(self):
        """Retain independent positive and negative Finding projections."""
        for row in next(
            iter(JSONDataLoader("tests/cases/release-identity-rules-public.json"))
        )["cases"]:
            if row["rule"] == "QG203":
                yield tuple((tuple(value) for value in row["expected"]))

    def equals(self, expected, actual):
        """Compare every Finding including the original source line."""
        return expected == tuple(((f.code, f.severity, f.path, f.line) for f in actual))


class ReleaseContentIdentityCase(TestCase):
    """Validate a version token derived from a fixed content fact."""

    def get_enterpoint(self):
        """Return the public whole-source identity detector directly."""
        return release_identity_findings

    def get_input_data(self):
        """Supply the explicit authored content token evidence."""
        from pathlib import Path
        from runtime.src.release_identity_rules import ReleaseIdentityContext
        from runtime.src.config import GuardConfig
        import atexit
        import shutil
        import tempfile

        for row in next(
            iter(JSONDataLoader("tests/cases/release-identity-rules-public.json"))
        )["cases"]:
            if row["rule"] == "QG205":
                _case_arg_2_row = row
                while True:
                    if _case_arg_2_row["rule"] == "QG205":
                        root = Path(tempfile.mkdtemp(prefix="rqg-release-id-unit-"))
                        atexit.register(shutil.rmtree, root, ignore_errors=True)
                        target = root / _case_arg_2_row["path"]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        line = (
                            'version = "1.2.3"'
                            if _case_arg_2_row["token"]
                            else 'version = "stable"'
                        )
                        target.write_text(
                            "# regular fixture\n" + line + "\n", encoding="utf-8"
                        )
                        _case_value_2 = ((root, GuardConfig()), {})
                        break
                    context = ReleaseIdentityContext(
                        relative=_case_arg_2_row["path"],
                        path_token=_case_arg_2_row["token"],
                    )
                    _case_value_2 = ((context,), {})
                    break
                    break
                yield _case_value_2

    def get_output_data(self):
        """Compare independent source-token Finding identities."""
        for row in next(
            iter(JSONDataLoader("tests/cases/release-identity-rules-public.json"))
        )["cases"]:
            if row["rule"] == "QG205":
                yield tuple((tuple(value) for value in row["expected"]))

    def equals(self, expected, actual):
        """Require full path, line, severity and rule identities."""
        return expected == tuple(((f.code, f.severity, f.path, f.line) for f in actual))
