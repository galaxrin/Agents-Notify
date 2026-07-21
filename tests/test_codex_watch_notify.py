import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from unittest.mock import patch

from src.codex_watch_notify import (
    DEFAULT_MESSAGES,
    Notification,
    SeenKeys,
    customize,
    load_messages,
    main,
    parse_event,
    process_line,
    publish,
    watch,
)


class EventsTest(unittest.TestCase):
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
            self.assertEqual(load_messages(path), {
                **DEFAULT_MESSAGES,
                "complete_title": "第二次完成",
                "approval_body": "新的审核正文",
            })

            path.write_text("not-json")
            self.assertEqual(load_messages(path), DEFAULT_MESSAGES)

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
            self.assertTrue(process_line(
                line,
                SeenKeys(root / "seen.json"),
                lambda item: sent.append(item) or True,
                messages,
            ))
            self.assertEqual(
                sent,
                [Notification("complete:custom", "自定义完成", "自定义正文")],
            )

    def test_task_complete_is_safe(self):
        record = {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-1",
                "last_agent_message": "secret source code",
            },
        }
        self.assertEqual(
            parse_event(record),
            Notification("complete:turn-1", "Codex · 已完成", "Codex 任务已结束"),
        )

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
        self.assertEqual(
            parse_event(record),
            Notification("approval:call-7", "Codex · 等待审核", "请回到 Codex 处理"),
        )

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

        self.assertEqual(
            parse_event(record),
            Notification("approval:call-desktop", "Codex · 等待审核", "请回到 Codex 处理"),
        )

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

            with patch("src.codex_watch_notify.Path.write_text", side_effect=OSError("full")), \
                    patch("src.codex_watch_notify.logging.warning") as warning:
                self.assertTrue(seen.add("two"))

            self.assertIn("two", seen.values)
            self.assertEqual(path.read_text(), '["one"]')
            warning.assert_called_once()

    def test_seen_keys_replace_failure_preserves_existing_file_and_logs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            path.write_text('["one"]')
            seen = SeenKeys(path)

            with patch("src.codex_watch_notify.os.replace", side_effect=OSError("read-only")), \
                    patch("src.codex_watch_notify.logging.warning") as warning:
                self.assertTrue(seen.add("two"))

            self.assertIn("two", seen.values)
            self.assertEqual(path.read_text(), '["one"]')
            warning.assert_called_once()

    def test_publish_fails_closed(self):
        def offline(*_args, **_kwargs):
            raise URLError("offline")

        self.assertFalse(publish(Notification("k", "t", "b"), "https://example.invalid/topic", None, offline))

    def test_publish_rejects_malformed_url(self):
        self.assertFalse(publish(Notification("k", "t", "b"), "http://[", None))

    def test_publish_rejects_non_https_before_building_request(self):
        with patch("src.codex_watch_notify.Request") as request:
            self.assertFalse(publish(Notification("k", "t", "b"), "http://example.invalid/topic", None))
        request.assert_not_called()

    def test_publish_logs_non_success_status(self):
        class Response:
            status = 503

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with self.assertLogs(level="WARNING") as logs:
            self.assertFalse(publish(
                Notification("k", "t", "b"),
                "https://example.invalid/topic",
                None,
                lambda *_args, **_kwargs: Response(),
            ))
        self.assertIn("503", "\n".join(logs.output))

    def test_process_line_sends_once(self):
        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json")
            sent = []
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "turn-2"},
            })
            self.assertTrue(process_line(line, seen, lambda item: sent.append(item) or True))
            self.assertFalse(process_line(line, seen, lambda item: sent.append(item) or True))
            self.assertEqual(len(sent), 1)

    def test_process_line_ignores_invalid_json(self):
        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json")
            self.assertFalse(process_line("not-json", seen, lambda _item: True))

    def test_process_line_ignores_json_that_is_not_an_object(self):
        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json")
            for line in ("null", "[]", '"text"', "1"):
                with self.subTest(line=line):
                    self.assertFalse(process_line(line, seen, lambda _item: True))

    def test_process_line_only_deduplicates_after_successful_send(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            seen = SeenKeys(path)
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "retry"},
            })
            attempts = []

            self.assertFalse(process_line(line, seen, lambda item: attempts.append(item) or False))
            self.assertFalse(path.exists())
            self.assertTrue(process_line(line, seen, lambda item: attempts.append(item) or True))
            self.assertFalse(process_line(line, seen, lambda item: attempts.append(item) or True))
            self.assertFalse(process_line(line, SeenKeys(path), lambda item: attempts.append(item) or True))
            self.assertEqual(len(attempts), 2)

    def test_process_line_contains_unexpected_send_errors(self):
        with TemporaryDirectory() as directory:
            seen = SeenKeys(Path(directory) / "seen.json")
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "crash"},
            })

            with patch("src.codex_watch_notify.logging.warning") as warning:
                self.assertFalse(process_line(line, seen, lambda _item: 1 / 0))
            self.assertTrue(process_line(line, seen, lambda _item: True))
            warning.assert_called_once()

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
            with patch("src.codex_watch_notify.time.sleep", side_effect=StopIteration):
                with self.assertRaises(StopIteration):
                    watch(Sessions(), SeenKeys(Path(directory) / "seen.json"),
                          lambda item: sent.append(item) or True, interval=0)

            self.assertEqual([item.key for item in sent], ["complete:new"])

    def test_watch_waits_for_newline_before_committing_trailing_record(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "split"},
            })
            calls = 0

            class Sessions:
                def rglob(self, _pattern):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        path.write_text(line)
                    return [] if calls == 1 else [path]

            sent = []
            sleeps = 0

            def finish_write(_interval):
                nonlocal sleeps
                sleeps += 1
                if sleeps == 1:
                    self.assertEqual(sent, [])
                    with path.open("a") as handle:
                        handle.write("\n")
                    return
                raise StopIteration

            with patch("src.codex_watch_notify.time.sleep", side_effect=finish_write):
                with self.assertRaises(StopIteration):
                    watch(Sessions(), SeenKeys(Path(directory) / "seen.json"),
                          lambda item: sent.append(item) or True, interval=0)

            self.assertEqual([item.key for item in sent], ["complete:split"])

    def test_watch_preserves_existing_unterminated_record(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text(json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "startup-split"},
            }))
            sent = []
            sleeps = 0

            def finish_write(_interval):
                nonlocal sleeps
                sleeps += 1
                if sleeps == 1:
                    self.assertEqual(sent, [])
                    with path.open("a") as handle:
                        handle.write("\n")
                    return
                raise StopIteration

            with patch("src.codex_watch_notify.time.sleep", side_effect=finish_write):
                with self.assertRaises(StopIteration):
                    watch(Path(directory), SeenKeys(Path(directory) / "seen.json"),
                          lambda item: sent.append(item) or True, interval=0)

            self.assertEqual([item.key for item in sent], ["complete:startup-split"])

    def test_watch_skips_bad_bytes_and_processes_following_event_once(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "after-bad-byte"},
            }) + "\n"
            calls = 0

            class Sessions:
                def rglob(self, _pattern):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        path.write_bytes(b"\xff\n" + line.encode())
                    return [] if calls == 1 else [path]

            sent = []
            with patch("src.codex_watch_notify.time.sleep", side_effect=[None, StopIteration]):
                with self.assertRaises(StopIteration):
                    watch(Sessions(), SeenKeys(Path(directory) / "seen.json"),
                          lambda item: sent.append(item) or True, interval=0)

            self.assertEqual([item.key for item in sent], ["complete:after-bad-byte"])

    def test_watch_resets_after_same_inode_truncate_and_regrow(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_bytes(b"x" * 32)
            inode = path.stat().st_ino
            line = json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "regrown"},
            }) + "\n"
            calls = 0

            class Sessions:
                def rglob(self, _pattern):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        path.write_bytes(line.encode())
                    return [path]

            sent = []
            with patch("src.codex_watch_notify.time.sleep", side_effect=StopIteration):
                with self.assertRaises(StopIteration):
                    watch(Sessions(), SeenKeys(Path(directory) / "seen.json"),
                          lambda item: sent.append(item) or True, interval=0)

            self.assertGreater(path.stat().st_size, 32)
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual([item.key for item in sent], ["complete:regrown"])

    def test_watch_resets_for_truncation_and_replacement(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            replacement = Path(directory) / "replacement.jsonl"
            path.write_text("x" * 1000)
            calls = 0

            def event(turn_id):
                return json.dumps({
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": turn_id},
                }) + "\n"

            class Sessions:
                def rglob(self, _pattern):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        path.write_text(event("truncated"))
                    elif calls == 3:
                        replacement.write_text(event("replaced"))
                        os.replace(replacement, path)
                    return [path]

            sent = []
            with patch("src.codex_watch_notify.time.sleep", side_effect=[None, StopIteration]):
                with self.assertRaises(StopIteration):
                    watch(Sessions(), SeenKeys(Path(directory) / "seen.json"),
                          lambda item: sent.append(item) or True, interval=0)

            self.assertEqual(
                [item.key for item in sent],
                ["complete:truncated", "complete:replaced"],
            )

    def test_watch_contains_startup_stat_race(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "vanished.jsonl"
            path.touch()
            calls = 0

            class Sessions:
                def rglob(self, _pattern):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        path.unlink()
                        return [path]
                    return []

            with patch("src.codex_watch_notify.time.sleep", side_effect=StopIteration):
                with self.assertRaises(StopIteration):
                    watch(Sessions(), SeenKeys(Path(directory) / "seen.json"), lambda _item: True, interval=0)

    def test_watch_contains_read_and_send_crashes_per_line(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            lines = [json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": turn_id},
            }) for turn_id in ("first", "second")]
            calls = 0

            class Sessions:
                def rglob(self, _pattern):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        path.write_text("\n".join(lines) + "\n")
                        return [path]
                    return []

            sent = []

            def send(item):
                sent.append(item)
                if item.key == "complete:first":
                    raise RuntimeError("publisher crashed")
                return True

            with patch("src.codex_watch_notify.time.sleep", side_effect=StopIteration):
                with self.assertRaises(StopIteration):
                    watch(Sessions(), SeenKeys(Path(directory) / "seen.json"), send, interval=0)
            self.assertEqual([item.key for item in sent], ["complete:first", "complete:second"])

            path.write_text("")
            class ExistingSession:
                def rglob(self, _pattern):
                    return [path]

            other_seen = SeenKeys(Path(directory) / "other-seen.json")
            with patch("src.codex_watch_notify.Path.open", side_effect=RuntimeError("read crashed")), \
                    patch("src.codex_watch_notify.time.sleep", side_effect=StopIteration), \
                    patch("src.codex_watch_notify.logging.warning") as warning:
                with self.assertRaises(StopIteration):
                    watch(ExistingSession(), other_seen, lambda _item: True, interval=0)
            warning.assert_called()

    def test_main_test_mode_returns_publish_status(self):
        with TemporaryDirectory() as directory:
            for published, expected in ((True, 0), (False, 1)):
                with self.subTest(published=published), \
                        patch.dict(os.environ, {"CODEX_WATCH_NTFY_URL": "https://example.invalid/topic"}, clear=True), \
                        patch("sys.argv", ["codex-watch-notify", "--test"]), \
                        patch("src.codex_watch_notify.Path.home", return_value=Path(directory)), \
                        patch("src.codex_watch_notify.publish", return_value=published) as publisher:
                    self.assertEqual(main(), expected)
                    publisher.assert_called_once_with(
                        Notification("manual", "Codex · 已完成", "Codex 任务已结束"),
                        "https://example.invalid/topic",
                        None,
                    )


if __name__ == "__main__":
    unittest.main()
