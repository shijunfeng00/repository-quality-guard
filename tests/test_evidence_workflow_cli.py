"""Functional CLI contracts for the production evidence lookup command."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

from rqgtest import CaseConfig, TestCase


class EvidenceWorkflowCliCase(TestCase):
    """Exercise the real quality_guard.py evidence process against frozen output oracles."""

    def __init__(self):
        super().__init__(config=CaseConfig(process="main"))

    def get_enterpoint(self):
        return subprocess.run

    def get_input_data(self):
        project = Path(__file__).resolve().parents[1]
        fixture = json.loads(
            (Path(__file__).parent / "cases/qg-evidence-lookup-cli.json").read_text(
                encoding="utf-8"
            )
        )
        for row in fixture["cases"]:
            with tempfile.TemporaryDirectory(
                prefix="rqg-evidence-cli-case-"
            ) as directory:
                repo = Path(directory)
                source = row.get("source", fixture["source"])
                if row.get("git_snapshot"):
                    snapshot = (
                        Path(__file__).parent
                        / "cases/qg-evidence-lookup-git-snapshot.tar.gz"
                    )
                    with tarfile.open(snapshot, "r:gz") as archive:
                        archive.extractall(repo)
                    if not (repo / ".git").is_dir():
                        raise RuntimeError(
                            "Frozen Git CLI fixture has no .git directory"
                        )
                (repo / "sample.py").write_text(source, encoding="utf-8")
                for name, content in row.get("extra_files", {}).items():
                    target = repo / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                args = [str(repo) if item == "@repo" else item for item in row["args"]]
                command = [
                    sys.executable,
                    "-B",
                    str(project / "scripts/quality_guard.py"),
                    "evidence",
                    *args,
                ]
                yield (
                    (command,),
                    {
                        "cwd": str(project),
                        "env": {
                            **os.environ,
                            "PYTHONPATH": str(project),
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                        "text": True,
                        "capture_output": True,
                        "check": False,
                        "timeout": 40,
                    },
                )

    def get_output_data(self):
        fixture = json.loads(
            (Path(__file__).parent / "cases/qg-evidence-lookup-cli.json").read_text(
                encoding="utf-8"
            )
        )
        for row in fixture["cases"]:
            yield row

    def equals(self, expected, actual):
        stdout = actual.stdout
        stderr = actual.stderr
        if actual.returncode != expected["code"]:
            return False
        if expected.get("stdout_empty", False) and stdout:
            return False
        if any(value not in stdout for value in expected.get("contains", [])):
            return False
        if any(value in stdout for value in expected.get("absent", [])):
            return False
        if any(value not in stderr for value in expected.get("stderr_contains", [])):
            return False
        if any(
            bounded["value"] in stdout.split(bounded["section"])[0]
            for bounded in expected.get("before_section_absent", [])
        ):
            return False
        artifacts = expected.get("artifacts", [])
        if artifacts:
            command = actual.args
            repo = Path(command[command.index("--repo") + 1])
            if any(not (repo / name).exists() for name in artifacts):
                return False
        return True


class EvidenceWorkflowCacheCliCase(TestCase):
    """Verify cache hit and equal-size source-edit invalidation via real CLI processes."""

    def __init__(self):
        super().__init__(config=CaseConfig(process="main"))

    def get_enterpoint(self):
        return subprocess.run

    def get_input_data(self):
        project = Path(__file__).resolve().parents[1]
        fixture = json.loads(
            (Path(__file__).parent / "cases/evidence-trace.json").read_text(
                encoding="utf-8"
            )
        )
        row = next(
            item for item in fixture["cases"] if item["scenario"] == "cache-invalidate"
        )
        with tempfile.TemporaryDirectory(
            prefix="rqg-evidence-cache-case-"
        ) as directory:
            repo = Path(directory) / "source"
            repo.mkdir()
            cache = Path(directory) / "cache"
            source = repo / "sample.py"
            source.write_text(row["source"], encoding="utf-8")
            for changed, marker in (
                (False, "cache=MISS/REBUILT"),
                (False, "cache=HIT"),
                (True, "cache=MISS/REBUILT"),
            ):
                if changed:
                    source.write_text(row["changed_source"], encoding="utf-8")
                command = [
                    sys.executable,
                    "-B",
                    str(project / "scripts/quality_guard.py"),
                    "evidence",
                    "--repo",
                    str(repo),
                    *row["args"],
                    "--cache-dir",
                    str(cache),
                ]
                yield (
                    (command,),
                    {
                        "cwd": str(project),
                        "env": {
                            **os.environ,
                            "PYTHONPATH": str(project),
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                        "text": True,
                        "capture_output": True,
                        "check": False,
                        "timeout": 60,
                    },
                )

    def get_output_data(self):
        yield {"returncode": 0, "marker": "cache=MISS/REBUILT", "stderr_empty": True}
        yield {"returncode": 0, "marker": "cache=HIT", "stderr_empty": True}
        yield {"returncode": 0, "marker": "cache=MISS/REBUILT", "stderr_empty": True}

    def equals(self, expected, actual):
        return (
            actual.returncode == expected["returncode"]
            and expected["marker"] in actual.stdout
            and (
                not expected["stderr_empty"]
                or not [
                    line
                    for line in actual.stderr.splitlines()
                    if not line.startswith(
                        (
                            "[QG] runtime bootstrap: ",
                            "[QG] Python 锁定依赖已就绪。",
                            "[QG] Node 锁定依赖已就绪: ",
                            "Skill 完整性门禁：PASS（",
                            "[QG] command finished rc=0; runtime cleanup complete.",
                        )
                    )
                ]
            )
        )


class EvidenceGraphCliCase(TestCase):
    """Verify constructor provenance and graph statistics through the shipped CLI."""

    def __init__(self):
        super().__init__(config=CaseConfig(process="main"))

    def get_enterpoint(self):
        return subprocess.run

    def get_input_data(self):
        project = Path(__file__).resolve().parents[1]
        fixture = json.loads(
            (
                Path(__file__).parent
                / "cases/graph-callback-topology-and-statistics.json"
            ).read_text(encoding="utf-8")
        )
        for row in fixture["cases"]:
            if row["scenario"] not in ("constructor-cli", "stats-cli"):
                continue
            with tempfile.TemporaryDirectory(prefix="rqg-graph-cli-case-") as directory:
                repo = Path(directory) / "repo"
                repo.mkdir()
                cache = Path(directory) / "cache"
                for name, content in row["sources"].items():
                    target = repo / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                args = ["--repo", str(repo), "--cache-dir", str(cache)]
                if row["scenario"] == "constructor-cli":
                    args += [
                        "--symbol",
                        "consumer.Receiver.work",
                        "--variable",
                        "self.channel",
                        "--depth",
                        "2",
                    ]
                else:
                    args += ["--graph-stats"]
                command = [
                    sys.executable,
                    "-B",
                    str(project / "scripts/quality_guard.py"),
                    "evidence",
                    *args,
                ]
                yield (
                    (command,),
                    {
                        "cwd": str(project),
                        "env": {
                            **os.environ,
                            "PYTHONPATH": str(project),
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                        "text": True,
                        "capture_output": True,
                        "check": False,
                        "timeout": 120,
                    },
                )

    def get_output_data(self):
        fixture = json.loads(
            (
                Path(__file__).parent
                / "cases/graph-callback-topology-and-statistics.json"
            ).read_text(encoding="utf-8")
        )
        for row in fixture["cases"]:
            if row["scenario"] in ("constructor-cli", "stats-cli"):
                yield {"scenario": row["scenario"], **row["expected"]}

    def equals(self, expected, actual):
        text = actual.stdout
        if actual.returncode != expected["exit"] or (
            not [
                line
                for line in actual.stderr.splitlines()
                if not line.startswith(
                    (
                        "[QG] runtime bootstrap: ",
                        "[QG] Python 锁定依赖已就绪。",
                        "[QG] Node 锁定依赖已就绪: ",
                        "Skill 完整性门禁：PASS（",
                        "[QG] command finished rc=0; runtime cleanup complete.",
                    )
                )
            ]
        ) is not expected.get("stderr_empty", False):
            return False
        if expected["scenario"] == "constructor-cli":
            return (
                ("self.channel = source" in text) is expected["channel_source"]
                and ("Receiver(source=self.state)" in text)
                is expected["constructor_call"]
                and ("self.state = Ledger()" in text) is expected["source_producer"]
                and ("[SOURCE_BOUND_MAY_CALL]" in text)
                is expected["possible_invocation"]
            )
        return (
            ("弱连通分量：" in text) is expected["components"]
            and ("类型约束可能调用：" in text) is expected["possible_calls"]
            and ("无已解析调用边的符号：" in text) is expected["isolated"]
        )


class UnifiedEvidenceWorkflowCase(TestCase):
    """Verify the official workflow exposes rule lookup and read-only graph facts."""

    def __init__(self):
        super().__init__(config=CaseConfig(process="main"))

    def get_enterpoint(self):
        return subprocess.run

    def get_input_data(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="rqg-unified-evidence-") as directory:
            repo = Path(directory)
            (repo / "module.py").write_text(
                "def add(first: int, second: int) -> int:\n    return first + second\n",
                encoding="utf-8",
            )
            for arguments in (
                ("--rule", "QG240"),
                ("--repo", str(repo), "--graph-stats"),
            ):
                command = [
                    sys.executable,
                    "-B",
                    str(project / "scripts" / "quality_guard.py"),
                    "evidence",
                    *arguments,
                ]
                yield (
                    (command,),
                    {
                        "cwd": str(project),
                        "env": {
                            **os.environ,
                            "PYTHONPATH": str(project),
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                        "text": True,
                        "capture_output": True,
                        "check": False,
                        "timeout": 40,
                    },
                )

    def get_output_data(self):
        yield {"required": "QG240", "forbidden": "Traceback"}
        yield {"required": "仓库图结构统计", "forbidden": "Traceback"}

    def equals(self, expected, actual):
        return (
            actual.returncode == 0
            and expected["required"] in actual.stdout
            and expected["forbidden"] not in actual.stderr
            and "Skill 完整性门禁：PASS" in actual.stderr
        )
