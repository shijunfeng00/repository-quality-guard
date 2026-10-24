#!/usr/bin/env python3
"""Verify one canonical release ZIP by inspecting, installing and exercising it.

Run after build_release.py at the release milestone. This command intentionally
is not part of the per-change unit-test suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


# The release verifier lives in tools/; resolve the same process lifecycle
# implementation used by the published scanner without a second runner.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.src.process_lifecycle import terminate_process_tree


_ROOT = "repository-quality-guard"
_REQUIRED_FILES = (
    "SKILL.md",
    ".git/HEAD",
    "runtime/RELEASE.lock",
    "runtime/dependencies.lock.json",
    "runtime/deploy.py",
    "runtime/install_dependencies.py",
    "runtime/src/integrity.py",
    "scripts/quality_guard.py",
    "scripts/git_hook_install.py",
    "offline/node_modules.zip",
)


def _execute(args: list[str], *, timeout: int = 180, cwd: Path | None = None) -> str:
    """Execute a real release command and fail if its exit status is nonzero."""
    result = subprocess.run(
        args, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError(
            f"release command failed ({result.returncode}): {args}\n"
            f"stdout: {result.stdout[-3000:]}\nstderr: {result.stderr[-3000:]}"
        )
    return result.stdout


def _scan_with_heartbeat(
    command: list[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run real audit/verify in a supervised subprocess without a total time cap.

    The verifier owns a private child process group and its output files.  A
    periodic heartbeat reports actual process liveness; it is not fabricated
    scanner progress or a substitute for the child's final exit code.
    """
    if not command:
        raise ValueError("release scan command must not be empty")
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout, \
         tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr:
        launch_options = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True}
        )
        process = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, text=True, **launch_options
        )
        try:
            while process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    print(
                        f"release scan running: pid={process.pid}, "
                        f"elapsed={time.monotonic() - started:.0f}s",
                        file=sys.stderr, flush=True,
                    )
        except BaseException:
            if process.poll() is None:
                terminate_process_tree(process)
            raise
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(
            command, process.returncode, stdout.read(), stderr.read()
        )


def _archive_contract(artifact: Path) -> tuple[str, int]:
    """Validate layout, offline lock completeness and archive member uniqueness."""
    with zipfile.ZipFile(artifact) as bundle:
        members = bundle.namelist()
        if len(members) != len(set(members)):
            raise RuntimeError("release ZIP contains duplicate archive members")
        files = {name for name in members if not name.endswith("/")}
        absent = [name for name in _REQUIRED_FILES if f"{_ROOT}/{name}" not in files]
        if absent:
            raise RuntimeError(f"release ZIP missing required content: {absent}")
        if any(not name.startswith(f"{_ROOT}/") for name in members):
            raise RuntimeError("unexpected release ZIP root")
        lock = json.loads(bundle.read(f"{_ROOT}/runtime/dependencies.lock.json"))
        packages = lock["python"]
        if not isinstance(packages, dict):
            raise RuntimeError("invalid release dependency lock")
        for name, version in packages.items():
            prefix = (
                f"{_ROOT}/offline/wheelhouse/{str(name).replace('-', '_')}-{version}-"
            )
            if not any(
                member.startswith(prefix) and member.endswith(".whl")
                for member in files
            ):
                raise RuntimeError(f"missing locked wheel for {name}=={version}")
        if any(
            name.endswith((".pyc", ".pyo"))
            or "/__pycache__/" in name
            or "/.pytest_cache/" in name
            for name in files
        ):
            raise RuntimeError("generated caches were included in release ZIP")
        # ZIP CRC validates data decompression of every member (not merely its name).
        damaged = bundle.testzip()
        if damaged:
            raise RuntimeError(f"corrupted release archive member: {damaged}")
    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest(), len(files)


def _install_and_hook_smoke(bundle: Path, workspace: Path) -> None:
    """Exercise actual deploy and hook installers against a disposable Git repo."""
    host = workspace / "host"
    host.mkdir()
    _execute(["git", "init", "-q", str(host)])
    destination = host / ".agents/skills/repository-quality-guard"
    _execute(
        [
            sys.executable,
            "-B",
            str(bundle / "runtime/deploy.py"),
            str(bundle),
            str(destination),
            "--profile",
            "qg-example-profile",
        ],
        timeout=180,
    )
    if not (destination / "runtime/RELEASE.lock").is_file():
        raise RuntimeError("installed runtime release lock missing")
    if (destination / "offline").exists():
        raise RuntimeError("installed runtime unexpectedly retains release media")
    installer = destination / "scripts/git_hook_install.py"
    _execute([sys.executable, "-B", str(installer), str(host)])
    hook = host / ".git/hooks/pre-push"
    first = hook.read_bytes()
    if b"# managed Repository Quality Guard pre-push" not in first:
        raise RuntimeError("installed pre-push hook is not the managed hook")
    if not os.access(hook, os.X_OK):
        raise RuntimeError("installed pre-push hook is not executable")
    _execute([sys.executable, "-B", str(installer), str(host)])
    if hook.read_bytes() != first:
        raise RuntimeError("second hook installation was not idempotent")
    # Existing non-managed user hooks are never overwritten silently.
    another = workspace / "existing-hook"
    another.mkdir()
    _execute(["git", "init", "-q", str(another)])
    old = another / ".git/hooks/pre-push"
    old.write_text("#!/bin/sh\necho existing-hook\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", str(installer), str(another)],
        capture_output=True,
        text=True,
        check=False,
    )
    if (
        result.returncode == 0
        or old.read_text(encoding="utf-8") != "#!/bin/sh\necho existing-hook\n"
    ):
        raise RuntimeError("hook installer overwrote an unmanaged hook")


def _unicode_git_smoke(bundle: Path, workspace: Path) -> None:
    """Check shipped Git-adapter UTF-8 handling with an actual Unicode filename."""
    repo = workspace / "unicode-git-fixture"
    repo.mkdir()
    _execute(["git", "init", "-q", str(repo)])
    filename = "中文“测试”.py"
    (repo / filename).write_text("VALUE = 1\n", encoding="utf-8")
    script = """from pathlib import Path
from unittest.mock import patch
import subprocess, sys
from runtime.src.git_utils import run_readonly_git
with patch.object(subprocess, '_text_encoding', return_value='gbk'):
    result = run_readonly_git(Path(sys.argv[1]), ('status', '--porcelain=v1', '--untracked-files=all'))
if result.returncode != 0 or result.stdout != '?? 中文“测试”.py\\n':
    raise RuntimeError(f'Unicode Git porcelain contract failed: {result.returncode}: {result.stdout!r}')
"""
    # Ensure the runtime imported is the one inside the release under test.
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(repo)],
        env={**os.environ, "PYTHONPATH": str(bundle), "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(
            f"release Git Unicode smoke failed ({result.returncode}): {result.stderr[-1000:]}"
        )


