from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.profile_build import build_profile_lock
from runtime.src.profile_api import QualityGuardProfile, QualityRule, RulePack, RuleContext, SearchStrategy
from runtime.src.project_profiles import load_quality_profile

ROOT = Path(__file__).resolve().parents[1]


class _EmptyRule(QualityRule):
    key = "tests.empty"

    def evaluate(self, ctx: RuleContext):
        return ()


class _Pack(RulePack):
    def register(self, profile: QualityGuardProfile) -> None:
        profile.add_capability("packed")


class TestProfileContract(unittest.TestCase):
    def test_configure_is_explicit_and_super_applies_manifest(self) -> None:
        class Profile(QualityGuardProfile):
            name = "sample"

            def configure(self) -> None:
                super().configure()
                self.add_capability("python")

        profile = Profile(
            manifest={
                "rules": {"disable": ["QG173"]},
                "capabilities": ["base"],
                "nonblocking_paths": ["tests/**"],
                "test_baseline_passthrough_paths": ["utils/tracing.py"],
            }
        )
        self.assertFalse(profile._configured)
        built = profile.build()
        self.assertTrue(profile._configured)
        self.assertEqual(built.disabled_rules, ("QG173",))
        self.assertEqual(built.capabilities, ("base", "python"))
        self.assertEqual(built.nonblocking_paths, ("tests/**",))
        self.assertEqual(built.test_baseline_passthrough_paths, ("utils/tracing.py",))

    def test_test_baseline_passthrough_is_independent_from_nonblocking(self) -> None:
        class Profile(QualityGuardProfile):
            name = "sample"

        built = Profile(
            manifest={
                "nonblocking_paths": ["tests/**", "utils/tracing.py"],
                "test_baseline_passthrough_paths": ["utils/tracing.py"],
            }
        ).build()
        self.assertEqual(built.nonblocking_paths, ("tests/**", "utils/tracing.py"))
        self.assertEqual(built.test_baseline_passthrough_paths, ("utils/tracing.py",))

    def test_integrity_rules_are_not_suppressible(self) -> None:
        profile = QualityGuardProfile(manifest={"name": "sample"})
        with self.assertRaises(ValueError):
            profile.disable_rule("QG990")
        with self.assertRaises(ValueError):
            profile.disable_rule("QG984")

    def test_rule_pack_is_oop_and_explicit(self) -> None:
        profile = QualityGuardProfile(manifest={"name": "sample"})
        profile.include(_Pack())
        self.assertEqual(profile.build().capabilities, ("packed",))
        with self.assertRaises(TypeError):
            profile.include(object())  # type: ignore[arg-type]

    def test_default_search_strategy_is_identity(self) -> None:
        rows = [object(), object()]
        self.assertEqual(list(SearchStrategy().rerank("q", rows, root=ROOT)), rows)

    def test_profile_module_export_is_fixed_as_Profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / "profile.json").write_text(
                json.dumps({"schema": "repository-quality-guard/profile-v1", "name": "x", "entrypoint": "extension.py:Other"}),
                encoding="utf-8",
            )
            (p / "extension.py").write_text(
                "from runtime.src.profile_api import QualityGuardProfile\nclass Profile(QualityGuardProfile):\n    name='x'\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_quality_profile(str(p), release_root=ROOT)

    def test_custom_rule_codes_are_frozen_and_not_recycled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / "profile.json").write_text(
                json.dumps({"schema": "repository-quality-guard/profile-v1", "name": "x", "entrypoint": "extension.py"}),
                encoding="utf-8",
            )
            (p / "extension.py").write_text(
                "from runtime.src.profile_api import QualityGuardProfile, QualityRule\n"
                "class RuleA(QualityRule):\n    key='x.a'\n    def evaluate(self, ctx): return ()\n"
                "class Profile(QualityGuardProfile):\n    name='x'\n    def configure(self):\n        super().configure()\n        self.add_rule(RuleA)\n",
                encoding="utf-8",
            )
            first = build_profile_lock(p)
            self.assertEqual(first["rule_codes"]["x.a"], "QG10000")
            (p / "extension.py").write_text(
                "from runtime.src.profile_api import QualityGuardProfile, QualityRule\n"
                "class RuleB(QualityRule):\n    key='x.b'\n    def evaluate(self, ctx): return ()\n"
                "class Profile(QualityGuardProfile):\n    name='x'\n    def configure(self):\n        super().configure()\n        self.add_rule(RuleB)\n",
                encoding="utf-8",
            )
            second = build_profile_lock(p)
            self.assertEqual(second["rule_codes"]["x.a"], "QG10000")
            self.assertEqual(second["rule_codes"]["x.b"], "QG10001")
            self.assertEqual(second["active_rule_keys"], ["x.b"])


if __name__ == "__main__":
    unittest.main()

class TestTestQualityProjection(unittest.TestCase):
    def test_nonblocking_paths_do_not_expand_test_baseline_projection(self) -> None:
        from runtime.src.cli import _filter_test_quality_findings
        from runtime.src.model import Finding

        findings = [
            Finding("QG190", "critical", "high", "tests/conftest.py", 1, 1, "dynamic import"),
            Finding("QG003", "error", "high", "utils/tracing.py", 2, 1, "mapping access"),
            Finding("QG149", "warning", "high", "tests/test_x.py", 3, 1, "private member"),
        ]
        projected = _filter_test_quality_findings(findings, ("utils/tracing.py",))
        self.assertEqual(
            [(item.code, item.path) for item in projected],
            [("QG003", "utils/tracing.py"), ("QG149", "tests/test_x.py")],
        )
