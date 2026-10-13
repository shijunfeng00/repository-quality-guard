"""Real snapshot coverage: missing C++ build truth must not be reported as PASS."""

from __future__ import annotations

import json

from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
from runtime.src.config import GuardConfig
from runtime.src.gate_status import code_status
from runtime.src.model import ScanReport
from runtime.src.compact_report import render_compact_report
from runtime.src.multilang import multilang_findings


def _snapshot(root, paths):
    return RepositoryAnalysisSnapshot(
        root,
        "DIRECTORY",
        {},
        {
            path: LanguageUnit(path, "cpp", (root / path).read_text(encoding="utf-8") if (root / path).is_file() else "int operation() { return 1; }\n")
            for path in paths
        },
    )


def test_missing_cpp_contract_reports_one_aggregated_coverage_gap(tmp_path):
    snapshot = _snapshot(
        tmp_path,
        ("src/one.cpp", "src/two.cc", "src/three.cxx", "inc/api.hpp"),
    )
    findings, summary = multilang_findings(snapshot, GuardConfig())
    gaps = [finding for finding in findings if finding.code == "QG208"]
    assert len(gaps) == 1
    assert gaps[0].severity == "info"
    assert gaps[0].evidence["missing_translation_units"] == 3
    assert summary["cpp_translation_units"] == 3
    assert summary["cpp_missing_native_build_contracts"] == 3
    assert summary["cpp_semantic_coverage"] == "NOT_MEASURED"
    report = ScanReport(tmp_path, 0, findings, 0)
    assert code_status(report) == "REVIEW_REQUIRED"


def test_missing_native_coverage_is_visible_in_official_compact_report(tmp_path):
    """Coverage gaps must appear in the actual report, not only scanner internals."""
    snapshot = _snapshot(tmp_path, ("src/first.cpp", "src/second.cpp"))
    findings, _ = multilang_findings(snapshot, GuardConfig())
    report = ScanReport(tmp_path, 0, findings, 0)
    document = render_compact_report(report, revision="")
    native = document.split("<!-- RQG:AUTO:BEGIN native_cpp_diagnostics -->", 1)[1]
    native = native.split("<!-- RQG:AUTO:END native_cpp_diagnostics -->", 1)[0]
    assert "| `QG208` |" in native
    assert "2 translation units" in native
    assert "src/first.cpp" in native
    assert "NOT_MEASURED" in native
    assert "`QG212`" not in native


def test_vendor_and_header_only_do_not_fabricate_translation_units(tmp_path):
    snapshot = _snapshot(tmp_path, ("vendor/dependency.cpp", "inc/api.hpp"))
    findings, summary = multilang_findings(snapshot, GuardConfig())
    assert not any(finding.code == "QG208" for finding in findings)
    assert summary["cpp_translation_units"] == 0


def test_authored_compilation_database_covers_correct_translation_unit(tmp_path):
    source = tmp_path / "src" / "real.cpp"
    source.parent.mkdir()
    source.write_text("int operation() { return 1; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(source),
                    "arguments": ["/usr/bin/g++", "-std=c++20", "-c", str(source)],
                }
            ]
        ),
        encoding="utf-8",
    )
    snapshot = _snapshot(tmp_path, ("src/real.cpp", "src/not_in_database.cpp"))
    findings, summary = multilang_findings(snapshot, GuardConfig())
    gaps = [finding for finding in findings if finding.code == "QG208"]
    assert len(gaps) == 1
    assert gaps[0].evidence["missing_paths"] == ("src/not_in_database.cpp",)
    assert summary["cpp_build_contracts"] == 1
    assert summary["cpp_missing_native_build_contracts"] == 1


def test_complete_build_contract_does_not_claim_native_tests_passed(tmp_path):
    source = tmp_path / "one.cpp"
    source.write_text("int operation() { return 1; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(source),
                    "arguments": ["/usr/bin/g++", "-c", str(source)],
                }
            ]
        ),
        encoding="utf-8",
    )
    findings, summary = multilang_findings(
        _snapshot(tmp_path, ("one.cpp",)), GuardConfig()
    )
    assert not any(finding.code == "QG208" for finding in findings)
    assert summary["cpp_missing_native_build_contracts"] == 0
    assert summary["cpp_semantic_coverage"] == "NOT_MEASURED"


def test_native_compiler_failure_is_reported_as_qg_not_just_external_oracle(tmp_path):
    """The production scanner must own the failure and its source-bound evidence."""
    import shutil
    import pytest

    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ unavailable: cannot prove native compiler execution")
    source = tmp_path / "src" / "broken.cpp"
    source.parent.mkdir()
    body = "int broken() { return unknown_name; }\n"
    source.write_text(body, encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{
            "directory": str(tmp_path),
            "file": str(source),
            "arguments": [compiler, "-std=c++17", "-c", str(source)],
        }]), encoding="utf-8",
    )
    findings, summary = multilang_findings(_snapshot(tmp_path, ("src/broken.cpp",)), GuardConfig())
    syntax = [finding for finding in findings if finding.code == "QG212"]
    assert len(syntax) == 1
    assert syntax[0].severity == "error"
    assert "unknown_name" in syntax[0].evidence["stderr"]
    assert "-std=c++17" in syntax[0].evidence["command"]
    assert summary["cpp_native_syntax"] == {"fail": 1}
    assert code_status(ScanReport(tmp_path, 0, findings, 0)) == "REVIEW_REQUIRED"


def test_native_compiler_success_does_not_fabricate_qg212(tmp_path):
    """A valid authored C++ standard must be preserved by automatic detection."""
    import shutil
    import pytest

    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ unavailable: cannot prove native compiler execution")
    source = tmp_path / "good.cpp"
    source.write_text("int good() { return 1; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{
            "directory": str(tmp_path),
            "file": str(source),
            "arguments": [compiler, "-std=c++14", "-c", str(source)],
        }]), encoding="utf-8",
    )
    findings, summary = multilang_findings(_snapshot(tmp_path, ("good.cpp",)), GuardConfig())
    assert not any(finding.code in {"QG208", "QG212"} for finding in findings)
    assert summary["cpp_native_syntax"] == {"pass": 1}


def test_native_compiler_unavailable_reports_qg208_without_fallback_std(tmp_path):
    """Toolchain absence is UNKNOWN and must not be disguised as successful parsing."""
    source = tmp_path / "src" / "ordinary.cpp"
    source.parent.mkdir()
    source.write_text("int operation() { return 1; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{
            "directory": str(tmp_path),
            "file": str(source),
            "arguments": ["g++-compiler-not-installed", "-std=c++11", "-c", str(source)],
        }]), encoding="utf-8",
    )
    findings, summary = multilang_findings(_snapshot(tmp_path, ("src/ordinary.cpp",)), GuardConfig())
    assert [item.code for item in findings] == ["QG208"]
    assert summary["cpp_native_unavailable"] == 1