def _official_audit_smoke(destination: Path, workspace: Path) -> None:
    """Exercise installed, sealed audit/verify and preserve the report unchanged."""
    repo = workspace / "audit-fixture"
    repo.mkdir()
    source = repo / "sample.py"
    source.write_text(
        "def calculate(records: list[int]) -> int:\n    return sum(records)\n",
        encoding="utf-8",
    )
    (repo / "producer.py").write_text(
        "def deliver(one, two):\n    return one, two\n", encoding="utf-8"
    )
    (repo / "consumer.py").write_text(
        "from producer import deliver\nvalue = deliver(1)\n", encoding="utf-8"
    )
    tool = destination / "scripts/quality_guard.py"
    command = [sys.executable, "-B", str(tool)]
    args = [str(repo), "--profile", "qg-example-profile", "--mode", "ignore-diff"]
    audit = _scan_with_heartbeat([*command, "audit", *args], cwd=workspace)
    report = repo / "修改说明.md"
    if audit.returncode not in (0, 3, 5) or not report.is_file():
        raise RuntimeError(
            f"installed audit failed ({audit.returncode}): {audit.stdout[-1000:]} {audit.stderr[-1000:]}"
        )
    rendered = report.read_text(encoding="utf-8")
    if "QG209" not in rendered or "consumer.py:2" not in rendered:
        raise RuntimeError("installed audit lost cross-module QG209 evidence")
    before = hashlib.sha256(report.read_bytes()).digest()
    verify = _scan_with_heartbeat([*command, "verify", *args], cwd=workspace)
    if verify.returncode not in (0, 3, 5):
        raise RuntimeError(
            f"installed verify could not validate report: {verify.stderr[-1000:]}"
        )
    if hashlib.sha256(report.read_bytes()).digest() != before:
        raise RuntimeError("verify modified its input report")
    wrong = subprocess.run(
        [
            *command,
            "audit",
            str(repo),
            "--profile",
            "non-authorized-profile",
            "--mode",
            "ignore-diff",
        ],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
    )
    if wrong.returncode == 0:
        raise RuntimeError("installed audit accepted an unauthorized Profile")
    if hashlib.sha256(report.read_bytes()).digest() != before:
        raise RuntimeError("failed Profile selection modified the report")


def _run_offline_install(bundle: Path, workspace: Path) -> None:
    """Bootstrap locked dependencies using the release's offline media in a venv."""
    venv = workspace / "python-env"
    _execute([sys.executable, "-m", "venv", str(venv)], timeout=120)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _execute(
        [
            str(python),
            "-B",
            str(bundle / "runtime/install_dependencies.py"),
            str(bundle),
            "--offline",
        ],
        timeout=300,
        cwd=workspace,
    )
    _execute(
        [str(python), "-B", str(bundle / "scripts/quality_guard.py"), "--help"],
        timeout=40,
        cwd=workspace,
    )


def _source_catalog_smoke(destination: Path, workspace: Path) -> None:
    """Require the installed CLI to refresh catalog results after a source edit."""
    project = workspace / "catalog-fixture"
    project.mkdir()
    source = project / "service.py"
    source.write_text(
        'def alpha():\n    """alpha capability"""\n    return "alpha"\n',
        encoding="utf-8",
    )
    output = project / "docs/api-reference"
    command = [
        sys.executable,
        "-B",
        str(destination / "scripts/quality_guard.py"),
        "doc-search",
        str(project),
        "alpha capability",
        "--output",
        str(output),
    ]
    _execute(command, timeout=120, cwd=workspace)
    stamp = source.stat()
    source.write_text(
        'def bravo():\n    """bravo capability"""\n    return "bravo"\n',
        encoding="utf-8",
    )
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    command[5] = "bravo capability"
    _execute(command, timeout=120, cwd=workspace)
    catalog = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
    symbols = [record["symbol"] for record in catalog["records"]]
    if not any(symbol.endswith("bravo") for symbol in symbols) or any(
        symbol.endswith("alpha") for symbol in symbols
    ):
        raise RuntimeError("installed CLI reused an obsolete API catalog")


