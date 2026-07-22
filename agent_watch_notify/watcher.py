from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent_watch_notify._offset import last_complete_offset
from agent_watch_notify.events import Notification, parse_event, guess_agent_name
from agent_watch_notify.notifier import customize, load_messages

_SEEN_PERMISSIONS = 0o600


@dataclass
class PendingEntry:
    started: float
    notification: Notification


@dataclass
class ProcessContext:
    seen: SeenKeys
    send: Callable[[Notification], bool]
    messages_path: Path | None = None
    pending: dict[str, PendingEntry] = field(default_factory=dict)
    now: float = 0.0
    approval_delay: float = 10.0
    agent_name: str | None = None


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
            os.chmod(temporary, _SEEN_PERMISSIONS)
            os.replace(temporary, self.path)
        except OSError as error:
            logging.warning("seen-key persistence failed: %s", error)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return True


def process_line(line: str, ctx: ProcessContext) -> bool:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(record, dict):
        return False
    if record.get("type") == "response_item":
        payload = record.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "custom_tool_call_output":
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                ctx.pending.pop(f"approval:{call_id}", None)
            return False
    notification = parse_event(record, agent_name=ctx.agent_name)
    if notification is None or notification.key in ctx.seen.values:
        return False
    if notification.key.startswith("approval:"):
        ctx.pending.setdefault(notification.key, PendingEntry(ctx.now, notification))
        return False
    if ctx.messages_path is not None:
        notification = customize(notification,
                                 load_messages(ctx.messages_path, ctx.agent_name))
    try:
        sent = ctx.send(notification)
    except Exception as error:
        logging.warning("notification send failed: %s", error)
        return False
    if not sent:
        return False
    ctx.seen.add(notification.key)
    return True


def flush_pending(ctx: ProcessContext) -> int:
    sent_count = 0
    for key, entry in list(ctx.pending.items()):
        if ctx.now - entry.started < ctx.approval_delay:
            continue
        item = entry.notification
        if ctx.messages_path is not None:
            agent = item.agent_name or ctx.agent_name
            item = customize(item, load_messages(ctx.messages_path, agent))
        try:
            sent = ctx.send(item)
        except Exception as error:
            logging.warning("notification send failed: %s", error)
            continue
        if sent:
            ctx.seen.add(key)
            ctx.pending.pop(key, None)
            sent_count += 1
    return sent_count


def _scan_paths(sessions_dir: Path) -> list[Path]:
    try:
        return list(sessions_dir.rglob("*.jsonl"))
    except Exception as error:
        logging.warning("session scan failed: %s", error)
        return []


def _is_subagent_meta(line: bytes) -> bool:
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(record, dict) or record.get("type") != "session_meta":
        return False
    payload = record.get("payload")
    return (isinstance(payload, dict)
            and (payload.get("thread_source") == "subagent"
                 or isinstance(payload.get("parent_thread_id"), str)))


def _read_file_state(path: Path) -> tuple[int, int, int, int, bytes, bool] | None:
    try:
        with path.open("rb") as handle:
            stat = os.fstat(handle.fileno())
            is_subagent = _is_subagent_meta(handle.readline())
            offset = last_complete_offset(handle)
            handle.seek(0)
            return (stat.st_dev, stat.st_ino, offset,
                    stat.st_size, handle.read(256), is_subagent)
    except Exception as error:
        logging.warning("session stat failed for %s: %s", path, error)
        return None


def _process_file(path: Path, states: dict, ctx: ProcessContext) -> None:
    try:
        with path.open("rb") as handle:
            stat = os.fstat(handle.fileno())
            prefix = handle.read(256)
            previous = states.get(path)
            replaced = previous is not None and (stat.st_dev, stat.st_ino) != previous[:2]
            truncated = previous is not None and stat.st_size < previous[3]
            rewritten = previous is not None and prefix[:len(previous[4])] != previous[4]
            start = 0 if previous is None or replaced or truncated or rewritten else previous[2]
            is_subagent = False if start == 0 else previous[5]
            handle.seek(start)
            offset = start
            while line := handle.readline():
                if not line.endswith(b"\n"):
                    break
                if offset == 0:
                    is_subagent = _is_subagent_meta(line)
                if not is_subagent:
                    ctx.now = time.monotonic()
                    process_line(line.decode("utf-8", errors="replace"), ctx)
                offset = handle.tell()
            size = os.fstat(handle.fileno()).st_size
            states[path] = (stat.st_dev, stat.st_ino,
                            0 if offset > size else offset, size, prefix, is_subagent)
    except Exception as error:
        logging.warning("session read failed for %s: %s", path, error)


def watch(sessions_dirs: list[Path], seen: SeenKeys,
          send: Callable[[Notification], bool],
          interval: float = 1.0, messages_path: Path | None = None,
          approval_delay: float = 10.0) -> None:
    states = {}
    # Map each directory to its agent name for per-agent message loading
    dir_agents: dict[Path, str | None] = {}
    for sessions_dir in sessions_dirs:
        dir_agents[sessions_dir] = guess_agent_name(sessions_dir)
    # Initial scan: register existing files with per-agent context
    for sessions_dir in sessions_dirs:
        agent = dir_agents.get(sessions_dir)
        for path in _scan_paths(sessions_dir):
            state = _read_file_state(path)
            if state is not None:
                states[path] = state
    # Shared pending dict so approval entries survive across loop iterations
    shared_pending: dict[str, PendingEntry] = {}
    flush_ctx = ProcessContext(seen=seen, send=send, messages_path=messages_path,
                              approval_delay=approval_delay, pending=shared_pending)
    while True:
        for sessions_dir in sessions_dirs:
            agent = dir_agents.get(sessions_dir)
            ctx = ProcessContext(seen=seen, send=send, messages_path=messages_path,
                                approval_delay=approval_delay, agent_name=agent,
                                pending=shared_pending)
            for path in _scan_paths(sessions_dir):
                _process_file(path, states, ctx)
        flush_ctx.now = time.monotonic()
        flush_pending(flush_ctx)
        time.sleep(interval)
