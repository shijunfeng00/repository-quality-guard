"""仓库证据查询的 Cases 驱动 CLI 回归：既查到证据又不能制造确定性。"""

from __future__ import annotations

import io
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace

from scripts import evidence_lookup
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evidence_lookup.py"
SOURCE = """raise RuntimeError("not to be executed")

class Base:
    def method(self):
        return 1

class Child(Base):
    def method(self):
        return super().method()

def sink():
    return 42

def intermediate():
    return sink()

def entry():
    return intermediate()

def use_callback():
    return map(sink, ())
"""


class TestEvidenceLookup(unittest.TestCase):
    """从实际工作树类型事实证明直接调用的双向检索和保守降级。"""

    def setUp(self) -> None:
        """创建无 Git 的简单 Python 仓库，验证不依赖项目运行环境。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "sample.py").write_text(SOURCE, encoding="utf-8")

    def query(self, *args: str) -> SimpleNamespace:
        """同一测试进程内复用权威 graph provider，避免反复启动解释器。"""
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = evidence_lookup.main(list(args))
            except SystemExit as error:
                code = int(error.code)
        return SimpleNamespace(
            returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue()
        )

    def test_real_cli_entrypoint(self) -> None:
        """至少通过一次独立进程回归真实 CLI 启动路径。"""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--rule", "QG003"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## QG003", result.stdout)

    def test_rule_and_upstream_share_one_command(self) -> None:
        """命令同时提供权威规则卡与两级真实静态 caller。"""
        result = self.query(
            "--rule",
            "QG004",
            "--repo",
            str(self.root),
            "--symbol",
            "sample.sink",
            "--direction",
            "callers",
            "--depth",
            "2",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## QG004", result.stdout)
        self.assertIn("`sample.intermediate` → `sample.sink`", result.stdout)
        self.assertIn("`sample.entry` → `sample.intermediate`", result.stdout)
        self.assertIn("`sample.py:", result.stdout)
        self.assertIn("DIRECT_CALL, resolved", result.stdout)
        self.assertIn("[CALLBACK_ARGUMENT]", result.stdout)
        self.assertIn("`sample.use_callback` → `sample.sink`", result.stdout)
        self.assertNotIn("`sample.use_callback` → `sample.sink`", result.stdout.split("## 传入回调/注册参数")[0])

    def test_downstream_and_depth_budget(self) -> None:
        """只能遍历所选方向和深度；资源预算明确标记截断。"""
        result = self.query(
            "--repo",
            str(self.root),
            "--symbol",
            "sample.entry",
            "--direction",
            "callees",
            "--depth",
            "2",
            "--max-edges",
            "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("`sample.entry` → `sample.intermediate`", result.stdout)
        self.assertIn("TRUNCATED", result.stdout)

    def test_one_edge_depth_does_not_invent_longer_path(self) -> None:
        """一层调用不穿透到更远的调用方。"""
        result = self.query(
            "--repo", str(self.root), "--symbol", "sample.sink", "--depth", "1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("`sample.intermediate` → `sample.sink`", result.stdout)
        self.assertNotIn("`sample.entry` → `sample.intermediate`", result.stdout)

    def test_inheritance_is_not_a_direct_call_stack(self) -> None:
        """继承不能自动变成实际调用帧，未知分派必须保守。"""
        result = self.query(
            "--repo",
            str(self.root),
            "--symbol",
            "sample.Child",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("类型关系", result.stdout)
        self.assertIn("inheritance", result.stdout)
        self.assertIn("静态，非运行时 Traceback", result.stdout)

    def test_ambiguous_function_name_rejected(self) -> None:
        """多文件同名函数不能根据顺序猜一个。"""
        (self.root / "second.py").write_text(
            "def sink():\n    return -1\n", encoding="utf-8"
        )
        result = self.query("--repo", str(self.root), "--symbol", "sink")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("目标必须唯一", result.stderr)
        self.assertIn("sample.sink", result.stderr)
        self.assertIn("second.sink", result.stderr)

    def test_case_driven_remediation_card_from_rules(self) -> None:
        """特殊业务裁决必须由唯一 RULES 来源呈现，不能只有通用修复建议。"""
        result = self.query("--rule", "QG003", "--rule", "QG004", "--rule", "QG033")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("QG003 / QG026", result.stdout)
        self.assertIn("实际可达性", result.stdout)
        self.assertIn("bool(set[str])", result.stdout)
        self.assertIn("RULES.md:", result.stdout)

    def test_existing_api_doc_catalog_reused_in_same_cli(self) -> None:
        """同一入口复用官方 BM25 Catalog；能力描述不自动猜调用身份。"""
        result = self.query(
            "--rule",
            "QG004",
            "--api",
            "intermediate",
            "--repo",
            str(self.root),
            "--symbol",
            "sample.intermediate",
            "--direction",
            "callees",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("# API Catalog 文档查询", result.stdout)
        self.assertIn("`sample.intermediate`", result.stdout)
        self.assertIn("## QG004", result.stdout)
        self.assertIn("`sample.intermediate` → `sample.sink`", result.stdout)
        self.assertTrue((self.root / "docs/api-reference/catalog.json").exists())

    def test_api_missing_cannot_be_treated_as_absence_proof(self) -> None:
        """BM25 未召回是 UNKNOWN，而非证明业务中没有该能力。"""
        result = self.query("--api", "ultranonexistentxyz", "--repo", str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("未召回不等于不存在", result.stdout)

    def test_rule_only_does_not_need_repo(self) -> None:
        """只有规则查询时不启动任何目标仓库快照。"""
        result = self.query("--rule", "QG003")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## QG003", result.stdout)
        self.assertNotIn("# 仓库静态证据查询", result.stdout)

    def test_invalid_argument_combinations_are_atomic(self) -> None:
        """非法实参组合、深度越界、缺失符号不能产生部分成功结果。"""
        for args in (
            ("--rule", "QG003", "--repo", str(self.root)),
            ("--repo", str(self.root), "--symbol", "missing"),
            ("--repo", str(self.root), "--symbol", "sample.sink", "--depth", "6"),
        ):
            with self.subTest(args=args):
                result = self.query(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_source_excerpt_is_bounded(self) -> None:
        """输出有界源码片段，默认不会复制全部目标仓库到 Agent 上下文。"""
        result = self.query(
            "--repo",
            str(self.root),
            "--symbol",
            "sample.sink",
            "--snippet-lines",
            "1",
            "--depth",
            "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("def sink():", result.stdout)
        self.assertNotIn("raise RuntimeError", result.stdout)


if __name__ == "__main__":
    unittest.main()