def _host_resource_release_smoke(bundle: Path, workspace: Path) -> None:
    """Check shipped instruction selection, race safety and deployment help flags."""
    help_result = subprocess.run(
        [sys.executable, "-B", str(bundle / "runtime/deploy.py"), "--help"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if (
        help_result.returncode != 0
        or "--profile" not in help_result.stdout
        or "--agents-profile" in help_result.stdout
        or "--ignore" in help_result.stdout
    ):
        raise RuntimeError(
            "shipped deployment CLI options differ from the public contract"
        )
    source = (bundle / "SKILL.md").read_text(encoding="utf-8")
    if not source.startswith("---\n"):
        raise RuntimeError("release SKILL.md is missing frontmatter")
    top = {
        line.split(":", 1)[0]
        for line in source.split("---\n", 2)[1].splitlines()
        if line and not line[0].isspace() and ":" in line
    }
    if (
        not top
        <= {
            "name",
            "description",
            "license",
            "compatibility",
            "metadata",
            "allowed-tools",
        }
        or "  status: stable" not in source.split("---\n", 2)[1]
    ):
        raise RuntimeError("release SKILL.md has invalid public frontmatter")
    script = """from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from runtime.deploy import _agents_source,_create_host_agents_if_missing
import sys
root=Path(sys.argv[1])
bundle=root/'bundle';profile=bundle/'profiles/sample';profile.mkdir(parents=True)
host=root/'host';host.mkdir()
(profile/'AGENTS.md').write_text('profile instructions\\n')
selected=_agents_source(bundle,SimpleNamespace(reference='sample'),SimpleNamespace(agents_file='AGENTS.md'))
if selected.read_text()!='profile instructions\\n':
    raise RuntimeError('Profile instruction source selection mismatch')
(profile/'HOST_INSTRUCTIONS.md').write_text('one root instruction\\n')
selected=_agents_source(bundle,SimpleNamespace(reference='sample'),SimpleNamespace(agents_file='HOST_INSTRUCTIONS.md'))
if not _create_host_agents_if_missing(host,selected) or (host/'AGENTS.md').read_text()!='one root instruction\\n':
    raise RuntimeError('Renamed host instructions could not be created')
if not (profile/'AGENTS.md').is_file():
    raise RuntimeError('Source profile instructions were changed')
template=bundle/'templates/AGENTS.template.md';template.parent.mkdir(parents=True)
template.write_text('generic instructions\\n')
if _agents_source(bundle,SimpleNamespace(reference=''),None).read_text()!='generic instructions\\n':
    raise RuntimeError('Generic instruction fallback differs from contract')
(host/'AGENTS.md').write_bytes(b'host-owned\\n\\x00stable')
target=(host/'AGENTS.md')
if _create_host_agents_if_missing(host,template) or target.read_bytes()!=b'host-owned\\n\\x00stable':
    raise RuntimeError('Existing user instructions were overwritten')
target.unlink()
plain=root/'template.md';plain.write_text('qg template\\n')
if not _create_host_agents_if_missing(host,plain) or _create_host_agents_if_missing(host,plain) or target.read_text()!='qg template\\n':
    raise RuntimeError('Host instructions are not create-once')
target.unlink()
try:
    with patch('runtime.deploy.os.open',side_effect=FileExistsError('race')):
        _create_host_agents_if_missing(host,plain)
except FileExistsError:
    pass
else:
    raise RuntimeError('Concurrent host creation did not fail closed')
"""
    fixture = workspace / "host-resource-contract"
    fixture.mkdir()
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(fixture)],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(bundle), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode:
        raise RuntimeError("host resource smoke failed: " + result.stderr[-1500:])


def _catalog_ledger_and_generated_docs_smoke(
    destination: Path, workspace: Path
) -> None:
    """Check released catalog ledger partitioning and generated-doc exclusion."""
    repository = workspace / "catalog-git-fixture"
    repository.mkdir()
    _execute(["git", "init", "-q", str(repository)])
    (repository / "base.py").write_text("BASE = True\n", encoding="utf-8")
    _execute(["git", "add", "base.py"], cwd=repository)
    _execute(
        [
            "git",
            "-c",
            "user.name=Release",
            "-c",
            "user.email=release@example.invalid",
            "commit",
            "-qm",
            "base",
        ],
        cwd=repository,
    )
    generated = repository / "docs/api-reference"
    generated.mkdir(parents=True)
    (generated / "INDEX.md").write_text(
        "# Generated API Catalog\nSource digest: `" + "a" * 64 + "`\n",
        encoding="utf-8",
    )
    (repository / "feature.py").write_text("TOKEN = 'v2.8-rc3'\n", encoding="utf-8")
    script = """import json,sys
from pathlib import Path
from runtime.src.config import GuardConfig
from runtime.src.report_contract import _changed_files
from runtime.src.release_identity_rules import release_identity_findings
from runtime.src.workflow import _append_search_ledger
root=Path(sys.argv[1])
paths=[item.path for item in _changed_files(root,'HEAD')]
findings=release_identity_findings(root,GuardConfig())
if paths!=['feature.py'] or any(f.path.startswith('docs/api-reference/') for f in findings) or not any(f.path=='feature.py' for f in findings):
    raise RuntimeError(f'Generated docs exclusion failed: {paths!r}, {findings!r}')
_append_search_ledger(root,'old',[],{'source_digest':'old-digest'})
_append_search_ledger(root,'new',[],{'source_digest':'new-digest'})
observed=[(item['query'],item['source_digest']) for item in map(json.loads,(root/'search-ledger.jsonl').read_text().splitlines())]
if observed!=[('new','new-digest')]:
    raise RuntimeError(f'Catalog ledger was not partitioned by source digest: {observed!r}')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(repository)],
        env={
            **os.environ,
            "PYTHONPATH": str(destination),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=35,
    )
    if result.returncode:
        raise RuntimeError(
            "catalog ledger/source exclusion smoke failed: " + result.stderr[-1500:]
        )


def _worker_lifecycle_release_smoke(bundle: Path, workspace: Path) -> None:
    """Check worker failure publication, stale-directory cleanup and parent death."""
    script = """import json,os,signal,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch
from runtime.src import scan_snapshot,scan_worker
root=Path(sys.argv[1])
status=root/'worker-status.json'
with (patch.object(scan_worker.faulthandler,'dump_traceback_later'),
      patch.object(scan_worker.traceback,'print_exc'),
      patch.object(scan_worker.cli,'scan_target',side_effect=RuntimeError('missing parser'))):
    code=scan_worker.main(['--path',str(root),'--output',str(root/'report.pkl'),'--status',str(status),'--parent-pid',str(os.getppid())])
payload=json.loads(status.read_text(encoding='utf-8'))
if code!=2 or payload['state']!='FAILED' or 'missing parser' not in payload['message']:
    raise RuntimeError('Worker failure status did not propagate')
worker=root/'scan-worker'
recent=worker/'scan-recent'
recent.mkdir(parents=True)
scan_snapshot._cleanup_stale_worker_dirs(worker)
if not recent.exists():
    raise RuntimeError('Worker cleanup removed a recent active directory')
old=worker/'scan-abandoned'
old.mkdir(parents=True)
past=time.time()-120
os.utime(old,(past,past))
scan_snapshot._cleanup_stale_worker_dirs(worker)
if old.exists():
    raise RuntimeError('Worker cleanup retained an abandoned directory')
if sys.platform.startswith('linux'):
    pid_file=root/'orphan-child.pid'
    ready=root/'orphan-ready'
    source=str(Path(sys.argv[2]))
    child_code=('import time;from pathlib import Path;'
       'from runtime.src.process_lifecycle import install_parent_death_guard;'
       'install_parent_death_guard();'
       f"Path({str(ready)!r}).write_text('ready');time.sleep(30)")
    parent_code=('import subprocess,sys,time;from pathlib import Path;'
       f'p=subprocess.Popen([sys.executable,"-c",{child_code!r}]);'
       f'Path({str(pid_file)!r}).write_text(str(p.pid));time.sleep(30)')
    parent=subprocess.Popen([sys.executable,'-c',parent_code],env={**os.environ,'PYTHONPATH':source})
    child_pid=None
    try:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not (ready.is_file() and pid_file.is_file()):
            time.sleep(0.05)
        if not ready.is_file() or not pid_file.is_file():
            raise RuntimeError('Orphan worker fixture never became ready')
        child_pid=int(pid_file.read_text())
        os.kill(parent.pid,signal.SIGKILL)
        parent.wait(timeout=3)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            stat=Path(f'/proc/{child_pid}/stat')
            if not stat.exists() or stat.read_text().split()[2]=='Z':
                break
            time.sleep(0.05)
        else:
            raise RuntimeError('Worker child survived parent termination')
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=3)
        if child_pid:
            try:
                os.kill(child_pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(workspace), str(bundle)],
        env={**os.environ, "PYTHONPATH": str(bundle), "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=25,
    )
    if result.returncode:
        raise RuntimeError(
            "worker lifecycle release smoke failed: " + result.stderr[-1500:]
        )


