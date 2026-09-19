"""安装/验证 Repository Quality Guard 锁定依赖；`.skill.zip` 支持离线介质，`.agents` 不携带介质。"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping

_LOCK_FILE = "runtime/dependencies.lock.json"
_OFFLINE_WHEELS = "offline/wheelhouse"
_OFFLINE_NODE_ARCHIVE = "offline/node_modules.zip"
_NODE_ENV = "RQG_NODE_MODULES"
_OFFLINE_ENV = "RQG_OFFLINE_ONLY"
_CACHE_ENV = "RQG_DEPENDENCY_CACHE"
_SYSTEM_ENV = "RQG_INSTALL_SYSTEM_DEPS"


def _read_lock(bundle: Path) -> dict[str, object]:
    """读取发布内依赖锁并验证固定 schema。"""
    path = bundle / _LOCK_FILE
    if not path.is_file():
        raise RuntimeError(f"缺少 {_LOCK_FILE}。")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "repository-quality-guard/dependencies-v1":
        raise RuntimeError("runtime dependencies lock schema 非法。")
    return payload


def _cache_root(environment: Mapping[str, str]) -> Path:
    """返回仓库外的用户级依赖缓存根目录。"""
    explicit = environment.get(_CACHE_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == "nt":
        base = environment.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / "repository-quality-guard" / "dependencies"
    xdg = environment.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "repository-quality-guard" / "dependencies"


def _version_matches(distribution: str, expected: str) -> bool:
    """判断当前 Python 环境中的 distribution 是否为锁定版本。"""
    try:
        return importlib.metadata.version(distribution) == expected
    except importlib.metadata.PackageNotFoundError:
        return False


def _python_ready(lock: dict[str, object]) -> bool:
    """验证锁定 Python 包及 Tree-sitter Python parser 可实际加载。"""
    packages = lock["python"]
    if not isinstance(packages, dict):
        raise RuntimeError("dependencies.lock.json python 字段非法。")
    if not all(
        _version_matches(str(name), str(version)) for name, version in packages.items()
    ):
        return False
    try:
        from apted import APTED, Config
        from tree_sitter import Language, Parser
        import tree_sitter_python

        parser = Parser(Language(tree_sitter_python.language()))
    except (ImportError, TypeError, ValueError):
        return False
    return APTED is not None and Config is not None and parser is not None


def _requirements(lock: dict[str, object]) -> list[str]:
    """把 Python lock 转成 pip 的精确 requirements。"""
    packages = lock["python"]
    if not isinstance(packages, dict):
        raise RuntimeError("dependencies.lock.json python 字段非法。")
    return [f"{name}=={version}" for name, version in sorted(packages.items())]


def _run(command: list[str], *, environment: Mapping[str, str]) -> bool:
    """执行安装命令并只返回成功状态；命令输出保留给调用者终端。"""
    return subprocess.run(command, check=False, env=dict(environment)).returncode == 0


def _install_python(
    bundle: Path, lock: dict[str, object], environment: Mapping[str, str]
) -> None:
    """在线优先安装 Python 依赖，失败后才使用 `.skill` 离线 wheelhouse。"""
    if _python_ready(lock):
        return
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    requirements = _requirements(lock)
    offline_only = environment.get(_OFFLINE_ENV, "") == "1"
    if not offline_only and _run([*base, *requirements], environment=environment):
        importlib.invalidate_caches()
        if _python_ready(lock):
            return
    wheelhouse = bundle / _OFFLINE_WHEELS
    if wheelhouse.is_dir() and any(wheelhouse.glob("*.whl")):
        if _run(
            [*base, "--no-index", "--find-links", str(wheelhouse), *requirements],
            environment=environment,
        ):
            importlib.invalidate_caches()
            if _python_ready(lock):
                return
    mode = "离线模式" if offline_only else "在线安装与离线 fallback"
    raise RuntimeError(f"锁定 Python 依赖安装失败（{mode}）。")


def _node_packages(lock: dict[str, object]) -> dict[str, str]:
    """返回 node package -> version 映射。"""
    packages = lock["node"]
    if not isinstance(packages, dict):
        raise RuntimeError("dependencies.lock.json node 字段非法。")
    return {str(name): str(version) for name, version in packages.items()}


def _node_cache(lock: dict[str, object], environment: Mapping[str, str]) -> Path:
    """根据锁定版本生成稳定的仓库外 node_modules cache。"""
    packages = _node_packages(lock)
    identity = "-".join(f"{name}-{packages[name]}" for name in sorted(packages))
    return _cache_root(environment) / "node" / identity


def _node_probe(
    node_modules: Path, lock: dict[str, object], environment: Mapping[str, str]
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
        env=dict(environment),
    )
    return result.returncode == 0


def _copy_offline_node_modules(bundle: Path, node_modules: Path) -> bool:
    """把 `.skill` 的离线 Node archive 解到仓库外 cache。"""
    archive = bundle / _OFFLINE_NODE_ARCHIVE
    if not archive.is_file():
        return False
    target_root = node_modules.parent
    temporary = target_root.with_name(target_root.name + ".offline-new")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True, exist_ok=True)
    shutil.unpack_archive(str(archive), str(temporary / "node_modules"), "zip")
    target_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(target_root, ignore_errors=True)
    os.replace(temporary, target_root)
    return True


def _existing_node_modules(
    lock: dict[str, object], environment: Mapping[str, str]
) -> Path | None:
    """优先复用宿主已有且版本完全匹配的 Node packages。"""
    candidates: list[Path] = []
    explicit = environment.get(_NODE_ENV, "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    npm = shutil.which("npm")
    if npm is not None:
        result = subprocess.run(
            [npm, "root", "-g"],
            check=False,
            capture_output=True,
            text=True,
            env=dict(environment),
        )
        if result.returncode == 0 and result.stdout.strip():
            candidates.append(Path(result.stdout.strip()))
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if _node_probe(resolved, lock, environment):
            return resolved
    return None


def _install_node(
    bundle: Path, lock: dict[str, object], environment: Mapping[str, str]
) -> Path | None:
    """在线优先安装 Node parser 依赖到用户 cache；无 Node 时推迟到真正需要时 fail-loud。"""
    node = shutil.which("node")
    if node is None:
        return None
    existing = _existing_node_modules(lock, environment)
    if existing is not None:
        return existing
    node_modules = _node_cache(lock, environment) / "node_modules"
    if _node_probe(node_modules, lock, environment):
        return node_modules
    offline_only = environment.get(_OFFLINE_ENV, "") == "1"
    npm = shutil.which("npm")
    packages = _node_packages(lock)
    prefix = node_modules.parent
    if not offline_only and npm is not None:
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
            *specs,
        ]
        if _run(command, environment=environment) and _node_probe(
            node_modules, lock, environment
        ):
            return node_modules
    if _copy_offline_node_modules(bundle, node_modules) and _node_probe(
        node_modules, lock, environment
    ):
        return node_modules
    if npm is None and not (bundle / _OFFLINE_NODE_ARCHIVE).is_file():
        return None
    mode = "离线模式" if offline_only else "npm 在线安装与离线 fallback"
    raise RuntimeError(f"锁定 Node parser 依赖安装失败（{mode}）。")


def _prune_managed_node_cache(
    keep_root: Path | None, environment: Mapping[str, str]
) -> None:
    """删除 QG 自己管理的旧 Node 依赖版本；宿主 npm/pip cache 不归 Guard 管理。"""
    node_root = _cache_root(environment) / "node"
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


def _maybe_install_system(environment: Mapping[str, str]) -> None:
    """仅在显式 opt-in 时尝试补齐 Node/Clang 系统可执行文件。"""
    if environment.get(_SYSTEM_ENV, "") != "1":
        return
    if shutil.which("node") and (shutil.which("clang++") or shutil.which("clang")):
        return
    command = _system_install_command()
    if command is None:
        raise RuntimeError("未发现支持的系统包管理器，无法自动安装 Node/Clang。")
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if sudo is None:
            raise RuntimeError("系统依赖缺失且当前非 root；请安装 Node.js/npm/Clang。")
        command = [sudo, "-n", *command]
    if not _run(command, environment=environment):
        raise RuntimeError("系统 Node/Clang 自动安装失败。")


def prepare_runtime_dependencies(
    bundle: Path,
    environment: Mapping[str, str] | None = None,
    *,
    prune_managed: bool = False,
) -> Path | None:
    """安装/验证锁定依赖并返回仓库外 node_modules 路径。

    Args:
        bundle: 当前 Skill/runtime bundle。
        environment: 可选显式环境。
        prune_managed: 部署写锁内为 True 时，只保留当前 QG 管理的 Node 依赖组。

    Returns:
        可用的 node_modules 根目录；仓库无 Node 时为 None。
    """
    bundle = bundle.resolve()
    source = dict(os.environ if environment is None else environment)
    _maybe_install_system(source)
    lock = _read_lock(bundle)
    _install_python(bundle, lock, source)
    node_modules = _install_node(bundle, lock, source)
    if prune_managed:
        managed = None
        if node_modules is not None:
            candidate = node_modules.parent
            managed_root = (_cache_root(source) / "node").resolve()
            if candidate.parent.resolve() == managed_root:
                managed = candidate
        _prune_managed_node_cache(managed, source)
    if environment is None and node_modules is not None:
        os.environ[_NODE_ENV] = str(node_modules)
    return node_modules


def main() -> int:
    """提供可独立执行的依赖安装入口。"""
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
        help="禁止联网；优先复用精确环境，否则只使用 `.skill.zip` 内离线 wheel/node payload。",
    )
    parser.add_argument(
        "--install-system",
        action="store_true",
        help="显式允许通过系统包管理器安装 Node.js/npm/Clang。",
    )
    args = parser.parse_args()
    environment = dict(os.environ)
    if args.offline:
        environment[_OFFLINE_ENV] = "1"
    if args.install_system:
        environment[_SYSTEM_ENV] = "1"
    node_modules = prepare_runtime_dependencies(Path(args.bundle), environment)
    print("Repository Quality Guard runtime dependencies: PASS")
    if node_modules is not None:
        print(f"RQG_NODE_MODULES={node_modules}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
