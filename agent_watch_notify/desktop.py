from __future__ import annotations

import os
import threading
from pathlib import Path

from agent_watch_notify.config_server import create_server
from agent_watch_notify.resources import resource_path


def configure_tls(certifi) -> None:
    if not Path(os.environ.get("SSL_CERT_FILE", "")).is_file():
        os.environ["SSL_CERT_FILE"] = certifi.where()


def run_desktop(webview, tray, image, config_dir: Path, start_listener=None,
                tray_title="Agents Notify") -> None:
    server, url = create_server(config_dir, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if start_listener is not None:
        threading.Thread(target=start_listener, daemon=True).start()

    window = webview.create_window("Agents Notify", url, width=1000, height=760)

    def hide(*args):
        window.hide()
        return False

    def show(icon=None, item=None):
        window.show()

    def quit_app(icon=None, item=None):
        server.shutdown()
        tray_icon.stop()
        window.destroy()

    window.events.closing += hide
    menu = tray.Menu(
        tray.MenuItem("打开配置", show),
        tray.MenuItem("退出", quit_app),
    )
    logo = image.open(resource_path("assets/galaxrin-agents-notify-logo.png"))
    tray_icon = tray.Icon("agents-notify", logo, tray_title, menu)
    tray_icon.run_detached()
    try:
        webview.start()
    finally:
        server.shutdown()
        tray_icon.stop()


def main() -> int:
    import certifi
    import pystray
    import webview
    from PIL import Image

    configure_tls(certifi)
    config_dir = Path.home() / ".config" / "agent-watch-notify"
    from agent_watch_notify.instance_lock import WatchLock
    desktop_lock = WatchLock(Path.home() / ".local" / "state" / "agent-watch-notify" / "desktop.lock")
    if not desktop_lock.acquire():
        return 0
    listener_lock = WatchLock(Path.home() / ".local" / "state" / "agent-watch-notify" / "watch.lock")

    def start_listener():
        from agent_watch_notify.__main__ import run_listener
        run_listener(config_dir, listener_lock)

    try:
        if listener_lock.acquire():
            run_desktop(webview, pystray, Image, config_dir, start_listener)
        else:
            run_desktop(webview, pystray, Image, config_dir,
                        tray_title="Agents Notify（已有服务正在监听）")
    finally:
        desktop_lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
