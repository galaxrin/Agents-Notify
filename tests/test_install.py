import json
import io
import os
import stat
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_watch_notify import installer
from agent_watch_notify.config_server import _Handler


class InstallerTest(unittest.TestCase):
    def test_installer_creates_messages_once_and_preserves_custom_copy(self):
        with TemporaryDirectory() as directory, \
                patch.object(installer, "_config_dir", return_value=Path(directory)):
            installer._install_message_files()
            messages = Path(directory) / "messages.json"
            self.assertEqual(json.loads(messages.read_text())["complete_title"], "任务已完成")

            messages.write_text('{"complete_title":"我的文案"}')
            installer._install_message_files()
            self.assertEqual(messages.read_text(), '{"complete_title":"我的文案"}')

    def test_packaged_installer_creates_default_agent_messages(self):
        with TemporaryDirectory() as directory, \
                patch.object(installer, "_config_dir", return_value=Path(directory)), \
                patch.object(installer, "_find_scripts_dir", return_value=None):
            installer._install_message_files()
            self.assertTrue((Path(directory) / "messages.codex.json").exists())
            self.assertTrue((Path(directory) / "messages.zcode.json").exists())

    def test_write_env_keeps_legacy_names_and_private_permissions(self):
        with TemporaryDirectory() as directory, \
                patch.object(installer, "_config_dir", return_value=Path(directory)):
            installer._write_env("https://ntfy.example/topic", "tk_test")
            env_file = Path(directory) / "env"
            content = env_file.read_text()
            self.assertIn("AGENT_WATCH_NTFY_URL=https://ntfy.example/topic", content)
            self.assertIn("CODEX_WATCH_NTFY_URL=https://ntfy.example/topic", content)
            self.assertIn("CODEX_WATCH_NTFY_TOKEN=tk_test", content)
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)


class BootstrapTest(unittest.TestCase):
    def test_macos_bootstrap_installs_from_github_then_configures(self):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            log = temp / "python.log"
            python = temp / "python3"
            python.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$BOOTSTRAP_LOG"\n'
            )
            python.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{temp}:/usr/bin:/bin",
                "BOOTSTRAP_LOG": str(log),
            }
            subprocess.run(["sh", str(root / "scripts/bootstrap.sh")],
                           env=environment, check=True)

            calls = log.read_text().splitlines()
            self.assertIn("-m pip install --user --upgrade https://github.com/galaxrin/Agents-Notify/archive/refs/heads/main.zip", calls)
            self.assertEqual(calls[-1], "-m agent_watch_notify --install")

    def test_windows_bootstrap_uses_same_package_and_installer(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts/bootstrap.ps1").read_text()
        self.assertIn("-m pip install --user --upgrade", script)
        self.assertIn("-m agent_watch_notify --install", script)


class ConfigServerTest(unittest.TestCase):
    def test_server_stops_after_config_is_saved(self):
        with TemporaryDirectory() as directory:
            _Handler.config_dir = Path(directory)
            stopped = threading.Event()

            class Server:
                def shutdown(self):
                    stopped.set()

            body = b'{"env":{},"agents":{}}'
            handler = object.__new__(_Handler)
            handler.path = "/api/config"
            handler.headers = {"Content-Length": str(len(body))}
            handler.rfile = io.BytesIO(body)
            handler.server = Server()
            handler._json_response = lambda *_args, **_kwargs: None
            handler.do_POST()
            self.assertTrue(stopped.wait(0.2))


if __name__ == "__main__":
    unittest.main()
