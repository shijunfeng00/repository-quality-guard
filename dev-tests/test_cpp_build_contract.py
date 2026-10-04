from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
from runtime.src.cpp_build_contract import (
    CompilerFamily,
    load_compilation_database,
)


class CppBuildContractTests(unittest.TestCase):
    def _snapshot(self, root: Path, label: str = "WORKTREE") -> RepositoryAnalysisSnapshot:
        source = "int main() { return 0; }\n"
        return RepositoryAnalysisSnapshot(
            root=root,
            label=label,
            units={},
            language_units={"src/main.cpp": LanguageUnit("src/main.cpp", "cpp", source)},
        )

    def _write(self, root: Path, entry: dict[str, object]) -> None:
        (root / "build").mkdir(parents=True, exist_ok=True)
        (root / "build" / "compile_commands.json").write_text(
            json.dumps([entry]), encoding="utf-8"
        )

    def test_gcc_arguments_are_preserved_and_native_command_stays_gcc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            source = root / "src" / "main.cpp"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            self._write(
                root,
                {
                    "directory": str(root / "build"),
                    "file": str(source),
                    "arguments": [
                        "/usr/bin/g++",
                        "-DMODE=7",
                        "-I",
                        str(root / "include"),
                        "-std=gnu++20",
                        "-c",
                        str(source),
                        "-o",
                        "main.o",
                    ],
                },
            )
            spec = load_compilation_database(self._snapshot(root))["src/main.cpp"]
            self.assertEqual(spec.family, CompilerFamily.GCC)
            self.assertEqual(spec.compiler, "/usr/bin/g++")
            self.assertIn("-DMODE=7", spec.arguments)
            command = spec.native_syntax_command()
            self.assertEqual(command[0], "/usr/bin/g++")
            self.assertIn("-std=gnu++20", command)
            self.assertIn("-fsyntax-only", command)
            self.assertNotIn("-c", command)
            self.assertNotIn("main.o", command)

    def test_clang_command_is_identified_without_rewriting_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            source = root / "src" / "main.cpp"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            self._write(
                root,
                {
                    "directory": str(root / "build"),
                    "file": str(source),
                    "command": f"clang++ -DCLANG_ONLY -std=c++23 -c {source} -o x.o",
                },
            )
            spec = load_compilation_database(self._snapshot(root))["src/main.cpp"]
            self.assertEqual(spec.family, CompilerFamily.CLANG)
            self.assertIn("-DCLANG_ONLY", spec.arguments)
            self.assertIn("-std=c++23", spec.arguments)

    def test_cache_wrapper_keeps_wrapper_but_classifies_real_compiler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            source = root / "src" / "main.cpp"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            self._write(
                root,
                {
                    "directory": str(root / "build"),
                    "file": str(source),
                    "arguments": ["ccache", "/usr/bin/g++", "-c", str(source)],
                },
            )
            spec = load_compilation_database(self._snapshot(root))["src/main.cpp"]
            self.assertEqual(spec.family, CompilerFamily.GCC)
            self.assertEqual(spec.arguments[:2], ("ccache", "/usr/bin/g++"))

    def test_msvc_contract_is_preserved_without_claiming_native_availability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            source = root / "src" / "main.cpp"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            self._write(
                root,
                {
                    "directory": str(root / "build"),
                    "file": str(source),
                    "arguments": ["cl.exe", "/std:c++20", "/c", str(source), "/Fomain.obj"],
                },
            )
            spec = load_compilation_database(self._snapshot(root))["src/main.cpp"]
            self.assertEqual(spec.family, CompilerFamily.MSVC)
            self.assertEqual(spec.compiler, "cl.exe")
            self.assertIn("/Zs", spec.native_syntax_command())

    def test_revision_snapshot_does_not_borrow_worktree_compilation_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            source = root / "src" / "main.cpp"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            self._write(
                root,
                {
                    "directory": str(root / "build"),
                    "file": str(source),
                    "arguments": ["g++", "-c", str(source)],
                },
            )
            self.assertEqual(load_compilation_database(self._snapshot(root, "HEAD")), {})


if __name__ == "__main__":
    unittest.main()
