"""Normalize Repository Quality Guard bootstrap configuration at one process boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BootstrapSettings:
    """Resolved bootstrap policy and subprocess environment.

    Instances are immutable handoff objects shared by bootstrap and parser callers.
    """

    environment: dict[str, str]
    cache_root: Path
    node_modules: str | None
    python_index_url: str | None
    npm_registry: str | None
    offline_only: bool
    install_system_dependencies: bool


def node_parser_environment() -> dict[str, str]:
    """Return a subprocess environment containing the resolved Node parser root.

    Returns:
        Copy of the current environment with ``RQG_NODE_MODULES`` resolved.

    Raises:
        RuntimeError: The locked Node parser dependency is unavailable.
    """
    node_modules = os.getenv("RQG_NODE_MODULES")
    if not node_modules:
        raise RuntimeError(
            "锁定 Node parser 依赖尚未安装；"
            "请运行 runtime/install_dependencies.py 或重新 deploy Skill。"
        )
    environment = dict(os.environ)
    environment["RQG_NODE_MODULES"] = node_modules
    return environment


def load_bootstrap_settings(
    force_offline: bool = False, force_system_install: bool = False
) -> BootstrapSettings:
    """Resolve optional process inputs into one typed bootstrap contract.

    Args:
        force_offline: Force use of local dependency media without network access.
        force_system_install: Allow the explicit system-dependency installation path.

    Returns:
        Immutable bootstrap settings resolved from arguments and process environment.
    """
    environment = dict(os.environ)
    explicit_cache = os.getenv("RQG_DEPENDENCY_CACHE")
    if explicit_cache:
        cache_root = Path(explicit_cache).expanduser().resolve()
    elif os.name == "nt" and os.getenv("LOCALAPPDATA"):
        cache_root = (
            Path(os.environ["LOCALAPPDATA"])
            / "repository-quality-guard"
            / "dependencies"
        )
    else:
        xdg_cache = os.getenv("XDG_CACHE_HOME")
        cache_root = (
            (Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache")
            / "repository-quality-guard"
            / "dependencies"
        )
    return BootstrapSettings(
        environment=environment,
        cache_root=cache_root,
        node_modules=os.getenv("RQG_NODE_MODULES"),
        python_index_url=os.getenv("PIP_INDEX_URL"),
        npm_registry=os.getenv("NPM_CONFIG_REGISTRY"),
        offline_only=force_offline or os.getenv("RQG_OFFLINE_ONLY") == "1",
        install_system_dependencies=(
            force_system_install or os.getenv("RQG_INSTALL_SYSTEM_DEPS") == "1"
        ),
    )