def _evidence_lookup_release_smoke(bundle: Path, workspace: Path) -> None:
    """Replay authored evidence CLI output oracles against the released command."""
    fixture = bundle / "tools/release_checks/fixtures/qg-evidence-lookup-cli-v1.json"
    if not fixture.is_file():
        raise RuntimeError("canonical evidence CLI oracle missing from release")
    script = """import contextlib,io,json,subprocess,sys,tempfile
from pathlib import Path
from scripts import evidence_lookup
bundle=Path(sys.argv[1]); rows=json.loads((bundle/'tools/release_checks/fixtures/qg-evidence-lookup-cli-v1.json').read_text())
with tempfile.TemporaryDirectory(prefix='rqg-evidence-release-') as directory:
    for index,row in enumerate(rows['cases']):
        root=Path(directory)/f'case-{index}'
        root.mkdir()
        (root/'sample.py').write_text(row.get('source',rows['source']),encoding='utf-8')
        for name,source in row.get('extra_files',{}).items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(source,encoding='utf-8')
        if row.get('git_snapshot') and not (root/'.git').is_dir():
            subprocess.run(['git','init','-q'],cwd=root,check=True,capture_output=True)
            subprocess.run(['git','add','.'],cwd=root,check=True,capture_output=True)
            subprocess.run(['git','-c','user.name=Release','-c','user.email=release@example.invalid','commit','-qm','fixture'],cwd=root,check=True,capture_output=True)
        args=[str(root) if item=='@repo' else item for item in row['args']]
        if row.get('exec')=='process':
            p=subprocess.run([sys.executable,str(bundle/'scripts/evidence_lookup.py'),*args],cwd=bundle,text=True,capture_output=True,check=False,timeout=40)
            code,stdout,stderr=p.returncode,p.stdout,p.stderr
        else:
            output,errors=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
                try: code=evidence_lookup.main(args)
                except SystemExit as e:code=int(e.code)
            stdout,stderr=output.getvalue(),errors.getvalue()
        failed=(code!=row['code']
            or row.get('stdout_empty',False) and bool(stdout)
            or any(value not in stdout for value in row.get('contains',[]))
            or any(value in stdout for value in row.get('absent',[]))
            or any(value not in stderr for value in row.get('stderr_contains',[]))
            or any(bounded['value'] in stdout.split(bounded['section'])[0] for bounded in row.get('before_section_absent',[]))
            or any(not (root/name).exists() for name in row.get('artifacts',[])))
        if failed:
            raise RuntimeError(f"Evidence CLI {row['case_id']} violated frozen output oracle: exit={code}, stdout={stdout[-1200:]!r}, stderr={stderr[-1200:]!r}")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(bundle)],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
        env={**os.environ, "PYTHONPATH": str(bundle), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode:
        raise RuntimeError(
            "released evidence CLI regression failed: " + result.stderr[-2000:]
        )


def _evidence_cache_release_smoke(bundle: Path, workspace: Path) -> None:
    """Verify unchanged-source reuse and equal-size source-edit invalidation."""
    script = """import contextlib,io,json,sys,tempfile
from pathlib import Path
from scripts import evidence_lookup
bundle=Path(sys.argv[1])
rows=json.loads((bundle/'tools/release_checks/fixtures/b33-evidence-trace-v1.json').read_text())['cases']
row=next(item for item in rows if item['scenario']=='cache-invalidate')
with tempfile.TemporaryDirectory(prefix='rqg-evidence-cache-release-') as directory:
    root=Path(directory)/'source';root.mkdir()
    cache=Path(directory)/'cache'
    source=root/'sample.py';source.write_text(row['source'],encoding='utf-8')
    args=['--repo',str(root),*row['args'],'--cache-dir',str(cache)]
    results=[]
    for changed in (False,False,True):
        if changed:source.write_text(row['changed_source'],encoding='utf-8')
        stdout,stderr=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            status=evidence_lookup.main(args)
        if status!=0 or stderr.getvalue():raise RuntimeError('evidence lookup failed during cache check')
        results.append(stdout.getvalue())
    if not ('cache=MISS/REBUILT' in results[0] and 'cache=HIT' in results[1]
            and 'cache=MISS/REBUILT' in results[2]):
        raise RuntimeError('evidence graph cache did not respect source mutation')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(bundle)],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(bundle), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode:
        raise RuntimeError(
            "released evidence cache regression failed: " + result.stderr[-2000:]
        )


def _snapshot_equivalence_release_smoke(bundle: Path, workspace: Path) -> None:
    """Compare normal and shared-snapshot reports on frozen source fixtures."""
    script = """import json,sys,tempfile
from pathlib import Path
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner,snapshot_only_rule_findings
from runtime.src.topology_facts import RepositoryTopology
rows=json.loads((Path(sys.argv[1])/'tools/release_checks/fixtures/b33-scanner-equivalence-v1.json').read_text())['cases']
for row in rows:
    with tempfile.TemporaryDirectory(prefix='rqg-snapshot-release-') as directory:
        root=Path(directory)
        for name,body in row['sources'].items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(body,encoding='utf-8')
        config=GuardConfig(require_docstrings=False,require_docstring_sections=False,include_tests=True)
        if row['scenario']=='normalized-topology':
            actual=RepositoryScanner(root,config).scan()
            legacy=RepositoryScanner(root,config,topology=RepositoryTopology(symbols=(),owners=(),edges=())).scan()
            symbol='sample.Worker._helper'
            def finding(report):
                return next(f for f in report.findings if f.code=='QG001' and f.symbol==symbol)
            observed={
                'default_source_bound':finding(actual).evidence['topology_resolution']=='normalized',
                'no_topology_is_explicit':finding(legacy).evidence['topology_resolution']=='legacy',
                'same_definition_count':actual.definitions==legacy.definitions,
                'same_documentation_count':actual.complete_docstrings==legacy.complete_docstrings,
            }
        else:
            snapshot=directory_analysis_snapshot(root,config)
            facts=snapshot_only_rule_findings(snapshot)
            observed=[]
            for include_tests in (False,True):
                scope=config.with_overrides(include_tests=include_tests)
                direct=RepositoryScanner(root,scope,snapshot).scan()
                reused=RepositoryScanner(root,scope,snapshot,shared_snapshot_findings=facts).scan()
                observed.append({
                    'report_equal':direct==reused,
                    'finding_equal':direct.findings==reused.findings,
                    'fingerprint_order_equal':[f.fingerprint for f in direct.findings]==[f.fingerprint for f in reused.findings],
                })
        if observed!=row['expected']:
            raise RuntimeError(f"snapshot equivalence failed for {row['case_id']}: {observed}")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(bundle)],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(bundle), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode:
        raise RuntimeError(
            "released snapshot comparison failed: " + result.stderr[-2000:]
        )


def _graph_public_cli_release_smoke(bundle: Path, workspace: Path) -> None:
    """Replay CLI and callback evidence against the real normalized graph."""
    script = """import contextlib,io,json,sys,tempfile
