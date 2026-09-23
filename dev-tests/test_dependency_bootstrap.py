from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import install_dependencies as dependencies


class TestDependencyBootstrap(unittest.TestCase):
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
                self.assertEqual({}, environment)
                commands.append((command, timeout))
                return True

            with (
                patch.object(dependencies, "_python_ready", side_effect=[False, True]),
                patch.object(dependencies, "_run", side_effect=fake_run),
            ):
                dependencies._install_python(
                    bundle,
                    {"python": {"apted": "1.0.3"}},
                    {},
                )

            self.assertEqual(1, len(commands))
            command, timeout = commands[0]
            self.assertIn("--no-index", command)
            self.assertIn("--find-links", command)
            self.assertIsNone(timeout)

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
                self.assertEqual({}, environment)
                commands.append((command, timeout))
                return True

            with (
                patch.object(dependencies, "_python_ready", side_effect=[False, True]),
                patch.object(dependencies, "_run", side_effect=fake_run),
            ):
                dependencies._install_python(
                    bundle,
                    {"python": {"apted": "1.0.3"}},
                    {},
                )

            self.assertEqual(1, len(commands))
            command, timeout = commands[0]
            self.assertNotIn("--no-index", command)
            self.assertEqual(dependencies._NETWORK_INSTALL_TIMEOUT_SECONDS, timeout)
            self.assertIn("--retries", command)
            self.assertIn("--timeout", command)

    def test_node_bundle_uses_local_payload_before_npm(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            archive = bundle / "offline" / "node_modules.zip"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"archive")
            node_modules = bundle / "cache" / "node_modules"

            with (
                patch.object(
                    dependencies.shutil, "which", return_value="/usr/bin/tool"
                ),
                patch.object(dependencies, "_existing_node_modules", return_value=None),
                patch.object(
                    dependencies, "_node_cache", return_value=node_modules.parent
                ),
                patch.object(dependencies, "_node_probe", side_effect=[False, True]),
                patch.object(
                    dependencies, "_copy_offline_node_modules", return_value=True
                ) as copy_payload,
                patch.object(dependencies, "_run") as run,
            ):
                result = dependencies._install_node(
                    bundle,
                    {"node": {"typescript": "6.0.2"}},
                    {},
                )

            self.assertEqual(node_modules, result)
            copy_payload.assert_called_once_with(bundle, node_modules)
            run.assert_not_called()

    def test_network_command_timeout_fails_closed_without_hanging(self) -> None:
        command = ["pip", "install", "example"]
        with patch.object(
            dependencies.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(command, 1),
        ) as run:
            ok = dependencies._run(command, environment={}, timeout=1)

        self.assertFalse(ok)
        run.assert_called_once_with(command, check=False, env={}, timeout=1)


if __name__ == "__main__":
    unittest.main()
