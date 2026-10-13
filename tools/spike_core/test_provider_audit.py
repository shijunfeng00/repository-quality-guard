"""Functional contracts for compiler evidence, including renaming invariance."""

from __future__ import annotations

import os
import hashlib
import pathlib
import tempfile
import unittest

from tools.spike_core.provider_audit import collect, triage


class ProviderAuditTests(unittest.TestCase):
    def test_review_keeps_all_native_findings_and_marks_missing_import_context(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            path = root / "source.py"
            path.write_text(
                "import missing_dependency\n"
                "value = missing_dependency.convert(1)\n"
                "task = lambda: value\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries = [
                (1, "reportMissingImports"),
                (2, "reportCallIssue"),
                (3, "reportPossiblyUnboundVariable"),
            ]
            report = {
                "schema": "rqg.compiler_observation.v1",
                "provider": "pyright",
                "version": "fixture",
                "source_inventory_sha256": "fixture",
                "observations": [
                    {
                        "source_path": "source.py",
                        "source_sha256": digest,
                        "line": line,
                        "native_rule": rule,
                        "native_severity": "error",
                        "message": rule,
                    }
                    for line, rule in entries
                ],
            }
            result = triage(report, root)
            self.assertEqual(result["total"], 3)
            self.assertEqual(
                result["groups"],
                {
                    "COMPILER_CONTRACT_WITH_MISSING_IMPORTS": 2,
                    "ENVIRONMENT_INCOMPLETE": 1,
                },
            )
            self.assertEqual(result["rows"][2]["context"][-1]["line"], 3)
            self.assertTrue(
                all(
                    row["status"] == "UNVERIFIED_SOURCE_CANDIDATE"
                    for row in result["rows"]
                )
            )
            path.write_text("pass\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale compiler diagnostic"):
                triage(report, root)

    @unittest.skipUnless(
        os.environ.get("RQG_TEST_PYRIGHT_ENTRY"), "offline entry absent"
    )
    def test_compiler_owns_semantics_and_rename_is_invariant(self) -> None:
        entry = pathlib.Path(os.environ["RQG_TEST_PYRIGHT_ENTRY"])
        pairs = []
        with tempfile.TemporaryDirectory() as temporary:
            for index, (call, field) in enumerate(
                (("apply", "payload"), ("inject", "blob"))
            ):
                root = pathlib.Path(temporary) / f"p{index}"
                root.mkdir()
                (root / "src.py").write_text(
                    f"def {call}({field}: int) -> int:\n    return {field}\n"
                    f"x = {call}('wrong-type')\n"
                )
                result = collect(root, entry)
                matches = [
                    o
                    for o in result["observations"]
                    if o["evidence_family"].startswith("R1_")
                ]
                self.assertTrue(matches, result["observations"])
                self.assertEqual(result["rqg_blockers"], 0)
                self.assertEqual(result["rqg_formal_proofs"], 0)
                self.assertEqual(
                    matches[0]["proof_capability"], "TYPECHECKER_DIAGNOSTIC_ONLY"
                )
                pairs.append(
                    [(m["native_rule"], m["evidence_family"]) for m in matches]
                )
            self.assertEqual(*pairs)


if __name__ == "__main__":
    unittest.main()
