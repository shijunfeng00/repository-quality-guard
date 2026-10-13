"""An external type checker must resolve real declarations, not equal spelling."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


class CompilerTests(unittest.TestCase):
    @unittest.skipIf(shutil.which("pyrefly") is None, "offline Pyrefly not installed")
    def test_binding_identity_and_dynamic_nonproof(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            project = root / "input"
            project.mkdir()
            (project / "models.py").write_text(
                "class Left:\n    def operation(self): return 1\n\n"
                "class Right:\n    def operation(self): return 2\n"
            )
            (project / "run.py").write_text(
                "from models import Left\n"
                "def task():\n"
                "    receiver = Left()\n"
                "    return receiver.operation()\n"
            )
            target = root / "resolved.json"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.spike_core.compiler_enrich",
                    str(project),
                    "--max-calls",
                    "30",
                    "--output",
                    str(target),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(target.read_text())
            targets = {
                v for item in report["target_bindings"] for v in item["symbol_ids"]
            }
            self.assertIn("models.Left.operation", targets)
            self.assertNotIn("models.Right.operation", targets)
            self.assertEqual(report["status"], "OPEN_WORLD_OBSERVED_MAY")


if __name__ == "__main__":
    unittest.main()
