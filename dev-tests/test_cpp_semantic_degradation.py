from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
from runtime.src.config import GuardConfig
from runtime.src.multilang import _cpp_changed_findings


class TestCppSemanticDegradation(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("clang++") or shutil.which("clang"), "clang required"
    )
    def test_base_provider_failure_does_not_turn_target_into_false_qg204(self) -> None:
        clang = shutil.which("clang++") or shutil.which("clang")
        assert clang is not None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "build").mkdir()
            target_source = "int run() {\n    if (1) { return 1; }\n    return 0;\n}\n"
            source_path = root / "src" / "sample.cpp"
            source_path.write_text(target_source, encoding="utf-8")
            (root / "build" / "compile_commands.json").write_text(
                json.dumps(
                    [
                        {
                            "directory": str(root / "build"),
                            "file": str(source_path),
                            "arguments": [
                                str(clang),
                                "-std=c++20",
                                "-c",
                                str(source_path),
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            base = RepositoryAnalysisSnapshot(
                root=root,
                label="BASE_REVISION",
                units={},
                language_units={
                    "src/sample.cpp": LanguageUnit(
                        "src/sample.cpp",
                        "cpp",
                        "#error old compiler-only source\nint run() { return 0; }\n",
                    )
                },
            )
            target = RepositoryAnalysisSnapshot(
                root=root,
                label="DIRECTORY",
                units={},
                language_units={
                    "src/sample.cpp": LanguageUnit(
                        "src/sample.cpp", "cpp", target_source
                    )
                },
            )
            findings = _cpp_changed_findings(
                base,
                target,
                GuardConfig(max_function_lines=1, max_branches=0, max_nesting=0),
            )
            self.assertEqual(findings, [])

    def test_target_semantic_na_short_circuits_base_provider(self) -> None:
        base = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="BASE",
            units={},
            language_units={
                "src/a.cpp": LanguageUnit("src/a.cpp", "cpp", "int run(){return 0;}\n")
            },
        )
        target = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="WORKTREE",
            units={},
            language_units={
                "src/a.cpp": LanguageUnit("src/a.cpp", "cpp", "int run(){return 1;}\n")
            },
        )
        with patch(
            "runtime.src.multilang._cpp_metrics",
            return_value=({}, set()),
        ) as metrics:
            self.assertEqual(_cpp_changed_findings(base, target, GuardConfig()), [])
        metrics.assert_called_once_with(
            target, ["src/a.cpp"], shutil.which("clang++") or shutil.which("clang")
        )

    def test_missing_clang_and_no_compdb_is_semantic_na_not_exception(self) -> None:
        base = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="BASE",
            units={},
            language_units={
                "src/a.cpp": LanguageUnit("src/a.cpp", "cpp", "int run(){return 0;}\n")
            },
        )
        target = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="WORKTREE",
            units={},
            language_units={
                "src/a.cpp": LanguageUnit("src/a.cpp", "cpp", "int run(){return 1;}\n")
            },
        )
        with patch("runtime.src.multilang.shutil.which", return_value=None):
            self.assertEqual(_cpp_changed_findings(base, target, GuardConfig()), [])


if __name__ == "__main__":
    unittest.main()
