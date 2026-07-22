import json
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_watch_notify import installer


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


if __name__ == "__main__":
    unittest.main()
