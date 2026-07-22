#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from glob import glob
from pathlib import Path

from agent_watch_notify._config import read_env_file
from agent_watch_notify.events import Notification
from agent_watch_notify.notifier import DEFAULT_MESSAGES, customize, load_messages, publish
from agent_watch_notify.watcher import SeenKeys, watch

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


_HARDCODED_SESSION_DIRS = [
    Path.home() / ".codex/sessions",
    Path.home() / ".zcode/sessions",
    Path.home() / ".zcode/cli/agents",
    Path.home() / ".zcode/cli/rollout",
]

# Glob patterns for auto-discovering agent session directories.
# Unix/macOS: ~/.*/sessions, ~/.*/cli/agents, ~/.*/cli/rollout
# Windows:    %APPDATA%/*\sessions, etc.
_DISCOVERY_PATTERNS = [
    str(Path.home() / ".*" / "sessions"),
    str(Path.home() / ".*" / "cli" / "agents"),
    str(Path.home() / ".*" / "cli" / "rollout"),
]
_appdata = os.environ.get("APPDATA")
if _appdata:
    _DISCOVERY_PATTERNS += [
        str(Path(_appdata) / "*" / "sessions"),
        str(Path(_appdata) / "*" / "cli" / "agents"),
        str(Path(_appdata) / "*" / "cli" / "rollout"),
    ]


def _discover_session_dirs() -> list[Path]:
    """Auto-discover agent session directories by scanning common patterns."""
    found: set[Path] = set()
    for pattern in _DISCOVERY_PATTERNS:
        for match in glob(pattern):
            path = Path(match)
            if path.is_dir():
                found.add(path)
    for path in _HARDCODED_SESSION_DIRS:
        if path.is_dir():
            found.add(path)
    return sorted(found)


def _parse_session_dirs(raw: str | None) -> list[Path]:
    if raw:
        dirs = []
        for part in raw.split(","):
            stripped = part.strip()
            if stripped:
                dirs.append(Path(stripped).expanduser())
        if dirs:
            return dirs
    return _discover_session_dirs() or [_HARDCODED_SESSION_DIRS[0]]


def _send_factory(config_dir: Path, messages_path: Path | None):
    env_path = config_dir / "env"

    def send(notification: Notification) -> bool:
        # Re-read env file on each send for real-time updates
        env = read_env_file(env_path)
        topic_url = env.get("AGENT_WATCH_NTFY_URL") or env.get("CODEX_WATCH_NTFY_URL", "")
        token = env.get("AGENT_WATCH_NTFY_TOKEN") or env.get("CODEX_WATCH_NTFY_TOKEN") or None
        messages = load_messages(messages_path) if messages_path else None
        if not topic_url:
            return False
        return publish(notification, topic_url, token, messages=messages)

    return send


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
        notification = customize(
            Notification("manual", DEFAULT_MESSAGES["complete_title"],
                         DEFAULT_MESSAGES["complete_body"]),
            load_messages(messages_path),
        )
        return 0 if publish(notification, topic_url, token,
                            messages=load_messages(messages_path)) else 1
    sessions_raw = env_file.get("AGENT_WATCH_SESSIONS_DIR") or env_file.get("CODEX_SESSIONS_DIR") or ""
    sessions_dirs = _parse_session_dirs(sessions_raw)
    seen = SeenKeys(Path.home() / ".local" / "state" / _STATE_DIR_NAME / "seen.json")
    try:
        approval_delay = float(env_file.get("AGENT_WATCH_APPROVAL_DELAY") or env_file.get("CODEX_WATCH_APPROVAL_DELAY") or "10")
    except ValueError:
        approval_delay = 10.0
    try:
        poll_interval = float(env_file.get("AGENT_WATCH_POLL_INTERVAL") or env_file.get("CODEX_WATCH_POLL_INTERVAL") or "1")
    except ValueError:
        poll_interval = 1.0
    if poll_interval < 0.5:
        poll_interval = 0.5
    send = _send_factory(config_dir, messages_path)
    watch(sessions_dirs, seen, send, interval=poll_interval,
          messages_path=messages_path, approval_delay=approval_delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
