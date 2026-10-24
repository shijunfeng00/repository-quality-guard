"""Real native compiler evidence through the public multi-language producer."""
from __future__ import annotations

import atexit
import json
import shutil
import tempfile
from pathlib import Path

from rqgtest import TestCase
from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
from runtime.src.config import GuardConfig
from runtime.src.multilang import multilang_findings


class CppNativeSyntaxCase(TestCase):
    """Check real GCC syntax outcomes and authored compilation flags."""

    def get_enterpoint(self):
        """Return the production multi-language Finding producer."""
        return multilang_findings

    def get_input_data(self):
        """Create authoritative native build inputs without invoking the producer."""
        records = json.loads(
            (Path(__file__).parent / "cases/cpp-native-syntax.json").read_text(
                encoding="utf-8"
            )
        )["cases"]
        compiler = shutil.which("g++")
        if compiler is None:
            raise RuntimeError("Native GCC fixture requires g++")
        for row in records:
            root = Path(tempfile.mkdtemp(prefix="rqg-native-contract-"))
            atexit.register(shutil.rmtree, root, ignore_errors=True)
            source = root / "src/sample.cpp"
            source.parent.mkdir()
            source.write_text(row["source"], encoding="utf-8")
            build = root / "build"
            build.mkdir()
            (build / "compile_commands.json").write_text(
                json.dumps(
                    [
                        {
                            "directory": str(build),
                            "file": str(source),
                            "arguments": [
                                compiler,
                                *row["flags"],
                                "-c",
                                str(source),
                                "-o",
                                "sample.o",
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            snapshot = RepositoryAnalysisSnapshot(
                root,
                "DIRECTORY",
                {},
                {"src/sample.cpp": LanguageUnit("src/sample.cpp", "cpp", row["source"])},
            )
            yield ((snapshot, GuardConfig()), {})

    def get_output_data(self):
        """Return independently authored Finding, compiler and coverage Oracles."""
        records = json.loads(
            (Path(__file__).parent / "cases/cpp-native-syntax.json").read_text(
                encoding="utf-8"
            )
        )["cases"]
        for row in records:
            yield row

    def equals(self, expected, actual):
        """Reject additional findings and missing native compiler observations."""
        findings, summary = actual
        failures = summary["cpp_native_failures"]
        observed = {
            "findings": [[item.code, item.severity, item.path] for item in findings],
            "native_failures": [
                [
                    item["path"], item["compiler_family"], item["returncode"],
                    expected["diagnostic_marker"] in item["stderr"],
                    expected["command_flag"] in item["command"],
                ]
                for item in failures
            ],
            "native_syntax": summary["cpp_native_syntax"],
            "compiler_families": summary["cpp_compiler_families"],
            "build_contracts": summary["cpp_build_contracts"],
            "translation_units": summary["cpp_translation_units"],
            "missing_contracts": summary["cpp_missing_native_build_contracts"],
            "native_unavailable": summary["cpp_native_unavailable"],
        }
        return observed == expected["expected"]
