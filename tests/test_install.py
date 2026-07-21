import json
import os
import plistlib
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class PlistRenderTest(unittest.TestCase):
    def test_installer_creates_messages_once_and_preserves_custom_copy(self):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            home = temp / "home"
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            launchctl = fake_bin / "launchctl"
            launchctl.write_text("#!/bin/sh\nexit 0\n")
            launchctl.chmod(0o755)
            environment = {
                **os.environ,
                "HOME": str(home),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "CODEX_WATCH_NTFY_URL": "https://ntfy.example/topic",
            }

            subprocess.run(["sh", str(root / "scripts/install.sh")],
                           env=environment, check=True)
            messages = home / ".config/codex-watch-notify/messages.json"
            self.assertEqual(json.loads(messages.read_text())["complete_title"],
                             "Codex · 已完成")
            self.assertEqual(stat.S_IMODE(messages.stat().st_mode), 0o600)

            messages.write_text('{"complete_title":"我的文案"}')
            subprocess.run(["sh", str(root / "scripts/install.sh")],
                           env=environment, check=True)
            self.assertEqual(messages.read_text(), '{"complete_title":"我的文案"}')

    def test_special_characters_round_trip_through_argv(self):
        root = Path(__file__).resolve().parents[1]
        url = "https://ntfy.example/topic?a=1&b=two|pipe\\slash\nnext"
        token = "tk_&|\\\nsecond-line"
        program = "/tmp/bin with spaces/codex-watch-notify"
        log = "/tmp/log with spaces/stderr.log"
        with TemporaryDirectory() as directory:
            output = Path(directory) / "agent.plist"
            subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts/render_plist.py"),
                    str(root / "scripts/com.codex.watch-notify.plist.in"),
                    str(output),
                    program,
                    url,
                    token,
                    log,
                ],
                check=True,
            )
            with output.open("rb") as source:
                rendered = plistlib.load(source)

        self.assertEqual(rendered["ProgramArguments"], [program])
        self.assertEqual(rendered["EnvironmentVariables"]["CODEX_WATCH_NTFY_URL"], url)
        self.assertEqual(rendered["EnvironmentVariables"]["CODEX_WATCH_NTFY_TOKEN"], token)
        self.assertEqual(rendered["StandardErrorPath"], log)
        self.assertTrue(rendered["RunAtLoad"])
        self.assertNotIn("KeepAlive", rendered)


if __name__ == "__main__":
    unittest.main()
