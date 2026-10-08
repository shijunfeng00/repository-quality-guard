"""安装/验证 Repository Quality Guard 锁定依赖；canonical full-repository ZIP 支持离线介质，`.agents` 不携带介质。"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import signal
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

# Allow the documented standalone file entrypoint as well as `python -m runtime...`.
# The installed Skill may live under `.agents`; anchor imports to this file,
# never to the caller's current working directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.src.bootstrap_settings import BootstrapSettings, load_bootstrap_settings
from runtime.src.process_lifecycle import process_alive

_LOCK_FILE = "runtime/dependencies.lock.json"
_OFFLINE_WHEELS = "offline/wheelhouse"
_OFFLINE_NODE_ARCHIVE = "offline/node_modules.zip"
_NETWORK_INSTALL_TIMEOUT_SECONDS = 90
_CONNECT_TIMEOUT_SECONDS = 3
_CONNECT_ATTEMPTS = 3


def _log(message: str) -> None:
    """Write bootstrap decisions to the agent-visible error stream."""
    sys.stderr.write(f"[QG] {message}\n")
    sys.stderr.flush()


def _network_available(url: str) -> bool:
    """Probe the exact package registry before any online fallback."""
    for attempt in range(1, _CONNECT_ATTEMPTS + 1):
        _log(
            f"检查网络 {attempt}/{_CONNECT_ATTEMPTS}: {url} (timeout={_CONNECT_TIMEOUT_SECONDS}s)"
        )
        try:
            with urllib.request.urlopen(
                url, timeout=_CONNECT_TIMEOUT_SECONDS
            ) as response:
                response.read(1)
            return True
        except urllib.error.HTTPError:
            return True
        except (OSError, urllib.error.URLError) as error:
            _log(f"网络探测失败: {error}")
    _log(
        "网络不可用；无法在线安装锁定依赖。完整 skill.zip 应自带锁定依赖介质；若当前安装形态缺少本地介质或缓存，请请求用户授权联网安装，或让用户提供完整 skill.zip / 重新安装。"
    )
    return False


def _read_lock(bundle: Path) -> dict[str, object]:
    """读取发布内依赖锁并验证固定 schema。"""
    path = bundle / _LOCK_FILE
    if not path.is_file():
        raise RuntimeError(f"缺少 {_LOCK_FILE}。")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if type(payload) is not dict or not {
        "schema",
        "registry",
        "python",
        "node",
    }.issubset(payload):
        raise RuntimeError("runtime dependencies lock 缺少必需字段。")
    if payload["schema"] != "repository-quality-guard/dependencies-v1":
        raise RuntimeError("runtime dependencies lock schema 非法。")
    registry = payload["registry"]
    if type(registry) is not dict or set(registry) != {"python", "node"}:
        raise RuntimeError("runtime dependencies lock 缺少 registry 地址。")
    return payload


def _registry_url(
    lock: dict[str, object], kind: str, settings: BootstrapSettings
) -> str:
    """Return the configured registry for one dependency ecosystem."""
    override = settings.python_index_url if kind == "python" else settings.npm_registry
    if override:
        return override.strip()
    registry = lock["registry"]
    if type(registry) is not dict or type(registry[kind]) is not str:
        raise RuntimeError(f"依赖锁缺少 {kind} registry 地址。")
    return registry[kind]


def _cache_root(settings: BootstrapSettings) -> Path:
    """Return the normalized repository-external dependency cache root."""
    return settings.cache_root


def _bootstrap_owner_pid(owner_file: Path) -> int | None:
    """Read a bootstrap-lock owner PID from the canonical lock envelope.

    Missing owner files are treated as an incomplete lock publication so the
    caller can use lock age to distinguish a concurrent creator from stale state.
    Malformed published envelopes are contract failures and are not downgraded to
    an anonymous lock.
    """
    try:
        raw = owner_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RuntimeError(f"无法读取依赖安装锁 owner: {owner_file}") from error
    try:
        owner = json.loads(raw)
    except ValueError as error:
        raise RuntimeError(f"依赖安装锁 owner JSON 非法: {owner_file}") from error
    if type(owner) is not dict or "pid" not in owner or type(owner["pid"]) is not int:
        raise RuntimeError(f"依赖安装锁 owner 协议非法: {owner_file}")
    return owner["pid"] if owner["pid"] > 0 else None


@contextmanager
def _bootstrap_single_flight(settings: BootstrapSettings):
    """Serialize dependency preparation for one user cache to avoid concurrent pip/npm writes."""
    root = _cache_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "bootstrap.lock"
    started = time.monotonic()
    last_log = 0.0
    while True:
        try:
            lock.mkdir()
        except FileExistsError:
            acquired = False
        else:
            acquired = True
        if acquired:
            (lock / "owner.json").write_text(
                json.dumps({"pid": os.getpid(), "started_at": time.time()}),
                encoding="utf-8",
            )
            break
        owner_file = lock / "owner.json"
        pid = _bootstrap_owner_pid(owner_file)
        if pid is None:
            try:
                lock_age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if lock_age > 5:
                shutil.rmtree(lock, ignore_errors=True)
                _log("清理无 owner 的失效依赖安装锁。")
                continue
        elif not process_alive(pid):
            shutil.rmtree(lock, ignore_errors=True)
            _log(f"清理失效依赖安装锁：owner pid={pid} 已退出。")
            continue
        now = time.monotonic()
        if last_log == 0.0 or now - last_log >= 5:
            detail = f" pid={pid}" if pid is not None else ""
            _log(
                "另一个 Repository Quality Guard 正在准备运行环境；"
                f"正在等待{detail} elapsed={now - started:.1f}s。"
            )
            last_log = now
        time.sleep(0.25)
    try:
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def _managed_wheelhouse(settings: BootstrapSettings) -> Path:
    """Return the managed user cache used to persist verified Python wheels."""
    return _cache_root(settings) / "python" / "wheelhouse"


def _seed_wheel_cache(bundle: Path, settings: BootstrapSettings) -> Path | None:
    """Copy release-provided wheels into the managed cache when media exists."""
    source = bundle / _OFFLINE_WHEELS
    if not source.is_dir() or not any(source.glob("*.whl")):
        return None
    target = _managed_wheelhouse(settings)
    target.mkdir(parents=True, exist_ok=True)
    for wheel in source.glob("*.whl"):
        destination = target / wheel.name
        if (
            not destination.is_file()
            or destination.stat().st_size != wheel.stat().st_size
        ):
            shutil.copy2(wheel, destination)
    return target


def _local_wheelhouses(bundle: Path, settings: BootstrapSettings) -> tuple[Path, ...]:
    """Return unique usable local wheelhouses in deterministic preference order."""
    candidates = (bundle / _OFFLINE_WHEELS, _managed_wheelhouse(settings))
    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen or not resolved.is_dir() or not any(resolved.glob("*.whl")):
            continue
        seen.add(resolved)
        result.append(resolved)
    return tuple(result)


def _python_ready(lock: dict[str, object]) -> bool:
    """验证锁定 Python 包及 Tree-sitter Python parser 可实际加载。"""
    packages = lock["python"]
    if not isinstance(packages, dict):
        raise RuntimeError("dependencies.lock.json python 字段非法。")
    for name, version in packages.items():
        try:
            installed_version = importlib.metadata.version(str(name))
        except importlib.metadata.PackageNotFoundError:
            return False
        if installed_version != str(version):
            return False
    try:
        from apted import APTED, Config
        from tree_sitter import Language, Parser
        import tree_sitter_python
    except ImportError:
        return False
    parser = Parser(Language(tree_sitter_python.language()))
    return APTED is not None and Config is not None and parser is not None


def _run(
    command: list[str],
    environment: Mapping[str, str],
    timeout: float | None = None,
) -> bool:
    """执行安装命令；超时或中断时清理整个安装进程树。"""
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            env=dict(environment),
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        return process.wait(timeout=timeout) == 0
    except subprocess.TimeoutExpired:
        executable = Path(command[0]).name if command else "dependency command"
        _log(f"依赖安装超时：{executable} 超过 {timeout:g}s，正在清理安装进程树。")
        return False
    finally:
        if process is not None and process.poll() is None:
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        timeout=5,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)


def _install_python(
    bundle: Path, lock: dict[str, object], settings: BootstrapSettings
) -> None:
    """优先使用随包 wheelhouse；仅缺少/失效时才尝试有界联网安装。"""
    if _python_ready(lock):
        _log("Python 锁定依赖已就绪。")
        return
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    packages = lock["python"]
    if not isinstance(packages, dict):
        raise RuntimeError("dependencies.lock.json python 字段非法。")
    requirements = [f"{name}=={version}" for name, version in sorted(packages.items())]
    _seed_wheel_cache(bundle, settings)
    wheelhouses = _local_wheelhouses(bundle, settings)
    if wheelhouses:
        for wheelhouse in wheelhouses:
            _log(f"发现本地 Python wheelhouse，优先离线安装: {wheelhouse}")
            if _run(
                [*base, "--no-index", "--find-links", str(wheelhouse), *requirements],
                environment=settings.environment,
                timeout=60,
            ):
                importlib.invalidate_caches()
                if _python_ready(lock):
                    _log("离线 Python 依赖安装完成。")
                    return
        _log("本地 wheel 不适用于当前 Python/操作系统，准备检查网络。")
    else:
        _log("未找到可用的本地 Python wheelhouse。")
    if settings.offline_only:
        raise RuntimeError("缺少可用的锁定 Python 依赖；离线模式禁止联网。")
    if not _network_available(_registry_url(lock, "python", settings)):
        raise RuntimeError(
            "缺少可用的锁定 Python 依赖，且 PyPI 网络不可用。完整 skill.zip 应包含兼容 wheel；若当前安装形态没有本地介质或缓存，请请求用户授权联网安装，或让用户提供完整 skill.zip / 重新安装。"
        )
    _log("正在从 PyPI 在线安装锁定 Python 依赖。")
    if _run(
        [*base, "--retries", "2", "--timeout", "10", *requirements],
        environment=settings.environment,
        timeout=_NETWORK_INSTALL_TIMEOUT_SECONDS,
    ):
        importlib.invalidate_caches()
        if _python_ready(lock):
            _log("在线 Python 依赖安装完成。")
            return
    raise RuntimeError(
        "锁定 Python 依赖在线安装失败。请根据上方 pip 诊断处理；若需要再次联网，请先请求用户授权，或让用户提供完整 skill.zip / 重新安装以恢复本地依赖介质。"
    )


def _node_packages(lock: dict[str, object]) -> dict[str, str]:
    """返回 node package -> version 映射。"""
    packages = lock["node"]
    if not isinstance(packages, dict):
        raise RuntimeError("dependencies.lock.json node 字段非法。")
    return {str(name): str(version) for name, version in packages.items()}


def _node_probe(
    node_modules: Path, lock: dict[str, object], settings: BootstrapSettings
) -> bool:
    """用 Node 实际加载 TypeScript/PostCSS 并校验版本。"""
    node = shutil.which("node")
    if node is None or not node_modules.is_dir():
        return False
    packages = _node_packages(lock)
    script = r"""
