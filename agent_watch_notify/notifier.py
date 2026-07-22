from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from agent_watch_notify.events import Notification

DEFAULT_MESSAGES = {
    "display_name": "",
    "title_separator": "·",
    "complete_title": "任务已完成",
    "complete_body": "Agent 任务已结束",
    "approval_title": "等待审核",
    "approval_body": "请回到 Agent 处理",
    "complete_priority": "default",
    "complete_tags": "white_check_mark",
    "approval_priority": "urgent",
    "approval_tags": "warning",
}

_VALID_PRIORITIES = {"min", "low", "default", "high", "urgent"}


def load_messages(path: Path, agent_name: str | None = None) -> dict[str, str]:
    messages = DEFAULT_MESSAGES.copy()
    # Try agent-specific file first, then fallback to base
    paths_to_try = []
    if agent_name:
        paths_to_try.append(path.with_name(f"messages.{agent_name}.json"))
    paths_to_try.append(path)
    for p in paths_to_try:
        try:
            configured = json.loads(p.read_text())
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, OSError) as error:
            logging.warning("message configuration failed: %s", error)
            continue
        if not isinstance(configured, dict):
            logging.warning("message configuration must be a JSON object: %s", p)
            continue
        for key in messages:
            value = configured.get(key)
            if key == "title_separator" and isinstance(value, str):
                messages[key] = value.strip()
            elif isinstance(value, str) and value.strip():
                messages[key] = value
        break  # first valid file wins
    return messages


def customize(notification: Notification, messages: dict[str, str]) -> Notification:
    prefix = "approval" if notification.key.startswith("approval:") else "complete"
    title = messages[f"{prefix}_title"]
    body = messages[f"{prefix}_body"]
    agent = messages.get("display_name") or notification.agent_name
    separator = messages.get("title_separator", "·").strip()
    if agent and agent.lower() not in title.lower():
        title = " ".join(part for part in (agent, separator, title) if part)
    return Notification(notification.key, title, body, agent_name=agent)


def publish(notification: Notification, topic_url: str, token: str | None,
            opener: Callable = urlopen,
            messages: dict[str, str] | None = None) -> bool:
    headers = {"Title": notification.title.encode("utf-8").decode("latin-1")}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if messages is not None:
        prefix = "approval" if notification.key.startswith("approval:") else "complete"
        priority = messages.get(f"{prefix}_priority", "default")
        if priority in _VALID_PRIORITIES:
            headers["X-Priority"] = priority
        tags = messages.get(f"{prefix}_tags", "")
        if tags.strip():
            headers["Tags"] = tags.strip()
    try:
        if not topic_url.startswith("http"):
            topic_url = "https://ntfy.sh/" + topic_url
        if urlsplit(topic_url).scheme.lower() not in ("https", "http"):
            raise ValueError("ntfy topic URL must use HTTP or HTTPS")
        request = Request(topic_url, data=notification.body.encode(), headers=headers, method="POST")
        with opener(request, timeout=5) as response:
            if 200 <= response.status < 300:
                return True
            logging.warning("ntfy publish failed: HTTP status %s", response.status)
            return False
    except (URLError, OSError, ValueError) as error:
        logging.warning("ntfy publish failed: %s", error)
        return False
