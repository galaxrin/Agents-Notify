#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from agent_watch_notify._config import read_env_file
from agent_watch_notify.events import Notification, guess_agent_name
from agent_watch_notify.notifier import DEFAULT_MESSAGES, customize, load_messages, publish
from agent_watch_notify.watcher import SeenKeys, discover_session_dirs as _discover_session_dirs, watch

_CONFIG_DIR_NAME = "agent-watch-notify"
_STATE_DIR_NAME = "agent-watch-notify"


def _env(key: str, fallback_key: str | None = None, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value is not None:
        return value
    if fallback_key:
        value = os.environ.get(fallback_key)
        if value is not None:
            return value
    return default


def _parse_session_dirs(raw: str | None, ignored: set[str] | None = None) -> list[Path]:
    found = set(_discover_session_dirs())
    if raw:
        for part in raw.split(","):
            stripped = part.strip()
            if stripped:
                found.add(Path(stripped).expanduser())
    return [path for path in sorted(found) if guess_agent_name(path) not in (ignored or set())]


def _send_factory(config_dir: Path, messages_path: Path | None):
    env_path = config_dir / "env"

    def send(notification: Notification) -> bool:
        # Re-read env file on each send for real-time updates
        env = read_env_file(env_path)
        topic_url = env.get("AGENT_WATCH_NTFY_URL") or env.get("CODEX_WATCH_NTFY_URL", "")
        token = env.get("AGENT_WATCH_NTFY_TOKEN") or env.get("CODEX_WATCH_NTFY_TOKEN") or None
        ignored = {name.strip() for name in env.get("AGENT_WATCH_IGNORED_AGENTS", "").split(",") if name.strip()}
        if notification.agent_name in ignored:
            return True
        messages = load_messages(messages_path) if messages_path else None
        if not topic_url:
            return False
        return publish(notification, topic_url, token, messages=messages)

    return send


def run_listener(config_dir: Path, listener_lock=None) -> bool:
    messages_path = config_dir / "messages.json"

    def current_session_dirs():
        current = read_env_file(config_dir / "env")
        sessions_raw = current.get("AGENT_WATCH_SESSIONS_DIR") or current.get("CODEX_SESSIONS_DIR") or ""
        ignored = {name.strip() for name in current.get("AGENT_WATCH_IGNORED_AGENTS", "").split(",") if name.strip()}
        return _parse_session_dirs(sessions_raw, ignored)

    sessions_dirs = current_session_dirs()
    state_dir = Path.home() / ".local" / "state" / _STATE_DIR_NAME
    seen = SeenKeys(state_dir / "seen.json")

    def current_settings():
        current = read_env_file(config_dir / "env")
        try:
            approval = float(current.get("AGENT_WATCH_APPROVAL_DELAY")
                             or current.get("CODEX_WATCH_APPROVAL_DELAY") or "10")
        except ValueError:
            approval = 10.0
        try:
            interval = float(current.get("AGENT_WATCH_POLL_INTERVAL")
                             or current.get("CODEX_WATCH_POLL_INTERVAL") or "1")
        except ValueError:
            interval = 1.0
        return approval, max(interval, 0.5)

    approval_delay, poll_interval = current_settings()
    if listener_lock is None:
        from agent_watch_notify.instance_lock import WatchLock
        listener_lock = WatchLock(state_dir / "watch.lock")
        if not listener_lock.acquire():
            print("监听服务已在运行")
            return False
    send = _send_factory(config_dir, messages_path)
    try:
        watch(sessions_dirs, seen, send, interval=poll_interval,
              messages_path=messages_path, approval_delay=approval_delay,
              discover=current_session_dirs, settings=current_settings)
    finally:
        listener_lock.release()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent task notification for ntfy")
    parser.add_argument("--install", action="store_true", help="install daemon and register auto-start")
    parser.add_argument("--uninstall", action="store_true", help="uninstall daemon and remove config")
    parser.add_argument("--test", action="store_true", help="send a test notification")
    parser.add_argument("--config", action="store_true", help="open web configuration page")
    args = parser.parse_args()
    config_dir = Path.home() / ".config" / _CONFIG_DIR_NAME
    messages_path = config_dir / "messages.json"

    if args.install:
        from agent_watch_notify.installer import do_install
        env_path = config_dir / "env"
        if env_path.exists():
            answer = input(f"配置已存在 ({env_path})，是否覆盖？[y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("已取消")
                return 0
        url = input("ntfy 主题地址 (回车生成随机主题): ").strip()
        if not url:
            import secrets
            url = f"AgentsNotify-{secrets.token_hex(16)}"
            print(f"已生成主题: {url}")
        token = input("ntfy 令牌 (无认证可回车跳过): ").strip()
        do_install(url, token)
        from agent_watch_notify.config_server import run_server
        run_server(config_dir)
        return 0

    if args.uninstall:
        from agent_watch_notify.installer import do_uninstall
        do_uninstall()
        return 0

    if args.config:
        from agent_watch_notify.config_server import run_server
        run_server(config_dir)
        return 0
    # Read env file as fallback so --test works without manual env setup
    env_file = read_env_file(config_dir / "env")
    topic_url = (_env("AGENT_WATCH_NTFY_URL", "CODEX_WATCH_NTFY_URL")
                 or env_file.get("AGENT_WATCH_NTFY_URL")
                 or env_file.get("CODEX_WATCH_NTFY_URL", ""))
    token = (_env("AGENT_WATCH_NTFY_TOKEN", "CODEX_WATCH_NTFY_TOKEN")
             or env_file.get("AGENT_WATCH_NTFY_TOKEN")
             or env_file.get("CODEX_WATCH_NTFY_TOKEN") or None)
    if not topic_url:
        parser.error("AGENT_WATCH_NTFY_URL is required: set env var or run --install first")
    if args.test:
        messages = load_messages(messages_path)
        notification = customize(
            Notification("manual", DEFAULT_MESSAGES["complete_title"],
                         DEFAULT_MESSAGES["complete_body"]),
            messages,
        )
        return 0 if publish(notification, topic_url, token, messages=messages) else 1
    run_listener(config_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