from pathlib import Path
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.relation_graph import RepositoryRelationGraph,normalized_topology
from runtime.src.topology_facts import UsageKind
from scripts import evidence_lookup
from scripts.evidence_graph import TraceOptions,_serialize,render
from scripts.evidence_statistics import graph_statistics
rows=json.loads((Path(sys.argv[1])/'tools/release_checks/fixtures/b32-graph-observable-v1.json').read_text())['cases']
for row in rows:
    if row['scenario']=='nested-owner':continue
    with tempfile.TemporaryDirectory(prefix='rqg-graph-cli-release-') as directory:
        root=Path(directory)/'repo';root.mkdir()
        for name,content in row['sources'].items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(content,encoding='utf-8')
        scenario=row['scenario']
        if scenario in ('constructor-cli','stats-cli'):
            output,errors=io.StringIO(),io.StringIO()
            args=['--repo',str(root),'--cache-dir',str(Path(directory)/'cache')]
            if scenario=='constructor-cli':
                args+=['--symbol','consumer.Receiver.work','--variable','self.channel','--depth','2']
            else:args+=['--graph-stats']
            with contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
                status=evidence_lookup.main(args)
            text=output.getvalue()
            if scenario=='constructor-cli':
                actual={'exit':status,'channel_source':'self.channel = source' in text,
                    'constructor_call':'Receiver(source=self.state)' in text,
                    'source_producer':'self.state = Ledger()' in text,
                    'possible_invocation':'[SOURCE_BOUND_MAY_CALL]' in text,
                    'stderr_empty':not errors.getvalue()}
            else:
                actual={'exit':status,'components':'弱连通分量：' in text,
                    'possible_calls':'类型约束可能调用：' in text,
                    'isolated':'无已解析调用边的符号：' in text,
                    'stderr_empty':not errors.getvalue()}
        else:
            snapshot=directory_analysis_snapshot(root,GuardConfig())
            topology=normalized_topology(RepositoryRelationGraph(snapshot))
            edges=[edge for edge in topology.edges if edge.source_id=='workflow.Worker.configure' and edge.target_id=='workflow.Worker.task']
            graph=_serialize(topology);stats=graph_statistics(graph)
            anchor=next(symbol for symbol in graph['symbols'] if symbol['id']=='workflow.Worker.task')
            rendered=render(snapshot,graph,anchor,TraceOptions(direction='callers',depth=2,
                budget=8,radius=1,variable=None,line=None,cache_info='release'))
            actual={'callback_edge':any(edge.kind is UsageKind.CALLBACK_REGISTRATION for edge in edges),
                'direct_call_not_fabricated':all(edge.kind is not UsageKind.DIRECT_CALL for edge in edges),
                'one_registration':'注册边 1' in stats,
                'three_components':'弱连通分量：3' in stats,
                'second_component':'弱连通分量 2' in stats,
                'traced_callback':'[CALLBACK_ARGUMENT]' in rendered,
                'exact_call_site':"graph.add_node('step', self.task)" in rendered,
                'not_actual_dispatch':'不证明实际调度' in rendered,
                'no_fake_resolved_direct_call':'[DIRECT_CALL, resolved]' not in rendered}
        if actual != row['expected']:
            raise RuntimeError(f"graph CLI regression {row['case_id']}: {actual}")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(bundle)],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(bundle), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode:
        raise RuntimeError(
            "released graph CLI regression failed: " + result.stderr[-2000:]
        )


def _rule_lookup_smoke(destination: Path, workspace: Path) -> None:
    """Verify official rule lookup accepts defined and rejects undefined rules."""
    entry = destination / "scripts/rule_lookup.py"
    known = _execute(
        [sys.executable, "-B", str(entry), "-QG210", "-QG215"],
        timeout=30,
        cwd=workspace,
    )
    if "## QG210" not in known or "## QG215" not in known:
        raise RuntimeError("installed lookup omitted a known rule contract")
    unknown = subprocess.run(
        [sys.executable, "-B", str(entry), "-QG456"],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
        cwd=workspace,
    )
    if unknown.returncode != 2 or "QG456" not in unknown.stderr:
        raise RuntimeError("installed lookup accepted an undefined rule")


def _integrity_mutation_smoke(bundle: Path, workspace: Path) -> None:
    """Validate release sealing and mutation rejection using the shipped implementation."""
    trial = workspace / "integrity-fixture"
    shutil.copytree(
        bundle,
        trial,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".pytest_cache", "offline"
        ),
    )
    program = """from pathlib import Path
import sys
from runtime.src.integrity import (
    RELEASE_HOME_ENV, RELEASE_SEAL_ENV, seal_release_tree,
    verify_release_integrity,
)
root = Path(sys.argv[1])
seal = seal_release_tree(root, "skill")
environment = {RELEASE_HOME_ENV: str(root), RELEASE_SEAL_ENV: seal}
if not verify_release_integrity(environment).passed:
    raise RuntimeError("fresh release seal does not validate")
contract = root / "runtime/src/integrity.py"
contract.write_bytes(contract.read_bytes() + b"\\n# changed after sealing\\n")
if verify_release_integrity(environment).passed:
    raise RuntimeError("release seal failed to detect a modified test contract")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", program, str(trial)],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
        cwd=workspace,
        env={**os.environ, "PYTHONPATH": str(trial)},
    )
    if result.returncode != 0:
        raise RuntimeError("integrity smoke failed: " + result.stderr[-1200:])


def _cache_worker_release_smoke(bundle: Path, workspace: Path) -> None:
    """Validate one real cold/warm scan in the shipped implementation."""
    repo = workspace / "cache-replay"
    repo.mkdir()
    _execute(["git", "init", "-q", str(repo)])
    (repo / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    _execute(["git", "add", "sample.py"], cwd=repo)
    _execute(
        [
            "git",
            "-c",
            "user.name=Release",
            "-c",
            "user.email=release@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        cwd=repo,
    )
    script = """import os,sys
from pathlib import Path
from runtime.src.cli import build_parser
from runtime.src.scan_snapshot import scan_target_cached
root=Path(sys.argv[1])
args=build_parser().parse_args([str(root),'--diff-base','HEAD'])
first=scan_target_cached(args)
second=scan_target_cached(args)
if not first[4].startswith('MISS') or not second[4].startswith('HIT'):
    raise RuntimeError(f'Cold/warm cache contract failed: {first[4]!r}, {second[4]!r}')
if [(x.code,x.path,x.line) for x in first[1].findings] != [(x.code,x.path,x.line) for x in second[1].findings]:
    raise RuntimeError('Warm scan returned different Findings')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(repo)],
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
        env={
            **os.environ,
            "PYTHONPATH": str(bundle),
            "REPO_QUALITY_GUARD_CACHE_DIR": str(workspace / "cache"),
            "REPO_QUALITY_GUARD_RELEASE_SEAL": "release-cache-smoke",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        cwd=workspace,
    )
    if result.returncode:
        raise RuntimeError("release cache scan failed: " + result.stderr[-2000:])


