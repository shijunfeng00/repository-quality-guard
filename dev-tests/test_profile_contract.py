from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.profile_build import build_profile_lock
from runtime.src.profile_api import Finding, QualityGuardProfile, QualityRule, RulePack, RuleContext, SearchStrategy
from runtime.src.project_profiles import load_quality_profile
from runtime.src.config import GuardConfig, is_test_path
from runtime.src.gate_status import code_status
from runtime.src.model import ScanReport

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


    def test_public_example_profile_is_executable_and_keeps_policy_in_json(self) -> None:
        profile_dir = ROOT / "profiles" / "qg-example-profile"
        profile = load_quality_profile(str(profile_dir), release_root=ROOT)
        self.assertIsNotNone(profile)
        assert profile is not None
        snapshot = profile.build()

        self.assertEqual(snapshot.rule_levels["QG203"], "blocker")
        self.assertEqual(snapshot.rule_levels["QG205"], "semantic")
        self.assertIn("QG187", snapshot.disabled_rules)
        self.assertEqual(
            snapshot.nonblocking_paths, ("tests/**", "tools/tracing.py")
        )
        self.assertEqual(len(snapshot.stable_mapping_contracts), 1)
        self.assertEqual(len(snapshot.callable_contracts), 2)
        self.assertEqual(
            snapshot.stable_mapping_contracts[0].qualname, "RuntimeState"
        )
        self.assertEqual(
            snapshot.callable_contracts[0].qualname, "ModelAdapter.generate"
        )

        extension = (profile_dir / "extension.py").read_text(encoding="utf-8")
        self.assertNotIn("set_rule_level", extension)
        self.assertNotIn("disable_rule", extension)
        self.assertNotIn("add_nonblocking_path", extension)

    def test_geek_ai_agent_nonblocking_policy_is_declared_only_in_json(self) -> None:
        profile_dir = ROOT / "profiles" / "geek-ai-agent"
        built = load_quality_profile(str(profile_dir))
        self.assertIsNotNone(built)
        snapshot = built.build() if built is not None else None
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.nonblocking_paths, ("tests/**", "utils/tracing.py"))
        self.assertEqual(snapshot.test_baseline_passthrough_paths, ("utils/tracing.py",))
        extension = (profile_dir / "extension.py").read_text(encoding="utf-8")
        self.assertNotIn("add_nonblocking_path", extension)

    def test_agent_unit_tests_and_tracing_path_are_nonblocking_but_still_classified(self) -> None:
        profile = load_quality_profile(str(ROOT / "profiles" / "geek-ai-agent"))
        self.assertIsNotNone(profile)
        assert profile is not None
        snapshot = profile.build()
        GuardConfig().with_project_profile(snapshot)

        self.assertTrue(is_test_path("tests/unit/test_runtime.py", "geek-ai-agent"))
        self.assertTrue(is_test_path("utils/tracing.py", "geek-ai-agent"))

        findings = [
            Finding(
                "QG183",
                "critical",
                "high",
                "tests/unit/test_runtime.py",
                1,
                1,
                "unit-test reflection",
            ),
            Finding(
                "QG186",
                "critical",
                "high",
                "utils/tracing.py",
                1,
                1,
                "trace hook patch",
            ),
        ]
        report = ScanReport(
            root=Path("."),
            files_scanned=2,
            findings=findings,
            definitions=0,
            project_name="geek-ai-agent",
            baseline=object(),  # type: ignore[arg-type]
        )
        self.assertEqual(code_status(report), "ACCEPT")

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


    def test_agents_file_is_manifest_driven(self) -> None:
        class Profile(QualityGuardProfile):
            name = "sample"

        built = Profile(manifest={"agents_file": "AGENTS.md"}).build()
        self.assertEqual(built.agents_file, "AGENTS.md")

    def test_finding_is_exported_from_stable_profile_api(self) -> None:
        finding = Finding(
            code="QG10000",
            severity="warning",
            confidence="high",
            path="x.py",
            line=1,
            column=1,
            message="example",
        )
        self.assertEqual(finding.code, "QG10000")

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
