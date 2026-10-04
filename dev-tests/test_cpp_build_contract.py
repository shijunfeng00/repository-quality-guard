from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
from runtime.src.cpp_build_contract import (
    CompilerFamily,
    NativeSyntaxState,
    check_native_syntax,
    load_compilation_database,
)


class TestCppBuildContract(unittest.TestCase):
    def _snapshot(
        self, root: Path, label: str = "WORKTREE"
    ) -> RepositoryAnalysisSnapshot:
        source = "int main() { return 0; }\n"
        return RepositoryAnalysisSnapshot(
            root=root,
            label=label,
            units={},
            language_units={
                "src/main.cpp": LanguageUnit("src/main.cpp", "cpp", source)
            },
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

    def test_malformed_arguments_do_not_fall_back_to_command(self) -> None:
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
                    "arguments": None,
                    "command": f"g++ -c {source}",
                },
            )
            self.assertEqual(load_compilation_database(self._snapshot(root)), {})

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

    def test_msvc_contract_is_preserved_without_claiming_native_availability(
        self,
    ) -> None:
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
                        "cl.exe",
                        "/std:c++20",
                        "/c",
                        str(source),
                        "/Fomain.obj",
                    ],
                },
            )
            spec = load_compilation_database(self._snapshot(root))["src/main.cpp"]
            self.assertEqual(spec.family, CompilerFamily.MSVC)
            self.assertEqual(spec.compiler, "cl.exe")
            self.assertIn("/Zs", spec.native_syntax_command())

    @unittest.skipUnless(__import__("shutil").which("g++"), "g++ required")
    def test_real_gcc_native_syntax_check_uses_authored_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            source = root / "src" / "main.cpp"
            source.write_text(
                "#ifndef MODE\n#error missing build contract\n#endif\nint main() { return MODE; }\n",
                encoding="utf-8",
            )
            self._write(
                root,
                {
                    "directory": str(root / "build"),
                    "file": str(source),
                    "arguments": ["g++", "-DMODE=7", "-std=gnu++20", "-c", str(source)],
                },
            )
            spec = load_compilation_database(self._snapshot(root))["src/main.cpp"]
            result = check_native_syntax(spec)
            self.assertEqual(result.state, NativeSyntaxState.PASS, result.stderr)
            self.assertEqual(result.command[0], "g++")
            self.assertIn("-DMODE=7", result.command)

    def test_nested_subproject_databases_are_merged_by_translation_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            language_units = {}
            for project in ("first", "second"):
                source = root / project / "src" / "main.cpp"
                source.parent.mkdir(parents=True)
                source.write_text("int main() { return 0; }\n", encoding="utf-8")
                build = root / project / "build-rqg"
                build.mkdir()
                (build / "compile_commands.json").write_text(
                    json.dumps(
                        [
                            {
                                "directory": str(build),
                                "file": str(source),
                                "arguments": ["g++", "-c", str(source)],
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                rel = source.relative_to(root).as_posix()
                language_units[rel] = LanguageUnit(rel, "cpp", source.read_text())
            snapshot = RepositoryAnalysisSnapshot(
                root=root, label="DIRECTORY", units={}, language_units=language_units
            )
            specs = load_compilation_database(snapshot)
            self.assertEqual(set(specs), {"first/src/main.cpp", "second/src/main.cpp"})

    def test_conflicting_commands_for_same_tu_remain_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "main.cpp"
            source.parent.mkdir()
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            for name, define in (("build-a", "A"), ("build-b", "B")):
                build = root / name
                build.mkdir()
                (build / "compile_commands.json").write_text(
                    json.dumps(
                        [
                            {
                                "directory": str(build),
                                "file": str(source),
                                "arguments": ["g++", f"-D{define}", "-c", str(source)],
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
            snapshot = self._snapshot(root)
            self.assertEqual(load_compilation_database(snapshot), {})

    def test_revision_snapshot_does_not_borrow_worktree_compilation_database(
        self,
    ) -> None:
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
            self.assertEqual(
                load_compilation_database(self._snapshot(root, "HEAD")), {}
            )


if __name__ == "__main__":
    unittest.main()