def _native_cpp_release_smoke(bundle: Path, workspace: Path) -> None:
    """Compile the shipped C++ TestUnit and compare its independent oracle."""
    test_source = bundle / "tools/release_checks/fixtures/cpp-testunit"
    if not test_source.is_dir():
        raise RuntimeError("missing C++ TestUnit contract sources in canonical release")
    build = workspace / "cpp-testunit-build"
    _execute(
        [
            "cmake",
            "-S",
            str(test_source),
            "-B",
            str(build),
            "-DCMAKE_BUILD_TYPE=Release",
        ],
        timeout=90,
    )
    _execute(["cmake", "--build", str(build), "-j2"], timeout=90)
    _execute(["ctest", "--test-dir", str(build), "--output-on-failure"], timeout=60)
    observed = json.loads(_execute([str(build / "rqg_testunit_cpp")], timeout=40))
    rows = json.loads(
        (bundle / "tools/release_checks/fixtures/native-oracle-b69.json").read_text(
            encoding="utf-8"
        )
    )["cases"]
    oracle = next(item["expected"] for item in rows if item["kind"] == "cpp-native")
    checks = {
        "configure": 0,
        "build": 0,
        "ctest": 0,
        "native": 0,
        "schema": observed["schema_version"],
        "succeeded": observed["succeeded"],
        "passed": observed["passed"],
        "cases": [[c["case_id"], c["status"]] for c in observed["cases"]],
        "steps": [
            [step["method"], step["status"]] for step in observed["cases"][6]["steps"]
        ],
        "keys": all(
            set(c) == {"case_id", "status", "detail", "steps_executed", "steps"}
            for c in observed["cases"]
        ),
    }
    if checks != oracle:
        raise RuntimeError("C++ TestUnit published behavior differs from frozen oracle")


def _native_c_java_release_smoke(bundle: Path, workspace: Path) -> None:
    """Validate C11/Java native ledgers and cross-language observable parity."""
    tests = bundle / "tools/release_checks/fixtures/native-testunit"
    if not tests.is_dir():
        raise RuntimeError("missing native TestUnit fixture sources")
    rows = json.loads(
        (bundle / "tools/release_checks/fixtures/native-oracle-b69.json").read_text(
            encoding="utf-8"
        )
    )["cases"]
    expected = {
        item["kind"] + ":" + str(item.get("language", "")): item["expected"]
        for item in rows
        if item["kind"] in {"native-provider", "native-equivalence"}
    }
    out = workspace / "native-testunit"
    out.mkdir()
    _execute(
        [
            "javac",
            "-encoding",
            "UTF-8",
            "-classpath",
            str(out),
            "-sourcepath",
            str(bundle / "runtime/contracts/java"),
            "-d",
            str(out),
            str(bundle / "runtime/contracts/java/TestUnit.java"),
            str(tests / "JavaNativeCases.java"),
        ],
        timeout=90,
    )
    _execute(
        [
            "gcc",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-I",
            str(bundle / "runtime/contracts/c"),
            str(tests / "c_native_cases.c"),
            "-o",
            str(out / "c_native_cases"),
        ],
        timeout=90,
    )

    def summarize(value: dict, language: str) -> dict:
        return {
            "schema": value["schema_version"],
            "passed": value["passed"],
            "succeeded": value["succeeded"],
            "cases": [
                [x["case_id"], x["status"], x["steps_executed"]] for x in value["cases"]
            ],
            "steps": [[s["method"], s["status"]] for s in value["cases"][4]["steps"]]
            if language == "java"
            else [],
            "public_keys": all(
                set(x) == {"case_id", "status", "detail", "steps_executed", "steps"}
                and len(x["steps"]) <= x["steps_executed"]
                for x in value["cases"]
            ),
            "unsupported_inconclusive": all(
                "C" in value["cases"][i]["detail"] for i in (2, 3)
            )
            if language == "c"
            else True,
        }

    for language, command in (
        ("java", ["java", "-cp", str(out), "JavaNativeCases"]),
        ("c", [str(out / "c_native_cases")]),
    ):
        actual = json.loads(_execute(command, timeout=40))
        if summarize(actual, language) != expected["native-provider:" + language]:
            raise RuntimeError(
                "Native " + language + " TestUnit deviates from frozen oracle"
            )
    _execute(
        [
            "g++",
            "-std=c++20",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-I",
            str(bundle / "runtime/contracts/cpp"),
            str(tests / "EquivalentNativeCases.cpp"),
            "-o",
            str(out / "equivalent_cpp_cases"),
        ],
        timeout=90,
    )
    _execute(
        [
            "javac",
            "-encoding",
            "UTF-8",
            "-classpath",
            str(out),
            "-sourcepath",
            str(bundle / "runtime/contracts/java"),
            "-d",
            str(out),
            str(bundle / "runtime/contracts/java/TestUnit.java"),
            str(tests / "EquivalentNativeCases.java"),
        ],
        timeout=90,
    )
    cpp = json.loads(_execute([str(out / "equivalent_cpp_cases")], timeout=40))
    java = json.loads(
        _execute(["java", "-cp", str(out), "EquivalentNativeCases"], timeout=40)
    )

    def public(value):
        return [
            [
                x["case_id"],
                x["status"],
                x["steps_executed"],
                [
                    [step["index"], step["method"], step["status"]]
                    for step in x["steps"]
                ],
            ]
            for x in value["cases"]
        ]

    parity = {
        "cpp": public(cpp),
        "java": public(java),
        "same": public(cpp) == public(java),
        "cpp_schema": cpp["schema_version"],
        "java_schema": java["schema_version"],
        "both_reject_injected_bad_cases": cpp["cases"][-1]["status"] == "INVALID_TEST"
        and java["cases"][-1]["status"] == "INVALID_TEST",
    }
    if parity != expected["native-equivalence:"]:
        raise RuntimeError(
            "C++/Java native parity differs from independently authored oracle"
        )


def _native_abstract_interface_negative_smoke(bundle: Path, workspace: Path) -> None:
    """Verify incomplete native Cases are rejected by actual C++/Java compilers.

    A positive native smoke alone cannot prove abstract API enforcement. Both
    intentionally incomplete sources must reach the compiler and be rejected.
    """
    root = workspace / "native-abstract-negative"
    root.mkdir()
    cpp = root / "MissingCase.cpp"
    cpp.write_text(
        '#include "tests.hpp"\n'
        'struct MissingCase final : rqg::tests::ITestCase {};\n'
        'int main() { MissingCase cannot_instantiate; }\n',
        encoding="utf-8",
    )
    java = root / "MissingCase.java"
    java.write_text(
        'import rqg.contracts.TestUnit;\n'
        'public final class MissingCase implements TestUnit.ITestCase {}\n',
        encoding="utf-8",
    )
    for language, command in (
        ("C++", ["c++", "-std=c++17", "-fsyntax-only", "-I",
                 str(bundle / "runtime/contracts/cpp"), str(cpp)]),
        ("Java", ["javac", "-d", str(root),
                  str(bundle / "runtime/contracts/java/TestUnit.java"), str(java)]),
    ):
        result = subprocess.run(command, text=True, capture_output=True,
                                check=False, timeout=45, cwd=workspace)
        if result.returncode == 0:
            raise RuntimeError(f"{language} accepted a missing abstract Case implementation")
        if language == "C++" and not any(word in result.stderr for word in
                                          ("abstract", "pure virtual")):
            raise RuntimeError("C++ negative check failed for unrelated reasons: "
                               + result.stderr[-700:])
        if language == "Java" and not any(word in result.stderr for word in
                                           ("abstract", "override")):
            raise RuntimeError("Java negative check failed for unrelated reasons: "
                               + result.stderr[-700:])


