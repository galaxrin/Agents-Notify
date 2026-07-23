import unittest
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


class DesktopTest(unittest.TestCase):
    def test_desktop_uses_bundled_ca_certificates(self):
        from agent_watch_notify import desktop

        certifi = Mock()
        certifi.where.return_value = "/app/cacert.pem"
        with patch.dict(os.environ, {"SSL_CERT_FILE": "/app/openssl.ca/no-such-file"},
                        clear=True):
            desktop.configure_tls(certifi)
            self.assertEqual(os.environ["SSL_CERT_FILE"], "/app/cacert.pem")

    def test_resource_path_uses_macos_app_resources(self):
        from agent_watch_notify import resources

        with TemporaryDirectory() as directory:
            executable = Path(directory) / "Agents Notify.app" / "Contents" / "MacOS" / "Agents Notify"
            expected = executable.parent.parent / "Resources" / "agent_watch_notify" / "config.html"
            expected.parent.mkdir(parents=True)
            expected.write_text("ok")
            with patch.object(resources, "__file__", str(Path(directory) / "missing" / "resources.py")), \
                    patch.object(resources.sys, "executable", str(executable)):
                self.assertEqual(resources.resource_path("config.html"), expected.resolve())

    def test_desktop_embeds_config_and_starts_tray(self):
        from agent_watch_notify import desktop

        class Event:
            def __init__(self):
                self.handlers = []

            def __iadd__(self, handler):
                self.handlers.append(handler)
                return self

        server = Mock()
        server.server_port = 4321
        window = Mock()
        window.events.closing = Event()
        webview = Mock()
        webview.create_window.return_value = window
        tray = Mock()
        tray.Menu.side_effect = lambda *items: items
        tray.MenuItem.side_effect = lambda title, action: (title, action)
        icon = tray.Icon.return_value
        image = Mock()

        with TemporaryDirectory() as directory, \
                patch.object(desktop, "create_server", return_value=(server, "http://127.0.0.1:4321")):
            desktop.run_desktop(webview, tray, image, Path(directory), start_listener=Mock())

        webview.create_window.assert_called_once_with(
            "Agents Notify", "http://127.0.0.1:4321", width=1000, height=760
        )
        icon.run_detached.assert_called_once()
        webview.start.assert_called_once()
        self.assertEqual(len(window.events.closing.handlers), 1)
        self.assertFalse(window.events.closing.handlers[0]())
        window.hide.assert_called_once()

        menu = tray.Icon.call_args.args[3]
        menu[0][1]()
        window.show.assert_called_once()
        menu[1][1]()
        window.destroy.assert_called_once()

    def test_main_exits_when_desktop_is_already_running(self):
        from agent_watch_notify import desktop

        fake_modules = {
            "certifi": Mock(),
            "pystray": Mock(),
            "webview": Mock(),
            "PIL": Mock(),
        }
        fake_modules["certifi"].where.return_value = "/app/cacert.pem"
        lock = Mock()
        lock.acquire.return_value = False
        with patch.dict(sys.modules, fake_modules), \
                patch("agent_watch_notify.instance_lock.WatchLock",
                      return_value=lock), \
                patch.object(desktop, "run_desktop") as run_desktop:
            self.assertEqual(desktop.main(), 0)

        run_desktop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
