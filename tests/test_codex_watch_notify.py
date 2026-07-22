import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from unittest.mock import patch

import agent_watch_notify.watcher as watcher_module
from agent_watch_notify.__main__ import _parse_session_dirs, main
from agent_watch_notify.events import Notification, guess_agent_name, parse_event
from agent_watch_notify.notifier import (
    DEFAULT_MESSAGES,
    customize,
    load_messages,
    publish,
)
from agent_watch_notify.watcher import (
    PendingEntry,
    ProcessContext,
    SeenKeys,
    flush_pending,
    process_line,
    watch,
)
from agent_watch_notify._offset import last_complete_offset


class EventsTest(unittest.TestCase):
    def test_seen_keys_keeps_constant_time_index_in_sync(self):
        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json", limit=2)

            seen.add("first")
            seen.add("second")
            seen.add("third")

            self.assertEqual(seen.keys, {"second", "third"})
            self.assertTrue(seen.contains("second"))
            self.assertFalse(seen.contains("first"))

    def test_discovers_windows_roaming_and_local_appdata(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as roaming, TemporaryDirectory() as local:
            roaming_sessions = Path(roaming) / "claude" / "sessions"
            local_sessions = Path(local) / "cursor" / "sessions"
            roaming_sessions.mkdir(parents=True)
            local_sessions.mkdir(parents=True)
            found = watcher_module.discover_session_dirs(Path(home), roaming, local)
            self.assertEqual(found, sorted([roaming_sessions, local_sessions]))

    def test_configured_session_dirs_do_not_disable_discovery(self):
        old = Path.home() / ".old-agent" / "sessions"
        new = Path.home() / ".new-agent" / "sessions"
        with patch("agent_watch_notify.__main__._discover_session_dirs", return_value=[new]):
            self.assertEqual(_parse_session_dirs(str(old)), sorted([old, new]))

    def test_ignored_agent_is_removed_from_auto_discovery(self):
        codex = Path.home() / ".codex" / "sessions"
        claude = Path.home() / ".claude" / "sessions"
        with patch("agent_watch_notify.__main__._discover_session_dirs", return_value=[codex, claude]):
            self.assertEqual(_parse_session_dirs("", {"codex"}), [claude])

    def test_approval_waits_for_grace_period(self):
        def request(call_id):
            return json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": call_id,
                    "input": json.dumps({"sandbox_permissions": "require_escalated"}),
                },
            })

        def output(call_id):
            return json.dumps({
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "call_id": call_id},
            })

        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json")
            pending = {}
            sent = []
            send = lambda item: sent.append(item) or True
            ctx = ProcessContext(seen=seen, send=send, pending=pending, now=0)

            self.assertFalse(process_line(request("call-cancel"), ctx))
            ctx.now = 5
            self.assertFalse(process_line(output("call-cancel"), ctx))
            self.assertEqual(pending, {})
            ctx.now = 20
            self.assertEqual(flush_pending(ctx), 0)

            ctx.now = 0
            self.assertFalse(process_line(request("call-wait"), ctx))
            ctx.now = 9
            self.assertFalse(process_line(request("call-wait"), ctx))
            ctx.now = 9.9
            self.assertEqual(flush_pending(ctx), 0)
            ctx.now = 10
            self.assertEqual(flush_pending(ctx), 1)
            self.assertEqual([item.key for item in sent], ["approval:call-wait"])
            ctx.now = 20
            self.assertEqual(flush_pending(ctx), 0)

            ctx.now = 0
            self.assertFalse(process_line(request("call-retry"), ctx))
            ctx.now = 10
            self.assertEqual(flush_pending(ProcessContext(
                seen=seen, send=lambda _item: False, pending=pending, now=10)), 0)
            self.assertIn("approval:call-retry", pending)
            ctx.now = 11
            self.assertEqual(flush_pending(ctx), 1)

            complete = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "still-immediate"},
            })
            ctx.now = 0
            self.assertTrue(process_line(complete, ctx))
            self.assertEqual(sent[-1].key, "complete:still-immediate")

    def test_approval_respects_custom_delay(self):
        def request(call_id):
            return json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": call_id,
                    "input": json.dumps({"sandbox_permissions": "require_escalated"}),
                },
            })

        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json")
            sent = []
            ctx = ProcessContext(seen=seen, send=lambda item: sent.append(item) or True,
                                approval_delay=3.0, now=0)
            self.assertFalse(process_line(request("fast"), ctx))
            ctx.now = 2.9
            self.assertEqual(flush_pending(ctx), 0)
            ctx.now = 3.0
            self.assertEqual(flush_pending(ctx), 1)
            self.assertEqual(sent[0].key, "approval:fast")

    def test_plan_mode_completion_waits_for_approval_notification(self):
        with TemporaryDirectory() as directory:
            sent = []
            ctx = ProcessContext(
                seen=SeenKeys(Path(directory) / "seen.json"),
                send=lambda item: sent.append(item) or True,
                pending={},
                now=0,
                approval_delay=10,
                agent_name="codex",
            )
            settings = json.dumps({
                "type": "turn_context",
                "payload": {"collaboration_mode": {"mode": "plan"}},
            })
            complete = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "plan-turn"},
            })

            self.assertFalse(process_line(settings, ctx))
            self.assertFalse(process_line(complete, ctx))
            self.assertEqual(list(ctx.pending), ["approval:plan-turn"])
            ctx.now = 10
            self.assertEqual(flush_pending(ctx), 1)
            self.assertEqual([item.key for item in sent], ["approval:plan-turn"])

    def test_plan_mode_zcode_turn_complete_waits_for_approval_notification(self):
        with TemporaryDirectory() as directory:
            ctx = ProcessContext(
                seen=SeenKeys(Path(directory) / "seen.json"),
                send=lambda _item: True,
                pending={},
                now=0,
                agent_name="zcode",
            )
            process_line(json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "thread_settings_applied",
                    "thread_settings": {"collaboration_mode": {"mode": "plan"}},
                },
            }), ctx)

            self.assertFalse(process_line(json.dumps({
                "type": "turn_complete",
                "turnId": "zcode-plan-turn",
            }), ctx))
            self.assertEqual(list(ctx.pending), ["approval:zcode-plan-turn"])

    def test_messages_reload_and_invalid_fields_fall_back(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "messages.json"
            path.write_text(json.dumps({
                "complete_title": "第一次完成",
                "complete_body": "第一次正文",
                "approval_title": "需要确认",
                "approval_body": "请处理",
            }))
            self.assertEqual(load_messages(path)["complete_title"], "第一次完成")

            path.write_text(json.dumps({
                "complete_title": "第二次完成",
                "complete_body": "",
                "approval_title": 7,
                "approval_body": "新的审核正文",
            }))
            result = load_messages(path)
            self.assertEqual(result["complete_title"], "第二次完成")
            self.assertEqual(result["approval_body"], "新的审核正文")
            self.assertEqual(result["complete_body"], DEFAULT_MESSAGES["complete_body"])

            path.write_text("not-json")
            self.assertEqual(load_messages(path), DEFAULT_MESSAGES)

    def test_load_messages_per_agent_file(self):
        with TemporaryDirectory() as directory:
            base = Path(directory) / "messages.json"
            codex = Path(directory) / "messages.codex.json"
            zcode = Path(directory) / "messages.zcode.json"
            base.write_text(json.dumps({"complete_title": "默认完成"}))
            codex.write_text(json.dumps({"complete_title": "Codex 完成"}))
            zcode.write_text(json.dumps({"complete_title": "ZCode 完成"}))

            self.assertEqual(load_messages(base, "codex")["complete_title"], "Codex 完成")
            self.assertEqual(load_messages(base, "zcode")["complete_title"], "ZCode 完成")
            self.assertEqual(load_messages(base, "unknown")["complete_title"], "默认完成")
            self.assertEqual(load_messages(base)["complete_title"], "默认完成")

    def test_load_messages_per_agent_falls_back_to_base(self):
        with TemporaryDirectory() as directory:
            base = Path(directory) / "messages.json"
            base.write_text(json.dumps({"complete_title": "基础完成"}))
            self.assertEqual(load_messages(base, "noagent")["complete_title"], "基础完成")

    def test_load_messages_preserves_empty_title_separator(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "messages.json"
            path.write_text(json.dumps({"title_separator": ""}))
            self.assertEqual(load_messages(path)["title_separator"], "")

    def test_process_line_applies_current_messages_before_send(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            messages = root / "messages.json"
            messages.write_text(json.dumps({
                "complete_title": "自定义完成",
                "complete_body": "自定义正文",
            }))
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "custom"},
            })
            sent = []
            ctx = ProcessContext(
                seen=SeenKeys(root / "seen.json"),
                send=lambda item: sent.append(item) or True,
                messages_path=messages,
            )
            self.assertTrue(process_line(line, ctx))
            self.assertEqual(sent[0].title, "自定义完成")
            self.assertEqual(sent[0].body, "自定义正文")

    def test_process_line_with_agent_name_prepends_to_title(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            messages = root / "messages.json"
            messages.write_text(json.dumps({
                "complete_title": "已完成",
                "complete_body": "任务结束",
            }))
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "agent-test"},
            })
            sent = []
            ctx = ProcessContext(
                seen=SeenKeys(root / "seen.json"),
                send=lambda item: sent.append(item) or True,
                messages_path=messages,
                agent_name="codex",
            )
            self.assertTrue(process_line(line, ctx))
            self.assertEqual(sent[0].title, "codex · 已完成")

    def test_process_line_with_agent_in_title_not_doubled(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            messages = root / "messages.json"
            messages.write_text(json.dumps({
                "complete_title": "Codex · 已完成",
                "complete_body": "结束",
            }))
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "nodup"},
            })
            sent = []
            ctx = ProcessContext(
                seen=SeenKeys(root / "seen.json"),
                send=lambda item: sent.append(item) or True,
                messages_path=messages,
                agent_name="codex",
            )
            self.assertTrue(process_line(line, ctx))
            self.assertEqual(sent[0].title, "Codex · 已完成")

    def test_task_complete_is_safe(self):
        record = {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-1",
                "last_agent_message": "secret source code",
            },
        }
        result = parse_event(record)
        self.assertEqual(result.key, "complete:turn-1")
        self.assertEqual(result.body, "Agent 任务已结束")

    def test_parse_event_carries_agent_name(self):
        record = {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "x"},
        }
        result = parse_event(record, agent_name="zcode")
        self.assertEqual(result.agent_name, "zcode")

    def test_escalated_tool_call_requests_review_without_leaking_command(self):
        record = {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "call-7",
                "input": json.dumps({
                    "sandbox_permissions": "require_escalated",
                    "cmd": "print-super-secret",
                }),
            },
        }
        result = parse_event(record)
        self.assertEqual(result.key, "approval:call-7")

    def test_wrapped_desktop_escalation_requests_review(self):
        record = {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "call-desktop",
                "name": "exec",
                "input": 'const r = await tools.exec_command({cmd:"/usr/bin/true",'
                         '"sandbox_permissions":"require_escalated",'
                         '"justification":"safe check"}); text(r.output);',
            },
        }
        result = parse_event(record)
        self.assertEqual(result.key, "approval:call-desktop")

    def test_current_desktop_escalation_format_requests_review(self):
        record = {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "call-current",
                "name": "exec",
                "input": '''const r = await tools.exec_command({
  cmd: "/usr/bin/true",
  sandbox_permissions: "require_escalated",
  justification: "safe check"
}); text(r.output);''',
            },
        }
        result = parse_event(record)
        self.assertEqual(result.key, "approval:call-current")

    def test_other_records_are_ignored(self):
        self.assertIsNone(parse_event({"type": "event_msg", "payload": {"type": "agent_message"}}))

    def test_seen_keys_persist(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            seen = SeenKeys(path, limit=3)
            self.assertTrue(seen.add("one"))
            self.assertFalse(seen.add("one"))
            self.assertFalse(SeenKeys(path, limit=3).add("one"))

    def test_seen_keys_ignores_valid_non_list_json(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            path.write_text('{"not": "a list"}')
            self.assertTrue(SeenKeys(path).add("one"))

    def test_seen_keys_write_failure_preserves_existing_file_and_logs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            path.write_text('["one"]')
            seen = SeenKeys(path)
            with patch("agent_watch_notify.watcher.Path.write_text", side_effect=OSError("full")), \
                    patch("agent_watch_notify.watcher.logging.warning") as warning:
                self.assertTrue(seen.add("two"))
            self.assertIn("two", seen.values)
            self.assertEqual(path.read_text(), '["one"]')
            warning.assert_called_once()

    def test_seen_keys_replace_failure_preserves_existing_file_and_logs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            path.write_text('["one"]')
            seen = SeenKeys(path)
            with patch("agent_watch_notify.watcher.os.replace", side_effect=OSError("read-only")), \
                    patch("agent_watch_notify.watcher.logging.warning") as warning:
                self.assertTrue(seen.add("two"))
            self.assertIn("two", seen.values)
            self.assertEqual(path.read_text(), '["one"]')
            warning.assert_called_once()

    def test_seen_keys_sets_file_permissions(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            seen = SeenKeys(path)
            seen.add("first")
            import stat
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_publish_fails_closed(self):
        def offline(*_args, **_kwargs):
            raise URLError("offline")
        self.assertFalse(publish(Notification("k", "t", "b"), "https://example.invalid/topic", None, offline))

    def test_publish_rejects_malformed_url(self):
        self.assertFalse(publish(Notification("k", "t", "b"), "http://[", None))

    def test_publish_rejects_invalid_scheme(self):
        self.assertFalse(publish(Notification("k", "t", "b"), "ftp://example.invalid/topic", None))

    def test_publish_logs_non_success_status(self):
        class Response:
            status = 503
            def __enter__(self): return self
            def __exit__(self, *_args): return False
        with self.assertLogs(level="WARNING") as logs:
            self.assertFalse(publish(
                Notification("k", "t", "b"), "https://example.invalid/topic", None,
                lambda *_a, **_k: Response()))
        self.assertIn("503", "\n".join(logs.output))

    def test_publish_sends_token_in_authorization_header(self):
        captured = {}
        def capture_opener(request, timeout=5):
            captured["headers"] = dict(request.headers)
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *_a): return False
            return Response()
        self.assertTrue(publish(
            Notification("k", "标题", "正文"), "https://ntfy.example/topic",
            "tk_secret_token", capture_opener))
        self.assertEqual(captured["headers"]["Authorization"], "Bearer tk_secret_token")
        self.assertEqual(captured["headers"]["Title"],
                         "标题".encode("utf-8").decode("latin-1"))

    def test_publish_omits_authorization_when_no_token(self):
        captured = {}
        def capture_opener(request, timeout=5):
            captured["headers"] = dict(request.headers)
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *_a): return False
            return Response()
        publish(Notification("k", "t", "b"), "https://ntfy.example/topic", None, capture_opener)
        self.assertNotIn("Authorization", captured["headers"])

    def test_publish_sends_priority_and_tags(self):
        captured = {}
        def capture_opener(request, timeout=5):
            captured["headers"] = dict(request.headers)
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *_a): return False
            return Response()
        messages = {
            "complete_title": "T", "complete_body": "B",
            "complete_priority": "high", "complete_tags": "rocket",
        }
        publish(Notification("complete:x", "T", "B"), "https://ntfy.example/topic",
                None, capture_opener, messages=messages)
        self.assertEqual(captured["headers"]["X-priority"], "high")
        self.assertEqual(captured["headers"]["Tags"], "rocket")

    def test_publish_approval_uses_approval_priority(self):
        captured = {}
        def capture_opener(request, timeout=5):
            captured["headers"] = dict(request.headers)
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *_a): return False
            return Response()
        messages = {
            "approval_title": "A", "approval_body": "B",
            "approval_priority": "urgent", "approval_tags": "warning",
        }
        publish(Notification("approval:y", "A", "B"), "https://ntfy.example/topic",
                None, capture_opener, messages=messages)
        self.assertEqual(captured["headers"]["X-priority"], "urgent")
        self.assertEqual(captured["headers"]["Tags"], "warning")

    def test_publish_skips_invalid_priority(self):
        captured = {}
        def capture_opener(request, timeout=5):
            captured["headers"] = dict(request.headers)
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *_a): return False
            return Response()
        messages = {"complete_priority": "not-a-level", "complete_tags": ""}
        publish(Notification("complete:x", "T", "B"), "https://ntfy.example/topic",
                None, capture_opener, messages=messages)
        self.assertNotIn("X-priority", captured["headers"])
        self.assertNotIn("Tags", captured["headers"])

    def test_publish_without_messages_param_omits_priority_and_tags(self):
        captured = {}
        def capture_opener(request, timeout=5):
            captured["headers"] = dict(request.headers)
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *_a): return False
            return Response()
        publish(Notification("complete:x", "T", "B"), "https://ntfy.example/topic",
                None, capture_opener)
        self.assertNotIn("X-priority", captured["headers"])
        self.assertNotIn("Tags", captured["headers"])

    def test_customize_prepends_agent_name(self):
        messages = {"complete_title": "已完成", "complete_body": "B",
                    "approval_title": "审核", "approval_body": "D"}
        result = customize(Notification("complete:x", "", "", agent_name="ZCode"), messages)
        self.assertEqual(result.title, "ZCode · 已完成")

    def test_customize_uses_custom_title_separator(self):
        for separator, expected in (
            ("·", "Codex · 已完成"),
            ("-", "Codex - 已完成"),
            ("：", "Codex ： 已完成"),
            ("", "Codex 已完成"),
        ):
            with self.subTest(separator=separator):
                messages = {
                    "display_name": "Codex",
                    "title_separator": separator,
                    "complete_title": "已完成",
                    "complete_body": "B",
                }
                result = customize(Notification("complete:x", "", ""), messages)
                self.assertEqual(result.title, expected)

    def test_customize_does_not_double_agent_name(self):
        messages = {"complete_title": "Codex · 已完成", "complete_body": "B",
                    "approval_title": "审核", "approval_body": "D"}
        result = customize(Notification("complete:x", "", "", agent_name="codex"), messages)
        self.assertEqual(result.title, "Codex · 已完成")

    def test_customize_no_agent_name(self):
        messages = {"complete_title": "已完成", "complete_body": "B",
                    "approval_title": "审核", "approval_body": "D"}
        result = customize(Notification("complete:x", "", ""), messages)
        self.assertEqual(result.title, "已完成")

    def test_guess_agent_name(self):
        self.assertEqual(guess_agent_name(Path.home() / ".codex" / "sessions"), "codex")
        self.assertEqual(guess_agent_name(Path.home() / ".zcode" / "sessions"), "zcode")
        self.assertEqual(guess_agent_name(Path.home() / ".zcode" / "cli" / "agents"), "zcode")
        self.assertEqual(guess_agent_name(Path.home() / ".claude" / "cli" / "agents"), "claude")
        self.assertEqual(guess_agent_name(Path.home() / ".kimi-code" / "sessions"), "kimi-code")
        self.assertIsNone(guess_agent_name("not-a-path"))

    def test_zcode_turn_complete(self):
        record = {
            "id": "abc-123",
            "turnId": "turn-xyz",
            "type": "turn_complete",
            "payload": {"response": "done"},
        }
        result = parse_event(record, agent_name="zcode")
        self.assertIsNotNone(result)
        self.assertEqual(result.key, "complete:turn-xyz")
        self.assertEqual(result.agent_name, "zcode")

    def test_zcode_turn_complete_fallback_key(self):
        record = {
            "id": "abc-123",
            "type": "turn_complete",
            "payload": {"response": "done"},
        }
        result = parse_event(record)
        self.assertEqual(result.key, "complete:abc-123")

    def test_process_line_sends_once(self):
        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json")
            sent = []
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "turn-2"},
            })
            ctx = ProcessContext(seen=seen, send=lambda item: sent.append(item) or True)
            self.assertTrue(process_line(line, ctx))
            self.assertFalse(process_line(line, ctx))
        self.assertEqual(len(sent), 1)

    def test_guardian_auto_review_suppresses_approval_notification(self):
        with TemporaryDirectory() as directory:
            sent = []
            review = watcher_module.AutoReviewState()
            ctx = ProcessContext(
                seen=SeenKeys(Path(directory) / "seen.json"),
                send=lambda item: sent.append(item) or True,
                pending={},
                review=review,
                now=0,
                approval_delay=10,
            )
            request = json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "auto-reviewed",
                    "input": json.dumps({"sandbox_permissions": "require_escalated"}),
                },
            })
            self.assertFalse(process_line(request, ctx))
            watcher_module.process_guardian_line(json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": ">>> APPROVAL REQUEST START"}],
                },
            }), ctx)

            ctx.now = 11
            self.assertEqual(flush_pending(ctx), 0)
            watcher_module.process_guardian_line(json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": json.dumps({"outcome": "allow"}),
                },
            }), ctx)
            self.assertEqual(ctx.pending, {})
            self.assertEqual(sent, [])

    def test_guardian_auto_review_does_not_suppress_other_approval(self):
        with TemporaryDirectory() as directory:
            sent = []
            ctx = ProcessContext(
                seen=SeenKeys(Path(directory) / "seen.json"),
                send=lambda item: sent.append(item) or True,
                pending={
                    "approval:auto-reviewed": PendingEntry(
                        0, Notification("approval:auto-reviewed", "A", "B")
                    ),
                    "approval:manual": PendingEntry(
                        0, Notification("approval:manual", "A", "B")
                    ),
                },
                review=watcher_module.AutoReviewState(
                    active=True, key="approval:auto-reviewed"
                ),
                now=10,
                approval_delay=10,
            )

            self.assertEqual(flush_pending(ctx), 1)
            self.assertEqual([item.key for item in sent], ["approval:manual"])
            self.assertEqual(list(ctx.pending), ["approval:auto-reviewed"])

    def test_guardian_session_is_control_stream(self):
        meta = json.dumps({
            "type": "session_meta",
            "payload": {
                "thread_source": "subagent",
                "source": {"subagent": {"other": "guardian"}},
                "parent_thread_id": "parent-1",
            },
        }).encode()
        self.assertEqual(watcher_module._session_type(meta), "guardian")

    def test_process_line_ignores_invalid_json(self):
        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json")
            ctx = ProcessContext(seen=seen, send=lambda _item: True)
            self.assertFalse(process_line("not-json", ctx))

    def test_process_line_ignores_json_that_is_not_an_object(self):
        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json")
            ctx = ProcessContext(seen=seen, send=lambda _item: True)
            for line in ("null", "[]", '"text"', "1"):
                with self.subTest(line=line):
                    self.assertFalse(process_line(line, ctx))

    def test_process_line_only_deduplicates_after_successful_send(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            seen = SeenKeys(path)
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "retry"},
            })
            attempts = []
            ctx_fail = ProcessContext(seen=seen, send=lambda item: attempts.append(item) or False)
            self.assertFalse(process_line(line, ctx_fail))
            self.assertFalse(path.exists())
            ctx_ok = ProcessContext(seen=seen, send=lambda item: attempts.append(item) or True)
            self.assertTrue(process_line(line, ctx_ok))
            self.assertFalse(process_line(line, ctx_ok))
            self.assertFalse(process_line(line, ProcessContext(
                seen=SeenKeys(path), send=lambda item: attempts.append(item) or True)))
            self.assertEqual(len(attempts), 2)

    def test_process_line_contains_unexpected_send_errors(self):
        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json")
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "crash"},
            })
            ctx_crash = ProcessContext(seen=seen, send=lambda _item: 1 / 0)
            with patch("agent_watch_notify.watcher.logging.warning") as warning:
                self.assertFalse(process_line(line, ctx_crash))
            ctx_ok = ProcessContext(seen=seen, send=lambda _item: True)
            self.assertTrue(process_line(line, ctx_ok))
            warning.assert_called_once()

    def test_load_messages_file_not_found_returns_defaults(self):
        result = load_messages(Path("/nonexistent/path/messages.json"))
        self.assertEqual(result, DEFAULT_MESSAGES)

    def test_watch_starts_existing_files_at_eof(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            old_line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "old"},
            })
            new_line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "new"},
            })
            path.write_text(old_line + "\n")
            calls = 0
            class Sessions:
                def rglob(self, _pattern):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        with path.open("a") as handle:
                            handle.write(new_line + "\n")
                    return [path]
            sent = []
            with patch("agent_watch_notify.watcher.time.sleep", side_effect=StopIteration):
                with self.assertRaises(StopIteration):
                    watch([Sessions()], SeenKeys(Path(directory) / "seen.json"),
                          lambda item: sent.append(item) or True, interval=0)
            self.assertEqual([item.key for item in sent], ["complete:new"])

    def test_watch_monitors_multiple_directories(self):
        with TemporaryDirectory() as dir_a, TemporaryDirectory() as dir_b:
            path_a = Path(dir_a) / "a.jsonl"
            path_b = Path(dir_b) / "b.jsonl"
            def event(turn_id):
                return json.dumps({
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": turn_id},
                }) + "\n"
            calls = 0
            class DirA:
                def rglob(self, _pattern):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        path_a.write_text(event("from-a"))
                    return [path_a] if path_a.exists() else []
            class DirB:
                def rglob(self, _pattern):
                    if calls == 2:
                        path_b.write_text(event("from-b"))
                    return [path_b] if path_b.exists() else []
            sent = []
            with patch("agent_watch_notify.watcher.time.sleep", side_effect=StopIteration):
                with self.assertRaises(StopIteration):
                    watch([DirA(), DirB()], SeenKeys(Path(dir_a) / "seen.json"),
                          lambda item: sent.append(item) or True, interval=0)
            keys = [item.key for item in sent]
            self.assertIn("complete:from-a", keys)
            self.assertIn("complete:from-b", keys)

    def test_watch_ignores_subagent_task_complete(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "subagent.jsonl"
            records = [
                {"type": "session_meta", "payload": {
                    "thread_source": "subagent",
                    "parent_thread_id": "parent-1",
                }},
                {"type": "event_msg", "payload": {
                    "type": "task_complete",
                    "turn_id": "child-1",
                }},
            ]
            calls = 0

            class Sessions:
                def rglob(self, _pattern):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        path.write_text("".join(json.dumps(record) + "\n" for record in records))
                    return [path] if path.exists() else []

            sent = []
            with patch("agent_watch_notify.watcher.time.sleep", side_effect=StopIteration):
                with self.assertRaises(StopIteration):
                    watch([Sessions()], SeenKeys(Path(directory) / "seen.json"),
                          lambda item: sent.append(item) or True, interval=0)
            self.assertEqual(sent, [])

    def test_watch_adds_session_directory_discovered_while_running(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initial = root / ".initial" / "sessions"
            late = root / ".late" / "sessions"
            initial.mkdir(parents=True)
            late.mkdir(parents=True)
            transcript = late / "session.jsonl"
            calls = 0

            def discover():
                nonlocal calls
                calls += 1
                if calls == 2:
                    transcript.write_text("")
                elif calls == 3:
                    transcript.write_text(json.dumps({"type": "event_msg", "payload": {
                        "type": "task_complete", "turn_id": "late-turn"}}) + "\n")
                return [initial] if calls == 1 else [initial, late]

            sent = []
            with patch("agent_watch_notify.watcher.time.sleep", side_effect=[None, None, StopIteration]):
                with self.assertRaises(StopIteration):
                    watch([initial], SeenKeys(root / "seen.json"),
                          lambda item: sent.append(item) or True,
                          interval=0, discover=discover)
            self.assertEqual([item.key for item in sent], ["complete:late-turn"])

    def test_main_test_mode_returns_publish_status(self):
        with TemporaryDirectory() as directory:
            for published, expected in ((True, 0), (False, 1)):
                with self.subTest(published=published), \
                        patch.dict(os.environ, {"AGENT_WATCH_NTFY_URL": "https://example.invalid/topic"}, clear=True), \
                        patch("sys.argv", ["agent-watch-notify", "--test"]), \
                        patch("agent_watch_notify.__main__.Path.home", return_value=Path(directory)), \
                        patch("agent_watch_notify.__main__.publish", return_value=published) as publisher:
                    self.assertEqual(main(), expected)
                    publisher.assert_called_once()

    def test_main_config_mode_uses_packaged_server(self):
        from agent_watch_notify import config_server

        with patch("sys.argv", ["agent-watch-notify", "--config"]), \
                patch.object(config_server, "run_server") as run_server:
            self.assertEqual(main(), 0)
        run_server.assert_called_once()

    def test_main_install_opens_config_server(self):
        from agent_watch_notify import config_server, installer

        with TemporaryDirectory() as directory, \
                patch("sys.argv", ["agent-watch-notify", "--install"]), \
                patch("builtins.input", side_effect=["topic", "token"]), \
                patch("agent_watch_notify.__main__.Path.home", return_value=Path(directory)), \
                patch.object(installer, "do_install") as do_install, \
                patch.object(config_server, "run_server") as run_server:
            self.assertEqual(main(), 0)
        do_install.assert_called_once_with("topic", "token")
        run_server.assert_called_once_with(Path(directory) / ".config" / "agent-watch-notify")

    def test_main_backward_compat_env_vars(self):
        with TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"CODEX_WATCH_NTFY_URL": "https://example.invalid/topic"}, clear=True), \
                    patch("sys.argv", ["agent-watch-notify", "--test"]), \
                    patch("agent_watch_notify.__main__.Path.home", return_value=Path(directory)), \
                    patch("agent_watch_notify.__main__.publish", return_value=True) as publisher:
                self.assertEqual(main(), 0)
                publisher.assert_called_once()


