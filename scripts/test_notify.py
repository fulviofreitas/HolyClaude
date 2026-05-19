#!/usr/bin/env python3
"""Unit tests for notify.py — the HolyClaude notification dispatcher.

Pure-Python, stdlib only (``unittest``). No network, no Apprise, no Docker —
the embed builders are pure functions, so they are exercised directly.

Run locally:        python3 scripts/test_notify.py
Run via discovery:  python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify  # noqa: E402


def serialize(embed):
    """Flatten an embed to a single string for substring assertions."""
    return json.dumps(embed, ensure_ascii=False)


# --------------------------------------------------------------------------- #

class TestTruncation(unittest.TestCase):

    def test_short_text_untouched(self):
        self.assertEqual(notify.truncate("hello", 100), "hello")

    def test_long_text_clipped_with_marker(self):
        out = notify.truncate("x" * 500, 100)
        self.assertEqual(len(out), 100)
        self.assertTrue(out.endswith(notify.ELLIPSIS))

    def test_zero_limit(self):
        self.assertEqual(notify.truncate("anything", 0), "")

    def test_none_is_empty(self):
        self.assertEqual(notify.truncate(None, 50), "")

    def test_code_block_fits_limit(self):
        block = notify.code_block("y" * 5000, 1024, lang="json")
        self.assertLessEqual(len(block), 1024)
        self.assertTrue(block.startswith("```json"))
        self.assertTrue(block.endswith("```"))

    def test_code_block_neutralizes_inner_fence(self):
        self.assertNotIn("```\nhostile", notify.code_block("```\nhostile", 200))

    def test_human_duration(self):
        self.assertEqual(notify.human_duration(5), "5s")
        self.assertEqual(notify.human_duration(125), "2m 05s")
        self.assertEqual(notify.human_duration(7320), "2h 02m")
        self.assertEqual(notify.human_duration(None), "")


# --------------------------------------------------------------------------- #

class TestRedaction(unittest.TestCase):

    def test_anthropic_key(self):
        self.assertNotIn("sk-ant-",
                          notify.redact("key is sk-ant-api03-AbCdEf0123456789xyz"))

    def test_openai_key(self):
        self.assertNotIn("sk-proj",
                          notify.redact("OPENAI=sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX"))

    def test_github_token(self):
        out = notify.redact("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        self.assertNotIn("ghp_ABCDEFGHIJ", out)
        self.assertIn(notify.REDACTED, out)

    def test_aws_access_key(self):
        self.assertNotIn("AKIA", notify.redact("aws AKIAIOSFODNN7EXAMPLE done"))

    def test_jwt(self):
        jwt = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
               "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
               "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
        self.assertNotIn("eyJhbGci", notify.redact("auth " + jwt))

    def test_env_assignment_keeps_key(self):
        out = notify.redact("DISCORD_TOKEN=supersecretvalue123")
        self.assertIn("DISCORD_TOKEN=", out)
        self.assertNotIn("supersecretvalue123", out)

    def test_json_secret_value(self):
        out = notify.redact('{"api_key": "abcdef123456789"}')
        self.assertNotIn("abcdef123456789", out)

    def test_password_in_url(self):
        out = notify.redact("https://user:hunter2pass@host.example/path")
        self.assertNotIn("hunter2pass", out)

    def test_discord_webhook_url(self):
        url = "https://discord.com/api/webhooks/123456789012/Ab9_secrettoken-XY"
        self.assertNotIn("secrettoken", notify.redact("posted to " + url))

    def test_apprise_url(self):
        self.assertNotIn("realtoken",
                          notify.redact("dest discord://999888777/realtokenABC123"))

    def test_normal_text_survives(self):
        text = "Refactored the parser and added 3 tests. All green."
        self.assertEqual(notify.redact(text), text)

    def test_literal_extra_secret_scrubbed(self):
        notify._EXTRA_SECRETS.append("MyLiteralWebhookToken")
        try:
            self.assertNotIn("MyLiteralWebhookToken",
                              notify.redact("leak MyLiteralWebhookToken here"))
        finally:
            notify._EXTRA_SECRETS.remove("MyLiteralWebhookToken")


# --------------------------------------------------------------------------- #

class TestDiscordUrls(unittest.TestCase):

    def test_classify_apprise_discord(self):
        self.assertTrue(notify.is_discord_url("discord://111/tok"))

    def test_classify_raw_webhook(self):
        self.assertTrue(notify.is_discord_url(
            "https://discord.com/api/webhooks/1/abc"))

    def test_classify_non_discord(self):
        self.assertFalse(notify.is_discord_url("slack://a/b/c"))
        self.assertFalse(notify.is_discord_url("tg://bot/chat"))

    def test_parse_apprise_form(self):
        self.assertEqual(
            notify.discord_webhook_url("discord://123456/AbCtoken_xyz"),
            "https://discord.com/api/webhooks/123456/AbCtoken_xyz")

    def test_parse_strips_query(self):
        self.assertEqual(
            notify.discord_webhook_url("discord://123/tok?format=markdown"),
            "https://discord.com/api/webhooks/123/tok")

    def test_parse_botname_prefix(self):
        self.assertEqual(
            notify.discord_webhook_url("discord://bot@123/tok"),
            "https://discord.com/api/webhooks/123/tok")

    def test_parse_raw_url_passthrough(self):
        url = "https://discord.com/api/webhooks/42/zzz"
        self.assertEqual(notify.discord_webhook_url(url), url)

    def test_collect_targets_splits_by_kind(self):
        env = {
            "NOTIFY_DISCORD": "discord://100/tokenAAA",
            "NOTIFY_SLACK": "slack://a/b/c",
            "NOTIFY_URLS": "tg://bot/chat, discord://200/tokenBBB",
            "HOLYCLAUDE_NOTIFY_VERBOSITY": "verbose",  # config, not a URL
            "PATH": "/usr/bin",
        }
        discord, apprise_urls = notify.collect_targets(env)
        self.assertEqual(len(discord), 2)
        self.assertIn("slack://a/b/c", apprise_urls)
        self.assertIn("tg://bot/chat", apprise_urls)
        # The HOLYCLAUDE_NOTIFY_* knob must never be treated as a destination.
        self.assertNotIn("verbose", apprise_urls)
        self.assertEqual(len(apprise_urls), 2)


# --------------------------------------------------------------------------- #

def stop_ctx(**overrides):
    ctx = {
        "event": "stop", "session_id": "sess-abc123",
        "transcript_path": "/home/claude/.claude/projects/p/t.jsonl",
        "cwd": "/workspace/projects/demo", "branch": "feature/dark-mode",
        "model": "claude-opus-4-7", "permission_mode": "acceptEdits",
        "prompt": "Add a dark-mode toggle to the settings page.",
        "summary": "Added the toggle, persisted the choice, wrote two tests.",
        "title": "Add dark-mode toggle",
        "tools": ["Read", "Edit", "Edit", "Bash", "Bash", "Bash"],
        "files": ["/workspace/projects/demo/ui/settings.tsx",
                  "/workspace/projects/demo/ui/theme.ts"],
        "tokens_out": 4210, "tokens_ctx": 81233, "duration": 214.0,
    }
    ctx.update(overrides)
    return ctx


def error_ctx(**overrides):
    ctx = {
        "event": "error", "session_id": "sess-err99",
        "transcript_path": "/home/claude/.claude/projects/p/t.jsonl",
        "cwd": "/workspace/projects/demo", "branch": "main",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/workspace/projects/demo/app.py",
                       "old_string": "foo", "new_string": "bar"},
        "tool_use_id": "toolu_01ABC", "error": "String to replace not found.",
        "is_interrupt": False, "duration": 4.187,
        "prompt": "Fix the failing import.",
    }
    ctx.update(overrides)
    return ctx


def waiting_ctx(**overrides):
    ctx = {
        "event": "waiting", "session_id": "sess-wait1",
        "cwd": "/workspace/projects/demo", "branch": "main",
        "message": "Claude needs your permission to use Bash",
        "notif_title": "Permission needed",
        "notification_type": "permission_prompt",
    }
    ctx.update(overrides)
    return ctx


def assert_within_discord_limits(test, embed):
    test.assertLessEqual(len(embed["title"]), notify.LIMIT_TITLE)
    test.assertLessEqual(len(embed["description"]), notify.LIMIT_DESC)
    test.assertLessEqual(len(embed["author"]["name"]), notify.LIMIT_AUTHOR)
    test.assertLessEqual(len(embed["footer"]["text"]), notify.LIMIT_FOOTER)
    test.assertLessEqual(len(embed["fields"]), notify.LIMIT_FIELDS)
    for field in embed["fields"]:
        test.assertLessEqual(len(field["name"]), notify.LIMIT_FIELD_NAME)
        test.assertLessEqual(len(field["value"]), notify.LIMIT_FIELD_VALUE)
        test.assertTrue(field["value"].strip(), "embed fields must be non-empty")
    test.assertLessEqual(notify._embed_length(embed), notify.LIMIT_EMBED_TOTAL)


class TestStopEmbed(unittest.TestCase):

    def test_happy_path(self):
        embed = notify.build_embed("stop", stop_ctx(), "standard")
        self.assertEqual(embed["color"], notify.COLOR_SUCCESS)
        self.assertIn("Add dark-mode toggle", embed["title"])
        self.assertIn("task complete", embed["footer"]["text"])
        self.assertIn("timestamp", embed)
        names = [f["name"] for f in embed["fields"]]
        self.assertTrue(any("Directory" in n for n in names))
        self.assertTrue(any("Branch" in n for n in names))
        self.assertTrue(any("Files changed" in n for n in names))
        self.assertTrue(any("Session" in n for n in names))
        assert_within_discord_limits(self, embed)

    def test_files_count_in_field_name(self):
        embed = notify.build_embed("stop", stop_ctx(), "standard")
        names = [f["name"] for f in embed["fields"]]
        self.assertIn("📄 Files changed (2)", names)

    def test_minimal_verbosity_drops_detail(self):
        embed = notify.build_embed("stop", stop_ctx(), "minimal")
        names = " ".join(f["name"] for f in embed["fields"])
        self.assertNotIn("Prompt", names)
        self.assertNotIn("Files changed", names)
        assert_within_discord_limits(self, embed)

    def test_verbose_includes_transcript(self):
        embed = notify.build_embed("stop", stop_ctx(), "verbose")
        session = [f for f in embed["fields"] if "Session" in f["name"]][0]
        self.assertIn(".jsonl", session["value"])

    def test_model_in_author(self):
        embed = notify.build_embed("stop", stop_ctx(), "standard")
        self.assertIn("claude-opus-4-7", embed["author"]["name"])


class TestErrorEmbed(unittest.TestCase):

    def test_happy_path(self):
        embed = notify.build_embed("error", error_ctx(), "standard")
        self.assertEqual(embed["color"], notify.COLOR_ERROR)
        self.assertIn("Edit", embed["title"])
        self.assertIn("not found", embed["description"])
        names = [f["name"] for f in embed["fields"]]
        self.assertTrue(any("Tool" in n for n in names))
        self.assertTrue(any("Tool input" in n for n in names))
        self.assertTrue(any("Suggested next step" in n for n in names))
        assert_within_discord_limits(self, embed)

    def test_interrupt_uses_warning_color(self):
        embed = notify.build_embed("error", error_ctx(is_interrupt=True), "standard")
        self.assertEqual(embed["color"], notify.COLOR_WARNING)
        self.assertIn("interrupted", embed["title"])

    def test_suggested_step_is_actionable(self):
        embed = notify.build_embed("error", error_ctx(), "standard")
        step = [f for f in embed["fields"] if "Suggested" in f["name"]][0]
        self.assertIn("re-read the file", step["value"])

    def test_tool_input_rendered_as_code(self):
        embed = notify.build_embed("error", error_ctx(), "standard")
        tin = [f for f in embed["fields"] if "Tool input" in f["name"]][0]
        self.assertIn("```", tin["value"])
        self.assertIn("file_path", tin["value"])


class TestWaitingEmbed(unittest.TestCase):

    def test_happy_path(self):
        embed = notify.build_embed("waiting", waiting_ctx(), "standard")
        self.assertEqual(embed["color"], notify.COLOR_WARNING)
        self.assertIn("Permission needed", embed["title"])
        self.assertIn("permission to use Bash", embed["description"])
        assert_within_discord_limits(self, embed)

    def test_verbose_shows_notification_type(self):
        embed = notify.build_embed("waiting", waiting_ctx(), "verbose")
        joined = serialize(embed)
        self.assertIn("permission_prompt", joined)


# --------------------------------------------------------------------------- #

class TestDiscordLimits(unittest.TestCase):

    def test_oversized_stop_embed_is_clamped(self):
        ctx = stop_ctx(
            prompt="P" * 20000,
            summary="S" * 20000,
            files=["/workspace/projects/demo/file_%d.py" % i for i in range(200)],
            tools=["Tool%d" % i for i in range(300)],
        )
        embed = notify.build_embed("stop", ctx, "verbose")
        assert_within_discord_limits(self, embed)

    def test_oversized_error_embed_is_clamped(self):
        ctx = error_ctx(error="E" * 30000,
                        tool_input={"blob": "x" * 30000},
                        prompt="P" * 30000)
        embed = notify.build_embed("error", ctx, "verbose")
        assert_within_discord_limits(self, embed)

    def test_finalize_caps_field_count_at_25(self):
        embed = {
            "title": "t", "description": "d",
            "author": {"name": "a"}, "footer": {"text": "f"},
            "fields": [{"name": "n%d" % i, "value": "v%d" % i, "inline": True}
                       for i in range(40)],
        }
        notify.finalize_embed(embed)
        self.assertEqual(len(embed["fields"]), 25)

    def test_finalize_enforces_total_budget(self):
        embed = {
            "title": "T" * 256, "description": "D" * 4096,
            "author": {"name": "A" * 256}, "footer": {"text": "F" * 2048},
            "fields": [{"name": "N" * 256, "value": "V" * 1024, "inline": False}
                       for _ in range(20)],
        }
        notify.finalize_embed(embed)
        self.assertLessEqual(notify._embed_length(embed), notify.LIMIT_EMBED_TOTAL)

    def test_finalize_drops_empty_fields(self):
        embed = {
            "title": "t", "description": "d",
            "author": {"name": "a"}, "footer": {"text": "f"},
            "fields": [{"name": "keep", "value": "yes", "inline": False},
                       {"name": "drop", "value": "   ", "inline": False},
                       {"name": "", "value": "novalue", "inline": False}],
        }
        notify.finalize_embed(embed)
        self.assertEqual([f["name"] for f in embed["fields"]], ["keep"])


# --------------------------------------------------------------------------- #

class TestSanitizationInEmbed(unittest.TestCase):

    SECRET = "sk-ant-api03-LEAKED0123456789abcdef"

    def test_secret_in_prompt_is_redacted(self):
        embed = notify.build_embed("stop", stop_ctx(prompt="run with " + self.SECRET),
                                   "verbose")
        self.assertNotIn(self.SECRET, serialize(embed))

    def test_secret_in_summary_is_redacted(self):
        embed = notify.build_embed("stop", stop_ctx(summary="done; " + self.SECRET),
                                   "verbose")
        self.assertNotIn(self.SECRET, serialize(embed))

    def test_secret_in_error_is_redacted(self):
        embed = notify.build_embed("error", error_ctx(error="failed: " + self.SECRET),
                                   "verbose")
        self.assertNotIn(self.SECRET, serialize(embed))

    def test_secret_in_tool_input_is_redacted(self):
        embed = notify.build_embed(
            "error", error_ctx(tool_input={"command": "curl -H " + self.SECRET}),
            "verbose")
        self.assertNotIn(self.SECRET, serialize(embed))

    def test_secret_in_waiting_message_is_redacted(self):
        embed = notify.build_embed("waiting",
                                   waiting_ctx(message="token " + self.SECRET),
                                   "verbose")
        self.assertNotIn(self.SECRET, serialize(embed))

    def test_secret_in_build_text(self):
        text = notify.build_text("stop", stop_ctx(summary="x " + self.SECRET),
                                 "verbose")
        self.assertNotIn(self.SECRET, text)


# --------------------------------------------------------------------------- #

class TestMissingFields(unittest.TestCase):
    """A near-empty ctx must still yield a valid, sendable embed."""

    def test_empty_stop_ctx(self):
        embed = notify.build_embed("stop", {"event": "stop"}, "standard")
        self.assertTrue(embed["title"])
        self.assertTrue(embed["description"])
        self.assertTrue(embed["author"]["name"])
        assert_within_discord_limits(self, embed)

    def test_empty_error_ctx(self):
        embed = notify.build_embed("error", {"event": "error"}, "standard")
        self.assertTrue(embed["description"])
        assert_within_discord_limits(self, embed)

    def test_empty_waiting_ctx(self):
        embed = notify.build_embed("waiting", {"event": "waiting"}, "standard")
        self.assertTrue(embed["description"])
        assert_within_discord_limits(self, embed)

    def test_unknown_event_falls_back_to_stop_shape(self):
        embed = notify.build_embed("mystery", {"event": "mystery"}, "standard")
        self.assertEqual(embed["color"], notify.COLOR_SUCCESS)

    def test_build_context_without_transcript(self):
        ctx = notify.build_context("stop", {"session_id": "s1", "cwd": "/tmp"})
        self.assertEqual(ctx["session_id"], "s1")
        self.assertEqual(ctx["cwd"], "/tmp")

    def test_no_session_field_when_no_id(self):
        embed = notify.build_embed("stop", {"event": "stop", "cwd": "/tmp"},
                                   "standard")
        self.assertFalse(any("Session" in f["name"] for f in embed["fields"]))


# --------------------------------------------------------------------------- #

class TestBuildText(unittest.TestCase):

    def test_within_content_limit(self):
        text = notify.build_text("stop", stop_ctx(prompt="P" * 9000,
                                                  summary="S" * 9000), "verbose")
        self.assertLessEqual(len(text), notify.LIMIT_CONTENT)

    def test_error_text_has_next_step(self):
        text = notify.build_text("error", error_ctx(), "standard")
        self.assertIn("Next:", text)

    def test_title_per_event(self):
        self.assertIn("Task complete",
                      notify.build_title("stop", stop_ctx(title="")))
        self.assertIn("Add dark-mode toggle",
                      notify.build_title("stop", stop_ctx()))
        self.assertIn("Tool failure", notify.build_title("error", error_ctx()))
        self.assertIn("Permission needed",
                      notify.build_title("waiting", waiting_ctx()))


# --------------------------------------------------------------------------- #

class TestConfigKnobs(unittest.TestCase):

    def test_verbosity_default_and_validation(self):
        self.assertEqual(notify.get_verbosity({}), "standard")
        self.assertEqual(notify.get_verbosity(
            {"HOLYCLAUDE_NOTIFY_VERBOSITY": "verbose"}), "verbose")
        self.assertEqual(notify.get_verbosity(
            {"HOLYCLAUDE_NOTIFY_VERBOSITY": "bogus"}), "standard")

    def test_style_default_and_validation(self):
        self.assertEqual(notify.get_style({}), "embed")
        self.assertEqual(notify.get_style(
            {"HOLYCLAUDE_NOTIFY_STYLE": "simple"}), "simple")
        self.assertEqual(notify.get_style(
            {"HOLYCLAUDE_NOTIFY_STYLE": "bogus"}), "embed")

    def test_legacy_events_present(self):
        for event in ("stop", "error", "waiting"):
            self.assertIn(event, notify.LEGACY_EVENTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