def _native_ts_go_interface_smoke(bundle: Path, workspace: Path) -> None:
    """Real compiler checks for TypeScript/Go Case interfaces and rejection.

    This is interface acceptance, not a TypeScript/Go test-runner or IR migration.
    No network-based compiler installation or skipped tooling is permitted.
    """
    ts_source = bundle / "runtime/contracts/typescript/testunit.ts"
    ts_positive = bundle / "tools/release_checks/fixtures/native-testunit/TypeScriptNativeCase.ts"
    go_source = bundle / "runtime/contracts/go/testunit.go"
    go_positive = bundle / "runtime/contracts/go/testunit_test.go"
    for required in (ts_source, ts_positive, go_source, go_positive):
        if not required.is_file():
            raise RuntimeError(f"missing native interface source: {required}")
    ts_compiled = workspace / "ts-contract-compiled"
    _execute([
        "tsc", "--strict", "--target", "es2022", "--module", "commonjs",
        "--moduleResolution", "node", "--rootDir", str(bundle),
        "--outDir", str(ts_compiled), str(ts_source), str(ts_positive),
    ], timeout=60)
    ts_entry = ts_compiled / "tools/release_checks/fixtures/native-testunit/TypeScriptNativeCase.js"
    observed = json.loads(_execute(["node", str(ts_entry)], timeout=30))
    expected = {"case_id": "sum-7", "kind": "RETURN", "matched": True}
    if observed != expected:
        raise RuntimeError(f"TypeScript ITestCase positive contract mismatch: {observed}")

    negative_ts = workspace / "IncompleteCase.ts"
    negative_ts.write_text(
        f'import type {{ ITestCase }} from "{ts_source.as_posix()[:-3]}";\n'
        'class IncompleteCase implements ITestCase { get_case_id(): string { return "broken"; } }\n'
        'void IncompleteCase;\n', encoding="utf-8"
    )
    rejected_ts = subprocess.run(
        ["tsc", "--strict", "--noEmit", "--target", "es2022",
         "--moduleResolution", "node", str(negative_ts)],
        capture_output=True, text=True, check=False, timeout=60,
    )
    if rejected_ts.returncode == 0 or "TS2420" not in rejected_ts.stdout + rejected_ts.stderr:
        raise RuntimeError(
            "TypeScript compiler did not reject missing ITestCase methods: "
            + (rejected_ts.stdout + rejected_ts.stderr)[-1000:]
        )

    go_root = workspace / "go-native-contract"
    go_root.mkdir()
    shutil.copy2(go_source, go_root / "testunit.go")
    shutil.copy2(go_positive, go_root / "testunit_test.go")
    go_env = {**os.environ, "GOWORK": "off", "GO111MODULE": "off", "GOPROXY": "off"}
    go_good = subprocess.run(
        ["go", "test", "-count=1", "-v", "."], cwd=go_root, env=go_env,
        capture_output=True, text=True, check=False, timeout=90,
    )
    if go_good.returncode or "--- PASS: TestRequiredMethodsAndCaseIdentity" not in go_good.stdout:
        raise RuntimeError("Go interface positive contract failed: " + go_good.stderr[-1000:])
    # Isolate the negative provider from positive tests, so a failure must
    # be caused by the real Go interface, not some unrelated prior test.
    go_bad = workspace / "go-missing-case"
    go_bad.mkdir()
    shutil.copy2(go_source, go_bad / "testunit.go")
    (go_bad / "bad_test.go").write_text(
        "package testunit\n"
        "type MissingCase struct{}\n"
        "var _ ITestCase = (*MissingCase)(nil)\n", encoding="utf-8"
    )
    rejected_go = subprocess.run(
        ["go", "test", "-count=1", "."], cwd=go_bad, env=go_env,
        capture_output=True, text=True, check=False, timeout=90,
    )
    if rejected_go.returncode == 0 or "does not implement ITestCase" not in (rejected_go.stdout + rejected_go.stderr):
        raise RuntimeError(
            "Go compiler did not reject incomplete ITestCase: "
            + (rejected_go.stdout + rejected_go.stderr)[-1000:]
        )


def _source_witness_release_smoke(bundle: Path, workspace: Path) -> None:
    """Verify source-bound R2/R3 evidence survives official audit and readonly verify."""
    rows = json.loads(
        (bundle / "tools/release_checks/fixtures/native-oracle-b69.json").read_text(
            encoding="utf-8"
        )
    )["cases"]
    row = next(item for item in rows if item["kind"] == "r2r3")
    project = workspace / "r2r3-witness"
    project.mkdir()
    for name, entry in row["samples"].items():
        (project / name).write_text(entry["source"], encoding="utf-8")
    command = [sys.executable, "-B", str(bundle / "scripts/quality_guard.py")]
    audit = _scan_with_heartbeat([*command, "audit", str(project)])
    report = project / "修改说明.md"
    if not report.is_file():
        raise RuntimeError(
            "source-witness audit did not produce a report: " + audit.stderr[-1000:]
        )
    content = report.read_text(encoding="utf-8")
    before = hashlib.sha256(report.read_bytes()).hexdigest()
    verify = _scan_with_heartbeat([*command, "verify", str(project)])
    actual = {
        "audit_return": audit.returncode,
        "integrity_audit": "Skill 完整性门禁：PASS" in audit.stdout,
        "witnesses_section": "RQG:AUTO:BEGIN source_bound_witnesses" in content,
        "hashes": [
            hashlib.sha256(entry["source"].encode()).hexdigest() in content
            for entry in row["samples"].values()
        ],
        "markers": [
            marker in content
            for entry in row["samples"].values()
            for marker in entry["must_contain"]
        ],
        "verify_return": verify.returncode,
        "integrity_verify": "Skill 完整性门禁：PASS" in verify.stdout,
        "report_unchanged": hashlib.sha256(report.read_bytes()).hexdigest() == before,
    }
    if actual != row["expected"]:
        raise RuntimeError(
            "R2/R3 release witness differs from authored oracle: " + repr(actual)
        )


