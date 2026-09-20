from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from runtime.src.config import GuardConfig
from runtime.src.release_identity_rules import release_identity_findings
from runtime.src.semantic_review import annotate_semantic_review


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _release_token() -> str:
    return "v" + "2.8" + "-" + "rc" + "3"


class TestReleaseIdentityRules(unittest.TestCase):
    def _repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        _git(root, "init", "-q")
        _git(root, "config", "user.name", "QG Test")
        _git(root, "config", "user.email", "qg@example.invalid")
        return temp, root

    def _scan(self, root: Path):
        _git(root, "add", ".")
        return release_identity_findings(root, GuardConfig())

    def test_readme_is_the_only_documentation_exemption(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        token = _release_token()
        (root / "README.md").write_text(f"release {token}\n", encoding="utf-8")
        (root / "ARCHITECTURE.md").write_text(f"frozen at {token}\n", encoding="utf-8")

        findings = self._scan(root)

        self.assertFalse(any(item.path == "README.md" for item in findings))
        self.assertTrue(any(item.code == "QG205" and item.path == "ARCHITECTURE.md" for item in findings))

    def test_release_bound_filename_is_generic_critical(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        tests = root / "tests"
        tests.mkdir()
        filename = "test_v28_" + "rc" + "3_graph_backend.py"
        (tests / filename).write_text("def test_graph_backend():\n    assert True\n", encoding="utf-8")

        findings = self._scan(root)
        matched = [item for item in findings if item.code == "QG203"]

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].severity, "critical")
        self.assertEqual(matched[0].evidence.get("mechanism"), "path-release-identity")

    def test_release_identity_inside_test_data_is_semantic_only(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        tests = root / "tests"
        tests.mkdir()
        marker = "rc" + "5-ticket"
        (tests / "test_contract.py").write_text(
            "def test_contract():\n"
            f"    session = {marker!r}\n"
            "    dependency = 'agent-dsl-core==0.3.0'\n"
            "    assert session and dependency\n",
            encoding="utf-8",
        )

        findings = self._scan(root)

        self.assertFalse(any(item.code == "QG203" for item in findings))
        semantic = [item for item in findings if item.code == "QG205"]
        self.assertTrue(semantic)
        self.assertTrue(all(item.severity == "info" for item in semantic))

    def test_fixed_digest_is_semantic_candidate(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        digest = "a" * 64
        (root / "integrity.py").write_text(
            f"EXPECTED_SHA256 = {digest!r}\n",
            encoding="utf-8",
        )

        findings = self._scan(root)
        matched = [item for item in findings if item.code == "QG205"]

        self.assertTrue(matched)
        kinds = {row["kind"] for row in matched[0].evidence.get("matches", [])}
        self.assertIn("digest", kinds)

    def test_api_version_control_flow_is_not_static_qg203(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        api_version = "v" + "1.0"
        (root / "client.py").write_text(
            "def encode(api_version, payload):\n"
            f"    if api_version == {api_version!r}:\n"
            "        return {'legacy_payload': payload}\n"
            "    return {'payload': payload}\n",
            encoding="utf-8",
        )

        findings = self._scan(root)

        self.assertFalse(any(item.code == "QG203" for item in findings))
        self.assertTrue(any(item.code == "QG205" for item in findings))

    def test_plain_dependency_version_requires_semantic_review(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.11"\ndependencies = ["pydantic>=2.8,<3"]\n',
            encoding="utf-8",
        )

        findings = self._scan(root)

        self.assertFalse(any(item.code == "QG203" for item in findings))
        self.assertTrue(any(item.code == "QG205" and item.path == "pyproject.toml" for item in findings))


    def test_minified_html_visual_decimals_are_not_versions(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "dashboard.html").write_text(
            '<style>.card{line-height:1.4;opacity:.52}</style>'
            '<script>const versionLabel="current"; if(x>1.2){y=.5}</script>'
            '<svg stroke-width="1.2" viewBox="0 0 21.8625 11.0292"></svg>\n',
            encoding="utf-8",
        )

        findings = self._scan(root)

        self.assertFalse(any(item.code == "QG205" for item in findings))

    def test_two_part_api_version_requires_local_context(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "client.py").write_text(
            "API_VERSION = '1.2'\nthreshold = 1.4\n", encoding="utf-8"
        )

        findings = self._scan(root)
        row = next(item for item in findings if item.code == "QG205")
        tokens = [item["token"] for item in row.evidence.get("matches", [])]

        self.assertIn("1.2", tokens)
        self.assertNotIn("1.4", tokens)

    def test_requirements_two_part_version_is_semantic_candidate(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "requirements.txt").write_text("pydantic>=2.8\n", encoding="utf-8")

        findings = self._scan(root)

        self.assertTrue(any(item.code == "QG205" for item in findings))

    def test_three_part_semver_does_not_generate_two_part_submatch(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "config.toml").write_text('dependency = "lib==0.20.1"\n', encoding="utf-8")

        findings = self._scan(root)
        row = next(item for item in findings if item.code == "QG205")
        tokens = [item["token"] for item in row.evidence.get("matches", [])]

        self.assertIn("0.20.1", tokens)
        self.assertNotIn("20.1", tokens)
        self.assertNotIn("0.20", tokens)

    def test_git_tag_reference_is_semantically_audited(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        token = _release_token()
        (root / "policy.toml").write_text(f"expected_tag = {token!r}\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "base")
        _git(root, "tag", token)

        findings = release_identity_findings(root, GuardConfig())
        semantic = next(item for item in findings if item.code == "QG205")

        self.assertIn(token, semantic.evidence.get("git_tags_checked", []))

    def test_non_git_directory_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "api.py").write_text("API_VERSION = '1.2.3'\n", encoding="utf-8")

            findings = release_identity_findings(root, GuardConfig())

            self.assertTrue(any(item.code == "QG205" for item in findings))

    def test_both_codes_are_semantic_review_routable_when_present(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        token = _release_token()
        path = root / ("artifact_" + token + ".txt")
        path.write_text(f"release={token}\n", encoding="utf-8")

        findings = annotate_semantic_review(list(self._scan(root)))
        routed = [item for item in findings if item.code in {"QG203", "QG205"}]

        self.assertEqual({item.code for item in routed}, {"QG203", "QG205"})
        self.assertTrue(all(item.evidence.get("semantic_review_required") is True for item in routed))


if __name__ == "__main__":
    unittest.main()
