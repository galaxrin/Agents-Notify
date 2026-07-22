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
from agent_watch_notify.events import (
    Notification,
    collaboration_mode,
    guess_agent_name,
    parse_event,
)
from agent_watch_notify.notifier import customize, load_messages

_SEEN_PERMISSIONS = 0o600


def discover_session_dirs(home: Path | None = None, appdata: str | None = None,
                          local_appdata: str | None = None) -> list[Path]:
    home = home or Path.home()
    roots = [(home, ".*")]
    for value in (appdata or os.environ.get("APPDATA"),
                  local_appdata or os.environ.get("LOCALAPPDATA")):
        if value and Path(value) != home:
            roots.append((Path(value), "*"))
    found = set()
    for root, prefix in roots:
        for suffix in ("sessions", "cli/agents", "cli/rollout"):
            found.update(path for path in root.glob(prefix + "/" + suffix) if path.is_dir())
    return sorted(found)


@dataclass
class PendingEntry:
    started: float
    notification: Notification


@dataclass
class AutoReviewState:
    active: bool = False
    key: str | None = None


@dataclass
class ProcessContext:
    seen: SeenKeys
    send: Callable[[Notification], bool]
    messages_path: Path | None = None
    pending: dict[str, PendingEntry] = field(default_factory=dict)
    now: float = 0.0
    approval_delay: float = 10.0
    agent_name: str | None = None
    collaboration_mode: str | None = None
    review: AutoReviewState = field(default_factory=AutoReviewState)


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
        self.keys = set(self.values)

    def contains(self, key: str) -> bool:
        return key in self.keys

    def add(self, key: str) -> bool:
        if self.contains(key):
            return False
        evicted = self.values[0] if self.limit and len(self.values) == self.limit else None
        self.values.append(key)
        self.keys.add(key)
        if evicted is not None and evicted not in self.values:
            self.keys.discard(evicted)
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
    mode = collaboration_mode(record)
    if mode is not None:
        ctx.collaboration_mode = mode
    if record.get("type") == "response_item":
        payload = record.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "custom_tool_call_output":
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                ctx.pending.pop(f"approval:{call_id}", None)
            return False
    notification = parse_event(record, agent_name=ctx.agent_name,
                               mode=ctx.collaboration_mode)
    if notification is None or ctx.seen.contains(notification.key):
        return False
    if notification.key.startswith("approval:"):
        ctx.pending.setdefault(notification.key, PendingEntry(ctx.now, notification))
        if ctx.review.active and ctx.review.key is None:
            ctx.review.key = notification.key
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
        if ctx.review.active and ctx.review.key == key:
            continue
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


def _session_type(line: bytes) -> str:
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "user"
    if not isinstance(record, dict) or record.get("type") != "session_meta":
        return "user"
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return "user"
    source = payload.get("source")
    if (isinstance(source, dict)
            and source.get("subagent") == {"other": "guardian"}):
        return "guardian"
    if (payload.get("thread_source") == "subagent"
            or isinstance(payload.get("parent_thread_id"), str)):
        return "subagent"
    return "user"


def process_guardian_line(line: str, ctx: ProcessContext) -> None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return
    if record.get("type") == "response_item" and payload.get("type") == "message":
        content = payload.get("content")
        if (payload.get("role") == "user" and isinstance(content, list)
                and any(isinstance(item, dict)
                        and ">>> APPROVAL REQUEST START" in item.get("text", "")
                        for item in content)):
            ctx.review.active = True
            ctx.review.key = next(reversed(ctx.pending), None)
    elif record.get("type") == "event_msg" and payload.get("type") == "task_complete":
        if ctx.review.active:
            if ctx.review.key is not None:
                ctx.pending.pop(ctx.review.key, None)
            ctx.review.active = False
            ctx.review.key = None


def _read_file_state(path: Path) -> tuple[int, int, int, int, bytes, str, str | None] | None:
    try:
        with path.open("rb") as handle:
            stat = os.fstat(handle.fileno())
            session_type = _session_type(handle.readline())
            offset = last_complete_offset(handle)
            handle.seek(0)
            return (stat.st_dev, stat.st_ino, offset,
                    stat.st_size, handle.read(256), session_type, None)
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
            session_type = "user" if start == 0 else previous[5]
            ctx.collaboration_mode = None if start == 0 else previous[6]
            handle.seek(start)
            offset = start
            while line := handle.readline():
                if not line.endswith(b"\n"):
                    break
                if offset == 0:
                    session_type = _session_type(line)
                if session_type == "guardian":
                    process_guardian_line(line.decode("utf-8", errors="replace"), ctx)
                elif session_type == "user":
                    ctx.now = time.monotonic()
                    process_line(line.decode("utf-8", errors="replace"), ctx)
                offset = handle.tell()
            size = os.fstat(handle.fileno()).st_size
            states[path] = (stat.st_dev, stat.st_ino,
                            0 if offset > size else offset, size, prefix, session_type,
                            ctx.collaboration_mode)
    except Exception as error:
        logging.warning("session read failed for %s: %s", path, error)


def watch(sessions_dirs: list[Path], seen: SeenKeys,
          send: Callable[[Notification], bool],
          interval: float = 1.0, messages_path: Path | None = None,
          approval_delay: float = 10.0,
          discover: Callable[[], list[Path]] | None = None) -> None:
    states = {}
    # Map each directory to its agent name for per-agent message loading
    dir_agents: dict[Path, str | None] = {}
    for sessions_dir in sessions_dirs:
        dir_agents[sessions_dir] = guess_agent_name(sessions_dir)
    # Initial scan: register existing files with per-agent context
    for sessions_dir in sessions_dirs:
        for path in _scan_paths(sessions_dir):
            state = _read_file_state(path)
            if state is not None:
                states[path] = state
    # Shared pending dict so approval entries survive across loop iterations
    shared_pending: dict[str, PendingEntry] = {}
    review = AutoReviewState()
    flush_ctx = ProcessContext(seen=seen, send=send, messages_path=messages_path,
                              approval_delay=approval_delay, pending=shared_pending,
                              review=review)
    while True:
        if discover is not None:
            current_dirs = set(discover())
            for sessions_dir in current_dirs - set(dir_agents):
                dir_agents[sessions_dir] = guess_agent_name(sessions_dir)
                for path in _scan_paths(sessions_dir):
                    state = _read_file_state(path)
                    if state is not None:
                        states[path] = state
            for sessions_dir in set(dir_agents) - current_dirs:
                dir_agents.pop(sessions_dir, None)
            sessions_dirs[:] = sorted(dir_agents)
        for sessions_dir in sessions_dirs:
            agent = dir_agents.get(sessions_dir)
            ctx = ProcessContext(seen=seen, send=send, messages_path=messages_path,
                                approval_delay=approval_delay, agent_name=agent,
                                pending=shared_pending, review=review)
            for path in _scan_paths(sessions_dir):
                _process_file(path, states, ctx)
        flush_ctx.now = time.monotonic()
        flush_pending(flush_ctx)
        time.sleep(interval)
