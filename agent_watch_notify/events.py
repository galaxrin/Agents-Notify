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


def parse_event(record: dict, agent_name: str | None = None) -> Notification | None:
    payload = record.get("payload")

    # Codex: event_msg / task_complete
    if record.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "task_complete":
        turn_id = payload.get("turn_id")
        if isinstance(turn_id, str):
            return Notification(f"complete:{turn_id}", "任务已完成", "Agent 任务已结束",
                                agent_name=agent_name)

    # ZCode: turn_complete (top-level type, in agent transcript files)
    if record.get("type") == "turn_complete":
        turn_id = record.get("turnId") or (payload.get("turnId") if isinstance(payload, dict) else None)
        key = f"complete:{turn_id}" if turn_id else f"complete:{record.get('id', 'unknown')}"
        return Notification(key, "任务已完成", "Agent 任务已结束",
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
    # Walk up to find the agent name
    parts = session_dir.parts
    for part in reversed(parts):
        cleaned = part.lstrip(".")
        if cleaned in ("codex", "zcode"):
            return cleaned.lower()
    parent = session_dir.parent.name
    if parent.startswith("."):
        parent = parent[1:]
    if parent:
        return parent.lower()
    return None