class LastCompleteOffsetTest(unittest.TestCase):
    def test_empty_file_returns_zero(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "empty.jsonl"
            path.write_bytes(b"")
            with path.open("rb") as handle:
                self.assertEqual(last_complete_offset(handle), 0)

    def test_single_complete_line(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "one.jsonl"
            path.write_bytes(b"line1\n")
            with path.open("rb") as handle:
                self.assertEqual(last_complete_offset(handle), 6)

    def test_multiple_lines_returns_last_newline_plus_one(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "multi.jsonl"
            path.write_bytes(b"line1\nline2\nline3\n")
            with path.open("rb") as handle:
                self.assertEqual(last_complete_offset(handle), 18)

    def test_no_trailing_newline_skips_incomplete_line(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "notrailing.jsonl"
            path.write_bytes(b"line1\nline2")
            with path.open("rb") as handle:
                self.assertEqual(last_complete_offset(handle), 6)

    def test_only_newline(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "newline.jsonl"
            path.write_bytes(b"\n")
            with path.open("rb") as handle:
                self.assertEqual(last_complete_offset(handle), 1)

    def test_long_file_spanning_multiple_chunks(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "long.jsonl"
            content = b"x" * 5000 + b"\nlast\n"
            path.write_bytes(content)
            with path.open("rb") as handle:
                self.assertEqual(last_complete_offset(handle), len(content))

    def test_file_with_only_incomplete_line(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "incomplete.jsonl"
            path.write_bytes(b"no-newline-here")
            with path.open("rb") as handle:
                self.assertEqual(last_complete_offset(handle), 0)


if __name__ == "__main__":
    unittest.main()
