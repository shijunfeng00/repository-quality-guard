"""Repository Quality Guard Agent-facing 启动器。"""

from __future__ import annotations

import faulthandler
import importlib.util
import os
import sys
from pathlib import Path

if __name__ == "__main__" and not sys.flags.dont_write_bytecode:
    os.execv(
        sys.executable,
        [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:]],
    )

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RELEASE_SEAL = "87f1bca95639ee9b5c86922ab89293356435d8ca734a0f3a24a6f59002108083"


def _prepare_dependencies() -> None:
    """验证锁定依赖；fresh clone 缺依赖时在线安装，离线包安装时可用本地介质。"""
    module_path = ROOT / "runtime" / "install_dependencies.py"
    spec = importlib.util.spec_from_file_location(
        "_rqg_dependency_installer", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 runtime/install_dependencies.py。")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        node_modules = module.prepare_runtime_dependencies(ROOT)
    finally:
        del sys.modules[spec.name]
    if node_modules is not None:
        os.environ["RQG_NODE_MODULES"] = str(node_modules)


def main() -> int:
    """准备依赖、验证发布完整性并执行唯一 QG workflow。

    Returns:
        QG workflow 的进程退出码。
    """
    _prepare_dependencies()
    os.environ["REPO_QUALITY_GUARD_HOME"] = str(ROOT)
    os.environ["REPO_QUALITY_GUARD_RELEASE_SEAL"] = RELEASE_SEAL
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    from runtime.src import workflow

    return int(workflow.main(sys.argv[1:]))


if __name__ == "__main__":
    faulthandler.dump_traceback_later(60, repeat=True, file=sys.stderr)
    raise SystemExit(main())
