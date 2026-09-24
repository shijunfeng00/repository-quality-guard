from __future__ import annotations

import os
import sys
import tempfile
import unittest
import threading
import time
import io
import contextlib
from pathlib import Path
from unittest.mock import patch

from runtime import install_dependencies as dependencies
from runtime.src.bootstrap_settings import BootstrapSettings


def _settings(
    cache_root: Path, environment: dict[str, str] | None = None
) -> BootstrapSettings:
    """Build explicit bootstrap settings for one isolated test cache."""
    return BootstrapSettings(
        environment=dict(os.environ) if environment is None else dict(environment),
        cache_root=cache_root,
        node_modules=None,
        python_index_url=None,
        npm_registry=None,
        offline_only=False,
        install_system_dependencies=False,
    )


class TestDependencyBootstrap(unittest.TestCase):
    def test_network_probe_stops_after_three_short_failures(self) -> None:
        with patch.object(
            dependencies.urllib.request, "urlopen", side_effect=OSError("offline")
        ) as open_url:
            self.assertFalse(
                dependencies._network_available("https://pypi.org/simple/")
            )
        self.assertEqual(3, open_url.call_count)
        self.assertTrue(
            all(call.kwargs["timeout"] == 3 for call in open_url.call_args_list)
        )

    def test_missing_node_reports_required_environment(self) -> None:
        with patch.object(dependencies.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "缺少 Node.js"):
                dependencies._install_node(
                    Path("."), {"node": {}}, _settings(Path("cache"))
                )

    def test_missing_offline_and_network_fails_before_pip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(dependencies, "_python_ready", return_value=False),
                patch.object(dependencies, "_network_available", return_value=False),
                patch.object(dependencies, "_run") as run,
                self.assertRaisesRegex(RuntimeError, "PyPI 网络不可用"),
            ):
                dependencies._install_python(
                    Path(temp),
                    {
                        "python": {"apted": "1.0.3"},
                        "registry": {"python": "https://pypi.org/simple/"},
                    },
                    _settings(Path(temp) / "cache"),
                )
            run.assert_not_called()

    def test_python_bundle_uses_local_wheelhouse_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            wheelhouse = bundle / "offline" / "wheelhouse"
            wheelhouse.mkdir(parents=True)
            (wheelhouse / "placeholder.whl").write_bytes(b"wheel")
            commands: list[tuple[list[str], float | None]] = []

            def fake_run(
                command: list[str],
                *,
                environment: dict[str, str],
                timeout: float | None = None,
            ) -> bool:
                self.assertEqual(
                    str(bundle / "cache"), environment["RQG_DEPENDENCY_CACHE"]
                )
                commands.append((command, timeout))
                return True

            with (
                patch.object(dependencies, "_python_ready", side_effect=[False, True]),
                patch.object(dependencies, "_run", side_effect=fake_run),
            ):
                dependencies._install_python(
                    bundle,
                    {
                        "python": {"apted": "1.0.3"},
                        "registry": {"python": "https://pypi.org/simple/"},
                    },
                    _settings(
                        bundle / "cache",
                        {"RQG_DEPENDENCY_CACHE": str(bundle / "cache")},
                    ),
                )

            self.assertEqual(1, len(commands))
            command, timeout = commands[0]
            self.assertIn("--no-index", command)
            self.assertIn("--find-links", command)
            self.assertEqual(60, timeout)

    def test_python_network_fallback_is_bounded_when_local_media_is_absent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            commands: list[tuple[list[str], float | None]] = []

            def fake_run(
                command: list[str],
                *,
                environment: dict[str, str],
                timeout: float | None = None,
            ) -> bool:
                self.assertEqual(
                    str(bundle / "cache"), environment["RQG_DEPENDENCY_CACHE"]
                )
                commands.append((command, timeout))
                return True

            with (
                patch.object(dependencies, "_python_ready", side_effect=[False, True]),
                patch.object(dependencies, "_run", side_effect=fake_run),
                patch.object(dependencies, "_network_available", return_value=True),
            ):
                dependencies._install_python(
                    bundle,
                    {
                        "python": {"apted": "1.0.3"},
                        "registry": {"python": "https://pypi.org/simple/"},
                    },
                    _settings(
                        bundle / "cache",
                        {"RQG_DEPENDENCY_CACHE": str(bundle / "cache")},
                    ),
                )

            self.assertEqual(1, len(commands))
            command, timeout = commands[0]
            self.assertNotIn("--no-index", command)
            self.assertEqual(dependencies._NETWORK_INSTALL_TIMEOUT_SECONDS, timeout)
            self.assertIn("--retries", command)
            self.assertIn("--timeout", command)

    def test_installed_tree_reuses_managed_wheel_cache_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            portable = root / "portable"
            portable_wheels = portable / "offline" / "wheelhouse"
            portable_wheels.mkdir(parents=True)
            (portable_wheels / "apted-1.0.3-py3-none-any.whl").write_bytes(b"wheel")
            cache = root / "cache"
            dependencies._seed_wheel_cache(portable, _settings(cache))
            installed = root / "installed"
            installed.mkdir()
            commands: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> bool:
                commands.append(command)
                return True

            with (
                patch.object(dependencies, "_python_ready", side_effect=[False, True]),
                patch.object(dependencies, "_run", side_effect=fake_run),
                patch.object(dependencies, "_network_available") as network,
            ):
                dependencies._install_python(
                    installed,
                    {
                        "python": {"apted": "1.0.3"},
                        "registry": {"python": "https://pypi.org/simple/"},
                    },
                    _settings(cache),
                )

            self.assertEqual(1, len(commands))
            self.assertIn("--no-index", commands[0])
            self.assertIn(str(cache / "python" / "wheelhouse"), commands[0])
            network.assert_not_called()

    def test_node_bundle_uses_local_payload_before_npm(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            archive = bundle / "offline" / "node_modules.zip"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"archive")
            cache_root = bundle / "cache"
            node_modules = cache_root / "node" / "typescript-6.0.2" / "node_modules"

            with (
                patch.object(
                    dependencies.shutil, "which", return_value="/usr/bin/tool"
                ),
                patch.object(dependencies, "_existing_node_modules", return_value=None),
                patch.object(dependencies, "_cache_root", return_value=cache_root),
                patch.object(dependencies, "_node_probe", side_effect=[False, True]),
                patch.object(
                    dependencies, "_copy_offline_node_modules", return_value=True
                ) as copy_payload,
                patch.object(dependencies, "_run") as run,
            ):
                result = dependencies._install_node(
                    bundle,
                    {"node": {"typescript": "6.0.2"}},
                    _settings(cache_root),
                )

            self.assertEqual(node_modules, result)
            copy_payload.assert_called_once_with(bundle, node_modules)
            run.assert_not_called()

    def test_network_command_timeout_fails_closed_without_hanging(self) -> None:
        command = [sys.executable, "-c", "import time; time.sleep(30)"]
        ok = dependencies._run(command, environment=dict(os.environ), timeout=0.3)
        self.assertFalse(ok)

    def test_dependency_bootstrap_single_flight_waits_instead_of_racing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = _settings(Path(temp))
            lock = Path(temp) / "bootstrap.lock"
            lock.mkdir()
            (lock / "owner.json").write_text(
                '{"pid": %d}' % os.getpid(), encoding="utf-8"
            )
            entered = threading.Event()
            stream = io.StringIO()

            def waiter() -> None:
                with contextlib.redirect_stderr(stream):
                    with dependencies._bootstrap_single_flight(settings):
                        entered.set()

            thread = threading.Thread(target=waiter)
            thread.start()
            time.sleep(0.5)
            self.assertFalse(entered.is_set())
            for path in lock.iterdir():
                path.unlink()
            lock.rmdir()
            thread.join(timeout=3)
            self.assertTrue(entered.is_set())
            self.assertIn("正在等待", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
