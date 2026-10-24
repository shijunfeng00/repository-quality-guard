"""Native compiler failures remain build facts, not quality findings."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.multilang import multilang_findings


class NativeBuildObservationCase(TestCase):
    """Exercise actual authored bad C++ with a genuine compiler database."""

    def get_enterpoint(self):
        return multilang_findings

    def get_input_data(self):
        compiler = shutil.which("g++")
        if compiler is None:
            raise RuntimeError("g++ required for this native build-gate regression")
        with tempfile.TemporaryDirectory(prefix="rqg-build-failure-") as temp:
            root = Path(temp)
            (root / "invalid.cpp").write_text("int broken( {\n", encoding="utf-8")
            (root / "compile_commands.json").write_text(
                json.dumps([{"directory": str(root), "file": str(root / "invalid.cpp"),
                             "arguments": [compiler, "-std=c++17", "-c", str(root / "invalid.cpp")]}]),
                encoding="utf-8",
            )
            yield ((directory_analysis_snapshot(root, GuardConfig()), GuardConfig()), {})

    def get_output_data(self):
        yield {"native_fail": 1, "quality_findings": 0}

    def equals(self, expected, output):
        findings, summary = output
        return (summary["cpp_native_syntax"].get("fail") == expected["native_fail"]
                and len(findings) == expected["quality_findings"])
