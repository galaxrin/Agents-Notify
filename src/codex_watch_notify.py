#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Notification:
    key: str
    title: str
    body: str


DEFAULT_MESSAGES = {
    "complete_title": "Codex · 已完成",
    "complete_body": "Codex 任务已结束",
    "approval_title": "Codex · 等待审核",
    "approval_body": "请回到 Codex 处理",
}


def load_messages(path: Path) -> dict[str, str]:
    messages = DEFAULT_MESSAGES.copy()
    try:
        configured = json.loads(path.read_text())
    except FileNotFoundError:
        return messages
    except (json.JSONDecodeError, OSError) as error:
        logging.warning("message configuration failed: %s", error)
        return messages
    if not isinstance(configured, dict):
        logging.warning("message configuration must be a JSON object")
        return messages
    for key in messages:
        value = configured.get(key)
        if isinstance(value, str) and value.strip():
            messages[key] = value
    return messages


def customize(notification: Notification, messages: dict[str, str]) -> Notification:
    prefix = "approval" if notification.key.startswith("approval:") else "complete"
    return Notification(
        notification.key,
        messages[f"{prefix}_title"],
        messages[f"{prefix}_body"],
    )


def parse_event(record: dict) -> Notification | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    if record.get("type") == "event_msg" and payload.get("type") == "task_complete":
        turn_id = payload.get("turn_id")
        if isinstance(turn_id, str):
            return Notification(f"complete:{turn_id}", "Codex · 已完成", "Codex 任务已结束")
    if record.get("type") == "response_item" and payload.get("type") == "custom_tool_call":
        raw_input = payload.get("input")
        call_id = payload.get("call_id")
        if isinstance(raw_input, str) and isinstance(call_id, str):
            try:
                tool_input = json.loads(raw_input)
            except json.JSONDecodeError:
                tool_input = None
            direct_escalation = (isinstance(tool_input, dict)
                                 and tool_input.get("sandbox_permissions") == "require_escalated")
            desktop_escalation = ('tools.exec_command({' in raw_input
                                  and '"sandbox_permissions":"require_escalated"' in raw_input)
            if direct_escalation or desktop_escalation:
                return Notification(f"approval:{call_id}", "Codex · 等待审核", "请回到 Codex 处理")
    return None


class SeenKeys:
    def __init__(self, path: Path, limit: int = 500):
        self.path = path
        self.limit = limit
        try:
            values = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            values = []
        if not isinstance(values, list):
            values = []
        self.values = deque((str(value) for value in values), maxlen=limit)

    def add(self, key: str) -> bool:
        if key in self.values:
            return False
        self.values.append(key)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(list(self.values)))
            os.replace(temporary, self.path)
        except OSError as error:
            logging.warning("seen-key persistence failed: %s", error)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return True


def publish(notification: Notification, topic_url: str, token: str | None,
            opener: Callable = urlopen) -> bool:
    headers = {"Title": notification.title.encode("utf-8").decode("latin-1")}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        if urlsplit(topic_url).scheme.lower() != "https":
            raise ValueError("ntfy topic URL must use HTTPS")
        request = Request(topic_url, data=notification.body.encode(), headers=headers, method="POST")
        with opener(request, timeout=5) as response:
            if 200 <= response.status < 300:
                return True
            logging.warning("ntfy publish failed: HTTP status %s", response.status)
            return False
    except (URLError, OSError, ValueError) as error:
        logging.warning("ntfy publish failed: %s", error)
        return False


def process_line(line: str, seen: SeenKeys, send: Callable[[Notification], bool],
                 messages_path: Path | None = None) -> bool:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(record, dict):
        return False
    notification = parse_event(record)
    if notification is None or notification.key in seen.values:
        return False
    if messages_path is not None:
        notification = customize(notification, load_messages(messages_path))
    try:
        sent = send(notification)
    except Exception as error:
        logging.warning("notification send failed: %s", error)
        return False
    if not sent:
        return False
    seen.add(notification.key)
    return True


def _last_complete_offset(handle) -> int:
    handle.seek(0, os.SEEK_END)
    position = handle.tell()
    while position:
        start = max(0, position - 4096)
        handle.seek(start)
        newline = handle.read(position - start).rfind(b"\n")
        if newline >= 0:
            return start + newline + 1
        position = start
    return 0


def watch(sessions_dir: Path, seen: SeenKeys, send: Callable[[Notification], bool],
          interval: float = 1.0, messages_path: Path | None = None) -> None:
    def paths():
        try:
            return list(sessions_dir.rglob("*.jsonl"))
        except Exception as error:
            logging.warning("session scan failed: %s", error)
            return []

    states = {}
    for path in paths():
        try:
            with path.open("rb") as handle:
                stat = os.fstat(handle.fileno())
                offset = _last_complete_offset(handle)
                handle.seek(0)
                # ponytail: the 256-byte checkpoint misses later rewrites; use a full-file checksum if those matter.
                states[path] = (stat.st_dev, stat.st_ino, offset,
                                stat.st_size, handle.read(256))
        except Exception as error:
            logging.warning("session stat failed for %s: %s", path, error)
    while True:
        for path in paths():
            try:
                with path.open("rb") as handle:
                    stat = os.fstat(handle.fileno())
                    prefix = handle.read(256)
                    previous = states.get(path)
                    replaced = previous is not None and (stat.st_dev, stat.st_ino) != previous[:2]
                    truncated = previous is not None and stat.st_size < previous[3]
                    rewritten = previous is not None and prefix[:len(previous[4])] != previous[4]
                    start = 0 if previous is None or replaced or truncated or rewritten else previous[2]
                    handle.seek(start)
                    offset = start
                    while line := handle.readline():
                        if not line.endswith(b"\n"):
                            break
                        process_line(line.decode("utf-8", errors="replace"), seen, send,
                                     messages_path)
                        offset = handle.tell()
                    size = os.fstat(handle.fileno()).st_size
                    states[path] = (stat.st_dev, stat.st_ino,
                                    0 if offset > size else offset, size, prefix)
            except Exception as error:
                logging.warning("session read failed for %s: %s", path, error)
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    topic_url = os.environ.get("CODEX_WATCH_NTFY_URL")
    if not topic_url:
        parser.error("CODEX_WATCH_NTFY_URL is required")
    token = os.environ.get("CODEX_WATCH_NTFY_TOKEN")
    messages_path = Path.home() / ".config/codex-watch-notify/messages.json"
    if args.test:
        notification = customize(
            Notification("manual", "Codex · 已完成", "Codex 任务已结束"),
            load_messages(messages_path),
        )
        return 0 if publish(notification, topic_url, token) else 1
    sessions = Path(os.environ.get("CODEX_SESSIONS_DIR", Path.home() / ".codex/sessions"))
    seen = SeenKeys(Path.home() / ".local/state/codex-watch-notify/seen.json")
    watch(sessions, seen, lambda item: publish(item, topic_url, token),
          messages_path=messages_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
