#!/usr/bin/env python3
"""Local web configuration page for agent-watch-notify."""
from __future__ import annotations

import json
import os
import re
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

from agent_watch_notify._config import read_env_file, write_env_file
from agent_watch_notify.notifier import DEFAULT_MESSAGES

_ENV_PATHS = {
    "url": "AGENT_WATCH_NTFY_URL",
    "token": "AGENT_WATCH_NTFY_TOKEN",
    "sessions": "AGENT_WATCH_SESSIONS_DIR",
    "delay": "AGENT_WATCH_APPROVAL_DELAY",
    "interval": "AGENT_WATCH_POLL_INTERVAL",
}

_DEFAULTS = {"url": "", "token": "", "sessions": "", "delay": "10", "interval": "1"}
_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class _Handler(BaseHTTPRequestHandler):
    config_dir: Path
    html_path: Path

    def log_message(self, fmt, *args):
        pass

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _detect_agent_sessions(self, agent_name):
        home = Path.home()
        candidates = [
            home / ("." + agent_name) / "sessions",
            home / ("." + agent_name) / "cli" / "agents",
            home / ("." + agent_name) / "cli" / "rollout",
            home / ("." + agent_name) / "logs",
            home / ".local" / "state" / agent_name,
            home / ".local" / "share" / agent_name / "log",
        ]
        # Windows: %APPDATA%\agent-name\sessions etc.
        appdata = os.environ.get("APPDATA")
        if appdata:
            appdata_path = Path(appdata)
            candidates += [
                appdata_path / agent_name / "sessions",
                appdata_path / agent_name / "cli" / "agents",
                appdata_path / agent_name / "cli" / "rollout",
            ]
        return [str(p) for p in candidates if p.exists()]

    def _discover_agents(self):
        agents = {}
        for p in sorted(self.config_dir.glob("messages.*.json")):
            name = p.stem.split(".", 1)[1]
            if name and _AGENT_NAME_RE.match(name):
                agents[name] = {"messages": self._read_messages_file(p), "dirs": self._detect_agent_sessions(name)}
        return agents

    def _read_messages_file(self, path):
        result = DEFAULT_MESSAGES.copy()
        try:
            configured = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return result
        if isinstance(configured, dict):
            for key in result:
                value = configured.get(key)
                if isinstance(value, str) and value.strip():
                    result[key] = value
        return result

    def _write_messages_file(self, path, messages):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        cleaned = {}
        for key in DEFAULT_MESSAGES:
            value = messages.get(key, "")
            if isinstance(value, str) and value.strip():
                cleaned[key] = value
        path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n")

    def _collect_all_sessions(self):
        all_dirs = set()
        for p in self.config_dir.glob("messages.*.json"):
            name = p.stem.split(".", 1)[1]
            if name and _AGENT_NAME_RE.match(name):
                for d in self._detect_agent_sessions(name):
                    all_dirs.add(d)
        return ",".join(sorted(all_dirs))

    def _read_env(self):
        result = _DEFAULTS.copy()
        configured = read_env_file(self.config_dir / "env")
        for short, env_key in _ENV_PATHS.items():
            if configured.get(env_key):
                result[short] = configured[env_key]
        return result

    def _write_env(self, env):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        env_path = self.config_dir / "env"
        url = env.get("url", "")
        token = env.get("token", "")
        sessions = self._collect_all_sessions()
        write_env_file(env_path, {
            "AGENT_WATCH_NTFY_URL": url,
            "AGENT_WATCH_NTFY_TOKEN": token,
            "AGENT_WATCH_SESSIONS_DIR": sessions,
            "AGENT_WATCH_APPROVAL_DELAY": env.get("delay", "10"),
            "AGENT_WATCH_POLL_INTERVAL": env.get("interval", "1"),
            "CODEX_WATCH_NTFY_URL": url,
            "CODEX_WATCH_NTFY_TOKEN": token,
        })

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            body = self.html_path.read_bytes()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/config":
            self._json_response({"agents": self._discover_agents(), "env": self._read_env()})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._json_response({"ok": False, "error": "invalid JSON"}, 400)
            return
        if self.path == "/api/config":
            env = data.get("env", {})
            agents = data.get("agents", {})
            for agent_name, agent_info in agents.items():
                if not _AGENT_NAME_RE.match(agent_name):
                    continue
                self._write_messages_file(self.config_dir / ("messages." + agent_name + ".json"), agent_info.get("messages", {}))
            self._write_env(env)
            self._json_response({"ok": True, "agents": self._discover_agents()})
            Thread(target=self.server.shutdown, daemon=True).start()
        elif self.path == "/api/test":
            env = self._read_env()
            agent_name = data.get("agent", "")
            messages = data.get("messages", {})
            if not messages:
                messages = DEFAULT_MESSAGES
            url = env.get("url", "")
            if not url:
                self._json_response({"ok": False, "error": "未配置 ntfy 地址"})
                return
            token = env.get("token", "") or None
            ntype = data.get("type", "complete")
            from agent_watch_notify.events import Notification
            from agent_watch_notify.notifier import publish, customize
            if ntype == "approval":
                key = "approval:manual"
                title = messages.get("approval_title", "等待审核")
                body = messages.get("approval_body", "请回到 Agent 处理")
            else:
                key = "manual"
                title = messages.get("complete_title", "任务已完成")
                body = messages.get("complete_body", "Agent 任务已结束")
            notification = customize(
                Notification(key, title, body, agent_name=agent_name or None),
                messages,
            )
            try:
                success = publish(notification, url, token, messages=messages)
                self._json_response({"ok": success, "error": "" if success else "发送失败"})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)})
        elif self.path == "/api/delete":
            agent_name = data.get("agent", "")
            if not agent_name or not _AGENT_NAME_RE.match(agent_name):
                self._json_response({"ok": False, "error": "invalid agent name"})
                return
            try:
                (self.config_dir / ("messages." + agent_name + ".json")).unlink(missing_ok=True)
                self._json_response({"ok": True})
            except OSError as e:
                self._json_response({"ok": False, "error": str(e)})
        elif self.path == "/api/reset":
            presets = {
                "codex": {"complete_title": "Codex · 已完成", "complete_body": "Codex 任务已结束",
                          "approval_title": "Codex · 等待审核", "approval_body": "请回到 Codex 处理"},
                "zcode": {"complete_title": "ZCode · 已完成", "complete_body": "ZCode 任务已结束",
                          "approval_title": "ZCode · 等待审核", "approval_body": "请回到 ZCode 处理"},
            }
            for p in self.config_dir.glob("messages.*.json"):
                name = p.stem.split(".", 1)[1]
                if name and _AGENT_NAME_RE.match(name):
                    self._write_messages_file(p, presets.get(name, {}))
            self._json_response({"ok": True})
        else:
            self.send_error(404)


def run_server(config_dir, host="127.0.0.1", port=9876):
    _Handler.config_dir = config_dir
    _Handler.html_path = Path(__file__).parent / "config.html"
    server = HTTPServer((host, port), _Handler)
    url = "http://" + host + ":" + str(port)
    print("配置页面: " + url)
    print("按 Ctrl+C 停止")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


if __name__ == "__main__":
    import sys
    cfg = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".config" / "agent-watch-notify"
    run_server(cfg)
