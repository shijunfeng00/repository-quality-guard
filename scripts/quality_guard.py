"""Repository Quality Guard Agent-facing 启动器。"""

from __future__ import annotations

import faulthandler
import importlib.util
import os
import sys
import signal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RELEASE_SEAL = "d3e984fbf655a56417edbf68fe0881225d34bcbe01edd5e2a419ff07ad26fee0"


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
    try:
        _prepare_dependencies()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[QG] 环境准备失败：{error}", file=sys.stderr, flush=True)
        return 2
    os.environ["REPO_QUALITY_GUARD_HOME"] = str(ROOT)
    os.environ["REPO_QUALITY_GUARD_RELEASE_SEAL"] = RELEASE_SEAL
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    from runtime.src import workflow

    return int(workflow.main(sys.argv[1:]))


if __name__ == "__main__":
    from runtime.src.process_lifecycle import install_parent_death_guard

    signal.signal(signal.SIGTERM, signal.default_int_handler)
    install_parent_death_guard()
    faulthandler.dump_traceback_later(60, repeat=True, file=sys.stderr)
    try:
        exit_code = main()
    except KeyboardInterrupt:
        print(
            "[QG] 审计被终止；已请求清理本次运行拥有的子进程。",
            file=sys.stderr,
            flush=True,
        )
        exit_code = 143
    print(
        f"[QG] command finished rc={exit_code}; runtime cleanup complete.",
        file=sys.stderr,
        flush=True,
    )
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(exit_code))