const path = require('path');
const fs = require('fs');
const root = process.argv[1];
const expected = JSON.parse(process.argv[2]);
const ts = require(path.join(root, 'typescript', 'lib', 'typescript.js'));
if (String(ts.version) !== String(expected.typescript)) process.exit(3);
const postcssPkg = require(path.join(root, 'postcss', 'package.json'));
if (String(postcssPkg.version) !== String(expected.postcss)) process.exit(4);
for (const name of ['nanoid', 'picocolors', 'source-map-js']) {
  let packagePath = path.join(root, name, 'package.json');
  if (!fs.existsSync(packagePath)) packagePath = path.join(root, 'postcss', 'node_modules', name, 'package.json');
  const pkg = require(packagePath);
  if (String(pkg.version) !== String(expected[name])) process.exit(5);
}
"""
    try:
        result = subprocess.run(
            [
                node,
                "-e",
                script,
                str(node_modules),
                json.dumps(packages, sort_keys=True),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=settings.environment,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _copy_offline_node_modules(bundle: Path, node_modules: Path) -> bool:
    """把 `.skill` 的离线 Node archive 解到仓库外 cache。"""
    archive = bundle / _OFFLINE_NODE_ARCHIVE
    if not archive.is_file():
        return False
    target_root = node_modules.parent
    temporary = target_root.with_name(target_root.name + ".offline-new")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True, exist_ok=True)
    try:
        shutil.unpack_archive(str(archive), str(temporary / "node_modules"), "zip")
    except (OSError, ValueError):
        shutil.rmtree(temporary, ignore_errors=True)
        _log("离线 Node payload 解包失败，将检查在线安装条件。")
        return False
    target_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(target_root, ignore_errors=True)
    os.replace(temporary, target_root)
    return True


def _existing_node_modules(
    lock: dict[str, object], settings: BootstrapSettings
) -> Path | None:
    """优先复用宿主已有且版本完全匹配的 Node packages。"""
    candidates: list[Path] = []
    if settings.node_modules:
        candidates.append(Path(settings.node_modules).expanduser())
    npm = shutil.which("npm")
    if npm is not None:
        try:
            result = subprocess.run(
                [npm, "root", "-g"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="surrogateescape",
                env=settings.environment,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                candidates.append(Path(result.stdout.strip()))
        except (OSError, subprocess.TimeoutExpired):
            _log("宿主 npm 全局依赖探测失败，继续检查离线介质。")
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if _node_probe(resolved, lock, settings):
            return resolved
    return None


def _install_node_offline(
    bundle: Path,
    node_modules: Path,
    lock: dict[str, object],
    settings: BootstrapSettings,
) -> Path | None:
    """Install the locked Node payload from bundled offline media when present."""
    if (bundle / _OFFLINE_NODE_ARCHIVE).is_file():
        _log("发现离线 Node payload，优先离线安装。")
    else:
        _log("未找到离线 Node payload。")
        return None
    if not _copy_offline_node_modules(bundle, node_modules):
        return None
    if not _node_probe(node_modules, lock, settings):
        return None
    _log("离线 Node parser 依赖安装完成。")
    return node_modules


def _install_node_online(
    node_modules: Path,
    packages: dict[str, str],
    lock: dict[str, object],
    settings: BootstrapSettings,
) -> Path:
    """Install the locked Node payload from npm with bounded network execution."""
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError(
            "缺少 npm，且没有可用的离线 Node payload；请安装 npm 或提供离线介质。"
        )
    if not _network_available(_registry_url(lock, "node", settings)):
        raise RuntimeError(
            "缺少锁定 Node parser 依赖，且 npm registry 网络不可用。完整 skill.zip 应包含 Node 依赖介质；若当前安装形态没有本地介质或缓存，请请求用户授权联网安装，或让用户提供完整 skill.zip / 重新安装。"
        )
    _log("正在从 npm registry 在线安装锁定 Node parser 依赖。")
    prefix = node_modules.parent
    prefix.mkdir(parents=True, exist_ok=True)
    specs = [f"{name}@{version}" for name, version in sorted(packages.items())]
    command = [
        npm,
        "install",
        "--prefix",
        str(prefix),
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--no-save",
        "--fetch-retries=2",
        "--fetch-timeout=15000",
        *specs,
    ]
    installed = _run(
        command,
        environment=settings.environment,
        timeout=_NETWORK_INSTALL_TIMEOUT_SECONDS,
    )
    if installed and _node_probe(node_modules, lock, settings):
        _log("在线 Node parser 依赖安装完成。")
        return node_modules
    raise RuntimeError(
        "锁定 Node parser 依赖在线安装失败。请根据上方 npm 诊断处理；若需要再次联网，请先请求用户授权，或让用户提供完整 skill.zip / 重新安装以恢复本地依赖介质。"
    )


def _install_node(
    bundle: Path, lock: dict[str, object], settings: BootstrapSettings
) -> Path | None:
    """优先复用缓存/离线 payload；仅在允许时执行有界 npm 安装。"""
    if shutil.which("node") is None:
        raise RuntimeError("缺少 Node.js 运行环境；请安装 Node.js 后重新执行安装器。")
    existing = _existing_node_modules(lock, settings)
    if existing is not None:
        _log(f"Node 锁定依赖已就绪: {existing}")
        return existing
    packages = _node_packages(lock)
    identity = "-".join(f"{name}-{packages[name]}" for name in sorted(packages))
    node_modules = _cache_root(settings) / "node" / identity / "node_modules"
    if _node_probe(node_modules, lock, settings):
        _log(f"Node 锁定依赖缓存已就绪: {node_modules}")
        return node_modules
    offline = _install_node_offline(bundle, node_modules, lock, settings)
    if offline is not None:
        return offline
    if settings.offline_only:
        raise RuntimeError("缺少可用的锁定 Node parser 依赖；离线模式禁止联网。")
    return _install_node_online(node_modules, packages, lock, settings)


def _prune_managed_node_cache(
    keep_root: Path | None, settings: BootstrapSettings
) -> None:
    """删除 QG 自己管理的旧 Node 依赖版本；宿主 npm/pip cache 不归 Guard 管理。"""
    node_root = _cache_root(settings) / "node"
    if not node_root.is_dir():
        return
    keep = keep_root.resolve() if keep_root is not None else None
    for candidate in node_root.iterdir():
        if not candidate.is_dir():
            continue
        if keep is not None and candidate.resolve() == keep:
            continue
        shutil.rmtree(candidate)
    if not any(node_root.iterdir()):
        node_root.rmdir()


def _system_install_command() -> list[str] | None:
    """返回显式 opt-in 时可使用的常见系统包管理器命令。"""
    if shutil.which("apt-get"):
        return ["apt-get", "install", "-y", "nodejs", "npm", "clang"]
    if shutil.which("dnf"):
        return ["dnf", "install", "-y", "nodejs", "npm", "clang"]
    if shutil.which("yum"):
        return ["yum", "install", "-y", "nodejs", "npm", "clang"]
    if shutil.which("pacman"):
        return ["pacman", "-S", "--noconfirm", "nodejs", "npm", "clang"]
    if shutil.which("brew"):
        return ["brew", "install", "node", "llvm"]
    return None


def _maybe_install_system(settings: BootstrapSettings) -> None:
    """仅在显式 opt-in 时尝试补齐 Node/Clang 系统可执行文件。"""
    if not settings.install_system_dependencies:
        return
    if shutil.which("node") and (shutil.which("clang++") or shutil.which("clang")):
        return
    command = _system_install_command()
    if command is None:
        raise RuntimeError("未发现支持的系统包管理器，无法自动安装 Node/Clang。")
    if os.name != "nt" and os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if sudo is None:
            raise RuntimeError("系统依赖缺失且当前非 root；请安装 Node.js/npm/Clang。")
        command = [sudo, "-n", *command]
    if not _run(command, environment=settings.environment):
        raise RuntimeError("系统 Node/Clang 自动安装失败。")


def prepare_runtime_dependencies(
    bundle: Path,
    settings: BootstrapSettings | None = None,
    prune_managed: bool = False,
) -> Path | None:
    """Install or verify locked runtime dependencies from normalized settings.

    Args:
        bundle: Current Skill/runtime bundle.
        settings: Optional already-normalized bootstrap settings.
        prune_managed: Keep only the active QG-managed Node dependency generation.

    Returns:
        Usable node_modules root, or None when the lock has no Node payload.
    """
    bundle = bundle.resolve()
    _log(f"runtime bootstrap: {bundle}")
    resolved = load_bootstrap_settings() if settings is None else settings
    with _bootstrap_single_flight(resolved):
        _maybe_install_system(resolved)
        lock = _read_lock(bundle)
        _install_python(bundle, lock, resolved)
        node_modules = _install_node(bundle, lock, resolved)
        if prune_managed:
            managed = None
            if node_modules is not None:
                candidate = node_modules.parent
                managed_root = (_cache_root(resolved) / "node").resolve()
                if candidate.parent.resolve() == managed_root:
                    managed = candidate
            _prune_managed_node_cache(managed, resolved)
    if settings is None and node_modules is not None:
        os.environ["RQG_NODE_MODULES"] = str(node_modules)
    return node_modules


def main() -> int:
    """Install or verify the locked runtime dependencies for one RQG bundle.

    Returns:
        Process exit code: ``0`` on success and ``2`` when dependency
        preparation fails with an actionable bootstrap error.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "bundle",
        nargs="?",
        default=str(Path(__file__).resolve().parents[1]),
        help="Skill 根目录；默认使用当前安装。",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="禁止联网；优先复用精确环境，否则只使用 canonical ZIP 内离线 wheel/node payload。",
    )
    parser.add_argument(
        "--install-system",
        action="store_true",
        help="显式允许通过系统包管理器安装 Node.js/npm/Clang。",
    )
    args = parser.parse_args()
    settings = load_bootstrap_settings(
        force_offline=args.offline, force_system_install=args.install_system
    )
    try:
        node_modules = prepare_runtime_dependencies(Path(args.bundle), settings)
    except (OSError, RuntimeError, ValueError) as error:
        _log(f"环境准备失败：{error}")
        return 2
    sys.stdout.write("Repository Quality Guard runtime dependencies: PASS\n")
    if node_modules is not None:
        sys.stdout.write(f"RQG_NODE_MODULES={node_modules}\n")
    return 0


if __name__ == "__main__":
    if str(Path(__file__).resolve().parents[1]) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runtime.src.process_lifecycle import install_parent_death_guard

    signal.signal(signal.SIGTERM, signal.default_int_handler)
    install_parent_death_guard()
    try:
        exit_code = main()
    except KeyboardInterrupt:
        _log("环境准备被终止；安装子进程树已请求清理。")
        exit_code = 143
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(exit_code))
