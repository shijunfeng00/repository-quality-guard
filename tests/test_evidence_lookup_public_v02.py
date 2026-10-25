"""Real evidence-lookup CLI contracts migrated from the frozen UT47 source ledger."""

from __future__ import annotations

from tests_support.evidence_lookup_cli import run_evidence_lookup_case

import json
from pathlib import Path

from rqgtest import TestCase




class EvidenceLookupCliCase(TestCase):
    """Run every immutable source row through the real CLI and its authored output Oracle."""

    def get_enterpoint(self):
        """Use the real CLI execution adapter, including true subprocess cases."""
        return run_evidence_lookup_case

    def get_input_data(self):
        """Provide only command inputs; expected-output fields are excluded."""
        root = Path(__file__).resolve().parents[1]
        document = json.loads(
            (root / "tests/cases/release/qg-evidence-lookup-cli-v1.json").read_text(
                encoding="utf-8"
            )
        )
        for row in document["cases"]:
            payload = {
                "case_id": row["case_id"],
                "args": row["args"],
                "exec": row.get("exec"),
                "source": row.get("source", document["source"]),
                "extra_files": row.get("extra_files", {}),
                "git_snapshot": row.get("git_snapshot", False),
            }
            yield ((payload,), {})

    def get_output_data(self):
        """Keep the original exit/stdout/stderr/section/artifact expectations independent."""
        root = Path(__file__).resolve().parents[1]
        document = json.loads(
            (root / "tests/cases/release/qg-evidence-lookup-cli-v1.json").read_text(
                encoding="utf-8"
            )
        )
        keys = (
            "code",
            "stdout_empty",
            "contains",
            "absent",
            "stderr_contains",
            "before_section_absent",
            "artifacts",
        )
        for row in document["cases"]:
            yield {key: row[key] for key in keys if key in row}

    def equals(self, expected, actual):
        """Check all frozen output constraints without comparing nondeterministic temp paths."""
        stdout, stderr = str(actual["stdout"]), str(actual["stderr"])
        if actual["code"] != expected["code"]:
            return False
        if expected.get("stdout_empty", False) and stdout:
            return False
        if any(value not in stdout for value in expected.get("contains", [])):
            return False
        if any(value in stdout for value in expected.get("absent", [])):
            return False
        if any(value not in stderr for value in expected.get("stderr_contains", [])):
            return False
        for bounded in expected.get("before_section_absent", []):
            if bounded["section"] not in stdout:
                prefix = stdout
            else:
                prefix = stdout.split(bounded["section"], 1)[0]
            if bounded["value"] in prefix:
                return False
        if any(
            name not in actual["artifacts"] for name in expected.get("artifacts", [])
        ):
            return False
        return True
