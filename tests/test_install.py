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
    def test_windows_wrapper_supports_non_ascii_user_profile(self):
        with TemporaryDirectory() as directory, \
                patch.object(installer.sys, "platform", "win32"), \
                patch.object(installer, "_bin_dir", return_value=Path(directory)), \
                patch.object(installer, "_config_dir", return_value=Path("C:/Users/张三/.config/agent-watch-notify")), \
                patch.object(installer, "_state_dir", return_value=Path("C:/Users/张三/.local/state/agent-watch-notify")):
            installer._write_wrapper()
            content = (Path(directory) / "agent-watch-notify.cmd").read_text()
            self.assertIn("%USERPROFILE%", content)
            content.encode("ascii")

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
                patch.object(installer, "_find_scripts_dir", return_value=None), \
                patch.object(installer, "discover_session_dirs", return_value=[
                    Path.home() / ".claude" / "sessions",
                    Path.home() / ".kimi-code" / "sessions",
                ]):
            installer._install_message_files()
            claude = json.loads(
                (Path(directory) / "messages.claude.json").read_text()
            )
            kimi = json.loads(
                (Path(directory) / "messages.kimi-code.json").read_text()
            )
            self.assertEqual(claude["display_name"], "Claude")
            self.assertEqual(kimi["display_name"], "Kimi-code")
            self.assertFalse((Path(directory) / "messages.codex.json").exists())

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
    def test_create_server_does_not_open_browser(self):
        from agent_watch_notify import config_server

        with TemporaryDirectory() as directory, \
                patch.object(config_server, "HTTPServer") as http_server, \
                patch.object(config_server.webbrowser, "open") as browser:
            http_server.return_value.server_port = 4321
            server, url = config_server.create_server(Path(directory), port=0)
            self.assertEqual(url, "http://127.0.0.1:4321")
            self.assertIs(server, http_server.return_value)
            browser.assert_not_called()

    def test_message_files_are_read_as_utf8(self):
        handler = object.__new__(_Handler)
        with patch.object(Path, "read_text",
                          return_value='{"complete_body":"宝宝任务已结束"}') as read:
            messages = handler._read_messages_file(Path("messages.codex.json"))

        self.assertEqual(messages["complete_body"], "宝宝任务已结束")
        read.assert_called_once_with(encoding="utf-8")

    def test_write_env_replaces_invalid_numbers_with_safe_defaults(self):
        with TemporaryDirectory() as directory:
            handler = object.__new__(_Handler)
            handler.config_dir = Path(directory)

            handler._write_env({
                "url": "topic",
                "token": "",
                "delay": "nan",
                "interval": "-1",
            })

            env = (Path(directory) / "env").read_text()
            self.assertIn("AGENT_WATCH_APPROVAL_DELAY=10\n", env)
            self.assertIn("AGENT_WATCH_POLL_INTERVAL=1\n", env)

    def test_config_post_rejects_non_object_sections(self):
        handler = object.__new__(_Handler)
        handler.path = "/api/config"
        body = b'{"env":[],"agents":[]}'
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        responses = []
        handler._json_response = lambda data, status=200: responses.append((data, status))

        handler.do_POST()

        self.assertEqual(responses, [({"ok": False, "error": "invalid config"}, 400)])

    def test_post_rejects_non_object_request(self):
        handler = object.__new__(_Handler)
        handler.path = "/api/config"
        body = b'[]'
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        responses = []
        handler._json_response = lambda data, status=200: responses.append((data, status))

        handler.do_POST()

        self.assertEqual(responses, [({"ok": False, "error": "invalid request"}, 400)])

    def test_header_logo_is_served_as_png(self):
        root = Path(__file__).resolve().parents[1] / "agent_watch_notify"
        _Handler.assets_dir = root / "assets"
        handler = object.__new__(_Handler)
        handler.path = "/assets/galaxrin-agents-notify-logo.png"
        handler.wfile = io.BytesIO()
        headers = {}
        handler.send_response = lambda status: headers.update(status=status)
        handler.send_header = lambda key, value: headers.update({key: value})
        handler.end_headers = lambda: None
        handler.send_error = lambda status: headers.update(status=status)

        handler.do_GET()

        self.assertEqual(headers["status"], 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertTrue(handler.wfile.getvalue().startswith(b"\x89PNG"))

    def test_agent_discovery_scans_session_directories_once(self):
        with TemporaryDirectory() as directory:
            config = Path(directory)
            (config / "messages.codex.json").write_text("{}")
            (config / "messages.zcode.json").write_text("{}")
            _Handler.config_dir = config
            handler = object.__new__(_Handler)
            paths = [Path.home() / ".codex" / "sessions",
                     Path.home() / ".zcode" / "cli" / "agents"]
            with patch.object(handler, "_all_session_dirs", return_value=paths) as scan:
                agents = handler._discover_agents()
            self.assertEqual(scan.call_count, 1)
            self.assertEqual(agents["codex"]["dirs"], [str(paths[0])])
            self.assertEqual(agents["zcode"]["dirs"], [str(paths[1])])

    def test_web_keeps_session_setting_empty_for_future_discovery(self):
        with TemporaryDirectory() as directory, TemporaryDirectory() as home:
            (Path(home) / ".codex" / "sessions").mkdir(parents=True)
            _Handler.config_dir = Path(directory)
            handler = object.__new__(_Handler)
            with patch("agent_watch_notify.config_server.Path.home", return_value=Path(home)):
                handler._write_env({"url": "topic", "token": "", "delay": "10", "interval": "1"})
            env = (Path(directory) / "env").read_text()
            self.assertIn("AGENT_WATCH_SESSIONS_DIR=\n", env)

    def test_deleted_discovered_agent_stays_hidden(self):
        with TemporaryDirectory() as directory, TemporaryDirectory() as home:
            (Path(home) / ".codex" / "sessions").mkdir(parents=True)
            _Handler.config_dir = Path(directory)
            handler = object.__new__(_Handler)
            handler.path = "/api/delete"
            body = b'{"agent":"codex"}'
            handler.headers = {"Content-Length": str(len(body))}
            handler.rfile = io.BytesIO(body)
            handler._json_response = lambda *_args, **_kwargs: None
            with patch("agent_watch_notify.config_server.Path.home", return_value=Path(home)):
                handler.do_POST()
                self.assertNotIn("codex", handler._discover_agents())

    def test_missing_topic_is_generated_once_and_persisted(self):
        with TemporaryDirectory() as directory:
            _Handler.config_dir = Path(directory)
            handler = object.__new__(_Handler)
            first = handler._read_env()["url"]
            second = handler._read_env()["url"]
            self.assertRegex(first, r"^AgentsNotify-[0-9a-f]{32}$")
            self.assertEqual(second, first)

    def test_config_page_auto_saves_and_keeps_reset_in_agent_panel(self):
        html = (Path(__file__).resolve().parents[1] / "agent_watch_notify" / "config.html").read_text()
        self.assertIn('class="app-shell"', html)
        self.assertNotIn('id="btnSave"', html)
        self.assertIn("scheduleSave", html)
        self.assertIn('data-reset="', html)
        self.assertIn('display_name:"Agent 名称"', html)
        self.assertIn('title_separator:"标题分隔符"', html)
        self.assertIn("function makePreset(name)", html)
        self.assertNotIn("var presets=", html)
        self.assertNotIn('"codex":{', html)
        self.assertNotIn('"zcode":{', html)

    def test_config_page_preserves_edits_when_adding_and_serializes_delete(self):
        html = (Path(__file__).resolve().parents[1] / "agent_watch_notify" / "config.html").read_text()
        add = html.index('agents[name]={messages:msgs,dirs:[]}')
        self.assertGreater(html.rfind("syncAgentsFromPanels()", 0, add), 0)
        self.assertIn(
            'saveRequest.then(function(){return api("POST","/api/delete"',
            html,
        )

    def test_config_page_serializes_saves_and_reports_network_failures(self):
        html = (Path(__file__).resolve().parents[1] / "agent_watch_notify" / "config.html").read_text()
        self.assertIn('saveRequest=saveRequest.then(function(){return api("POST","/api/config"', html)
        self.assertIn('setSaveState("更新失败",true)', html)
        self.assertIn('.catch(function(e){toast("请求失败: "+e.message,false)})', html)
        self.assertIn('saveRequest.then(function(){return api("POST","/api/test"', html)

    def test_empty_title_separator_is_persisted(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "messages.codex.json"
            handler = object.__new__(_Handler)
            handler.config_dir = Path(directory)
            handler._write_messages_file(path, {"title_separator": ""})
            self.assertEqual(json.loads(path.read_text())["title_separator"], "")

    def test_discovers_agents_from_session_directories(self):
        with TemporaryDirectory() as directory, TemporaryDirectory() as home:
            (Path(home) / ".claude" / "sessions").mkdir(parents=True)
            (Path(home) / ".kimi-code" / "sessions").mkdir(parents=True)
            _Handler.config_dir = Path(directory)
            handler = object.__new__(_Handler)
            with patch("agent_watch_notify.config_server.Path.home", return_value=Path(home)):
                agents = handler._discover_agents()
            self.assertEqual(set(agents), {"claude", "kimi-code"})
            self.assertEqual(agents["claude"]["dirs"], [str(Path(home) / ".claude" / "sessions")])

    def test_server_keeps_running_after_config_is_saved(self):
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
            self.assertFalse(stopped.wait(0.05))


if __name__ == "__main__":
    unittest.main()