def _qg003_dual_oracle_release_smoke(bundle: Path, workspace: Path) -> None:
    """Cross-check scanner Findings and executable return values using frozen cases.

    The two real operations span source analysis and customer code execution;
    they are exercised as a release integration contract rather than a
    synthetic TestCase method hiding an entire execution workflow.
    """
    dataset = bundle / "tools/release_checks/fixtures/qg003-business-v1.json"
    if not dataset.is_file():
        raise RuntimeError("QG003 release fixture missing: " + str(dataset))
    script = r"""
import json
import tempfile
from pathlib import Path
from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner
rows=json.loads(Path('tools/release_checks/fixtures/qg003-business-v1.json').read_text(encoding='utf-8'))['cases']
if {row['kind'] for row in rows} != {'TP','TN'}:
    raise RuntimeError('QG003 independent polarity oracles are incomplete')
for row in rows:
    if row['kind'] != ('TP' if row['expected_findings'] else 'TN'):
        raise RuntimeError('Inconsistent frozen finding oracle: ' + row['case_id'])
    with tempfile.TemporaryDirectory(prefix='qg003-release-') as folder:
        root=Path(folder)
        (root/'subject.py').write_text(row['source'],encoding='utf-8')
        actual=sum(item.code=='QG003' for item in RepositoryScanner(root,GuardConfig()).scan().findings)
    if actual != row['expected_findings']:
        raise RuntimeError('QG003 Finding mismatch: '+row['case_id']+f' {actual} != {row["expected_findings"]}')
    scope={}
    exec(compile(row['source'],'subject.py','exec'),scope)
    outcome=(scope['Index'](tuple(row['constructor_args'])).classify(*row['args'])
             if row['symbol']=='Index.classify' else scope[row['symbol']](*row['args']))
    if type(outcome) is not type(row['expected_result']) or outcome != row['expected_result']:
        raise RuntimeError('QG003 business outcome mismatch: '+row['case_id'])
print(f'QG003 dual oracle: {len(rows)} PASS')
"""
    _execute([sys.executable, "-c", script], cwd=bundle, timeout=90)


def _gitless_release_smoke(destination: Path, workspace: Path) -> None:
    """Audit and verify a real Gitless repository using the installed Skill."""
    root = workspace / "gitless-fixture"
    root.mkdir()
    (root / "service.py").write_text(
        "def total(values: list[int]) -> int:\n    return sum(values)\n",
        encoding="utf-8",
    )
    command = [sys.executable, "-B", str(destination / "scripts/quality_guard.py")]
    result = _scan_with_heartbeat([*command, "audit", str(root)], cwd=workspace)
    report = root / "修改说明.md"
    if result.returncode not in (3, 5) or not report.is_file():
        raise RuntimeError(
            "Gitless official audit failed: "
            + result.stdout[-1000:]
            + result.stderr[-1000:]
        )
    before = report.read_bytes()
    verified = _scan_with_heartbeat([*command, "verify", str(root)], cwd=workspace)
    if verified.returncode not in (3, 5) or report.read_bytes() != before:
        raise RuntimeError(
            "Gitless report verify failed or mutated report: "
            + verified.stdout[-1000:]
            + verified.stderr[-1000:]
        )


def _report_mutation_release_smoke(destination: Path, workspace: Path) -> None:
    """Prove official verify rejects a tampered machine-owned schema identity."""
    repo = workspace / "audit-fixture"
    report = repo / "修改说明.md"
    original = report.read_bytes()
    lines = original.splitlines(keepends=True)
    matches = [
        index for index, line in enumerate(lines) if line.startswith(b"report_schema: ")
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "official audit did not produce exactly one report schema identity"
        )
    lines[matches[0]] = b"report_schema: deliberately-invalid-contract\n"
    altered = b"".join(lines)
    report.write_bytes(altered)
    try:
        result = _scan_with_heartbeat(
            [
                sys.executable,
                "-B",
                str(destination / "scripts/quality_guard.py"),
                "verify",
                str(repo),
                "--profile",
                "qg-example-profile",
                "--mode",
                "ignore-diff",
            ],
            cwd=workspace,
        )
        output = result.stdout + result.stderr
        if (
            result.returncode == 0
            or "报告 schema 不匹配" not in output
            or report.read_bytes() != altered
        ):
            raise RuntimeError(
                "official verify did not reject the precise tampered report schema"
            )
    finally:
        report.write_bytes(original)


def main(argv: list[str] | None = None) -> int:
    """Validate the exact release artifact, then perform isolated installation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive", type=Path, help="full release ZIP from tools/build_release.py"
    )
    args = parser.parse_args(argv)
    artifact = args.archive.resolve(strict=True)
    digest, count = _archive_contract(artifact)
    print(f"release ZIP structure: PASS ({count} files, SHA256 {digest})", flush=True)
    with tempfile.TemporaryDirectory(prefix="rqg-release-verify-") as directory:
        workspace = Path(directory)
        with zipfile.ZipFile(artifact) as archive:
            archive.extractall(workspace / "release")
        bundle = workspace / "release" / _ROOT
        _run_offline_install(bundle, workspace)
        print("offline dependency installation and CLI smoke: PASS", flush=True)
        _install_and_hook_smoke(bundle, workspace)
        _host_resource_release_smoke(bundle, workspace)
        _unicode_git_smoke(bundle, workspace)
        print("deployment and Git hook smoke: PASS", flush=True)
        installed = workspace / "host/.agents/skills/repository-quality-guard"
        _official_audit_smoke(installed, workspace)
        print("installed Profile, audit and verify smoke: PASS", flush=True)
        _source_catalog_smoke(installed, workspace)
        _catalog_ledger_and_generated_docs_smoke(installed, workspace)
        _rule_lookup_smoke(installed, workspace)
        _evidence_lookup_release_smoke(bundle, workspace)
        _evidence_cache_release_smoke(bundle, workspace)
        _snapshot_equivalence_release_smoke(bundle, workspace)
        _graph_public_cli_release_smoke(bundle, workspace)
        _integrity_mutation_smoke(bundle, workspace)
        _cache_worker_release_smoke(bundle, workspace)
        _worker_lifecycle_release_smoke(bundle, workspace)
        _native_cpp_release_smoke(bundle, workspace)
        _native_c_java_release_smoke(bundle, workspace)
        _native_abstract_interface_negative_smoke(bundle, workspace)
        _native_ts_go_interface_smoke(bundle, workspace)
        _source_witness_release_smoke(bundle, workspace)
        _qg003_dual_oracle_release_smoke(bundle, workspace)
        # Gitless audit and post-audit report mutation were not exercised by
        # the Git-backed pipeline; preserve both independent negative witnesses.
        _gitless_release_smoke(installed, workspace)
        _report_mutation_release_smoke(installed, workspace)
        # Native installation/hooks, Profile selection, delta policy, debt and
        # worker lifecycle already have real checks above.  Do not call seven
        # phantom release_checks scripts or run equivalent smokes twice.
        print("catalog, rule lookup, integrity, cache and native C++: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
