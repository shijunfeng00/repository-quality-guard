from __future__ import annotations

import ast
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.src.git_utils import run_readonly_git

ROOT = Path(__file__).resolve().parents[1]


class TestSubprocessEncoding(unittest.TestCase):
    def test_git_text_output_is_utf8_even_when_host_locale_is_gbk(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            filename = "中文“测试”.py"
            (root / filename).write_text("VALUE = 1\n", encoding="utf-8")

            with patch.object(subprocess, "_text_encoding", return_value="gbk"):
                result = run_readonly_git(
                    root,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                )

            self.assertEqual(0, result.returncode)
            self.assertIn(filename, result.stdout)

    def test_text_subprocesses_declare_encoding_explicitly(self) -> None:
        missing: list[str] = []
        for directory in (ROOT / "runtime", ROOT / "scripts"):
            for path in directory.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    if node.func.attr not in {"run", "Popen"}:
                        continue
                    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "subprocess":
                        continue
                    keywords = {item.arg: item.value for item in node.keywords if item.arg}
                    text = keywords.get("text")
                    if not isinstance(text, ast.Constant) or text.value is not True:
                        continue
                    if "encoding" not in keywords:
                        relative = path.relative_to(ROOT).as_posix()
                        missing.append(f"{relative}:{node.lineno}")

        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
