from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


_ESCALATION_RE = re.compile(
    r'''(?:["']sandbox_permissions["']|sandbox_permissions)\s*:\s*["']require_escalated["']'''
)


@dataclass(frozen=True)
class Notification:
    key: str
    title: str
    body: str
    agent_name: str | None = None


def collaboration_mode(record: dict) -> str | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get("collaboration_mode")
    settings = payload.get("thread_settings")
    if isinstance(settings, dict):
        value = settings.get("collaboration_mode", value)
    if isinstance(value, dict):
        value = value.get("mode")
    return value.lower() if isinstance(value, str) else None


def parse_event(record: dict, agent_name: str | None = None,
                mode: str | None = None) -> Notification | None:
    payload = record.get("payload")

    # Codex: event_msg / task_complete
    if record.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "task_complete":
        turn_id = payload.get("turn_id")
        if isinstance(turn_id, str):
            waiting = mode == "plan"
            kind = "approval" if waiting else "complete"
            title = "等待审核" if waiting else "任务已完成"
            body = "请回到 Agent 处理" if waiting else "Agent 任务已结束"
            return Notification(f"{kind}:{turn_id}", title, body,
                                agent_name=agent_name)

    # ZCode: turn_complete (top-level type, in agent transcript files)
    if record.get("type") == "turn_complete":
        turn_id = record.get("turnId") or (payload.get("turnId") if isinstance(payload, dict) else None)
        waiting = mode == "plan"
        kind = "approval" if waiting else "complete"
        key = f"{kind}:{turn_id}" if turn_id else f"{kind}:{record.get('id', 'unknown')}"
        title = "等待审核" if waiting else "任务已完成"
        body = "请回到 Agent 处理" if waiting else "Agent 任务已结束"
        return Notification(key, title, body,
                            agent_name=agent_name)

    # Codex/ZCode: custom_tool_call requiring escalation
    if record.get("type") == "response_item" and isinstance(payload, dict) and payload.get("type") == "custom_tool_call":
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
                                  and _ESCALATION_RE.search(raw_input) is not None)
            if direct_escalation or desktop_escalation:
                return Notification(f"approval:{call_id}", "等待审核", "请回到 Agent 处理",
                                    agent_name=agent_name)
    return None


def guess_agent_name(session_dir) -> str | None:
    """Derive agent name from session directory path.

    ~/.codex/sessions → 'codex'
    ~/.zcode/sessions → 'zcode'
    ~/.zcode/cli/agents → 'zcode'
    ~/.zcode/cli/rollout → 'zcode'
    Non-Path objects  → None
    """
    if not isinstance(session_dir, Path):
        return None
    for part in reversed(session_dir.parts):
        if part.startswith(".") and len(part) > 1:
            return part[1:].lower()
    if session_dir.name in ("agents", "rollout") and session_dir.parent.name == "cli":
        return session_dir.parent.parent.name.lower()
    if session_dir.parent.name:
        return session_dir.parent.name.lower()
    return None
