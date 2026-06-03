#!/usr/bin/env python3
"""HolyClaude — notification dispatcher.

Usage:
    notify.py stop      # a task finished      (Claude Code "Stop" hook)
    notify.py error     # a tool-use failure   (Claude Code "PostToolUseFailure" hook)
    notify.py waiting   # input/permission     (Claude Code "Notification" hook)

The calling agent passes its hook-event JSON on **stdin** (Claude Code, Codex
and Gemini CLI all do this). This script turns that event — plus whatever it
can mine from the session transcript — into a context-rich Discord embed so a
notification is useful on its own, without opening the session.

Destinations are read from ``NOTIFY_*`` environment variables (same as before):

* Discord webhooks (``discord://…`` or a raw ``…/api/webhooks/…`` URL) receive a
  native rich embed posted straight to the webhook.
* Every other service (Telegram, Slack, Email, Gotify, …) receives an enriched
  Markdown message via Apprise, exactly as before.

Behaviour is unchanged when unconfigured: if ``~/.claude/notify-on`` is absent
or no ``NOTIFY_*`` variable is set, the script exits silently and does nothing.

Two optional knobs tune the output (see README → Notifications):

* ``HOLYCLAUDE_NOTIFY_STYLE``      ``embed`` (default) | ``simple``
* ``HOLYCLAUDE_NOTIFY_VERBOSITY``  ``minimal`` | ``standard`` (default) | ``verbose``

Notifications are best-effort: every failure is swallowed so a notification can
never break the parent tool invocation.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

FLAG_FILE = "/home/claude/.claude/notify-on"

# NOTIFY_* keys that carry configuration rather than a destination URL.
RESERVED_NOTIFY_KEYS = {"NOTIFY_URLS"}

# Discord payload limits (characters). https://discord.com/developers/docs
LIMIT_CONTENT = 2000
LIMIT_TITLE = 256
LIMIT_DESC = 4096
LIMIT_FIELD_NAME = 256
LIMIT_FIELD_VALUE = 1024
LIMIT_FOOTER = 2048
LIMIT_AUTHOR = 256
LIMIT_FIELDS = 25
LIMIT_EMBED_TOTAL = 6000

# Embed accent colours.
COLOR_SUCCESS = 0x2ECC71  # green
COLOR_ERROR = 0xE74C3C    # red
COLOR_WARNING = 0xF1C40F  # yellow
COLOR_INFO = 0x3498DB     # blue

ELLIPSIS = "…"
REDACTED = "[redacted]"

# Tools whose input names a file Claude touched.
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Update", "Create"}

# Per-verbosity character / item budgets.
BUDGETS = {
    "minimal": {
        "summary": 280, "prompt": 0, "error": 600,
        "tool_input": 0, "files": 0, "tools": False, "extras": False,
    },
    "standard": {
        "summary": 700, "prompt": 400, "error": 1400,
        "tool_input": 600, "files": 12, "tools": True, "extras": False,
    },
    "verbose": {
        "summary": 1800, "prompt": 1000, "error": 3400,
        "tool_input": 1000, "files": 30, "tools": True, "extras": True,
    },
}

# Legacy plain-text strings — the ultimate fallback when there is no context
# at all. Kept so behaviour degrades to the original v1 messages.
LEGACY_EVENTS = {
    "stop": ("HolyClaude — Task Complete",
             "Claude has finished the current task.", "success"),
    "error": ("HolyClaude — Something Went Wrong",
              "A tool use failure occurred. Check the session for details.", "failure"),
    "waiting": ("HolyClaude — Waiting For You",
                "Claude is waiting for your input.", "warning"),
}

# Literal secrets (configured webhook tokens, etc.) scrubbed from every string
# regardless of the pattern rules. Populated in main().
_EXTRA_SECRETS = []

# --------------------------------------------------------------------------- #
# Sanitisation                                                                #
# --------------------------------------------------------------------------- #

# (pattern, replacement). Replacements with back-references keep the key name
# so a redacted value still reads as "API_KEY=[redacted]".
_REDACTION_RULES = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"), REDACTED),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{28,}"), REDACTED),
    (re.compile(r"github_pat_[A-Za-z0-9_]{28,}"), REDACTED),
    (re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"), REDACTED),
    (re.compile(r"AIza[A-Za-z0-9_-]{30,}"), REDACTED),
    (re.compile(r"A(?:KIA|SIA)[0-9A-Z]{16}"), REDACTED),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"), REDACTED),
    # Apprise / messaging URLs that embed credentials.
    (re.compile(r"(?i)\b(?:discord|tg|pover|slack|gotify|ntfy|matrixs?|mailtos?|"
                r"twilio|pushover)://[^\s\"'<>]+"), REDACTED),
    # Raw Discord webhook URLs.
    (re.compile(r"(?i)https?://(?:[a-z]+\.)?discord(?:app)?\.com/api/"
                r"(?:v\d+/)?webhooks/\d+/[A-Za-z0-9._-]+"), REDACTED),
    # `user:password@host` credentials inside any URL.
    (re.compile(r"(://[^/\s:@]+):[^/\s@]+@"), r"\1:%s@" % REDACTED),
    # `bearer <token>` headers.
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"), "bearer " + REDACTED),
    # ALL_CAPS env-style assignments: FOO_TOKEN=value -> FOO_TOKEN=[redacted].
    (re.compile(r"\b([A-Z][A-Z0-9_]{1,48}"
                r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|CREDENTIALS?))(\s*=\s*)\S+"),
     r"\1\2" + REDACTED),
    # key/secret/token/password as a json or kwarg value.
    (re.compile(r"(?i)([\"']?\b(?:api[_-]?key|access[_-]?key|client[_-]?secret|"
                r"secret|token|password|passwd|auth[_-]?token)\b[\"']?\s*[:=]\s*)"
                r"([\"']?)[^\s\"',}{]{4,}"),
     r"\1\2" + REDACTED),
]


def redact(text):
    """Return ``text`` with secrets, credentials and tokens masked.

    Applies the configured literal scrub list first (exact configured webhook
    tokens), then the pattern rules. Always returns a string.
    """
    if text is None:
        return ""
    out = str(text)
    for secret in _EXTRA_SECRETS:
        if secret and len(secret) >= 6 and secret in out:
            out = out.replace(secret, REDACTED)
    for pattern, repl in _REDACTION_RULES:
        out = pattern.sub(repl, out)
    return out


# --------------------------------------------------------------------------- #
# Text helpers                                                                #
# --------------------------------------------------------------------------- #

def truncate(text, limit, marker=ELLIPSIS):
    """Clip ``text`` to ``limit`` characters, appending ``marker`` if clipped."""
    if text is None:
        return ""
    text = str(text)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(marker):
        return text[:limit]
    return text[:limit - len(marker)].rstrip() + marker


def code_block(body, limit, lang=""):
    """Wrap ``body`` in a fenced code block that fits within ``limit`` chars."""
    body = redact(body or "").replace("```", "ʼʼʼ")
    overhead = len("```" + lang + "\n") + len("\n```")
    inner = truncate(body, max(0, limit - overhead))
    if not inner:
        return ""
    return "```" + lang + "\n" + inner + "\n```"


def human_int(value):
    """Format an int with thousands separators; '' for unknown values."""
    try:
        return "{:,}".format(int(value))
    except (TypeError, ValueError):
        return ""


def human_duration(seconds):
    """Render a second count as a compact human string ('2m 04s')."""
    try:
        seconds = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if seconds < 0:
        return ""
    if seconds < 60:
        return "{}s".format(seconds)
    if seconds < 3600:
        return "{}m {:02d}s".format(seconds // 60, seconds % 60)
    return "{}h {:02d}m".format(seconds // 3600, (seconds % 3600) // 60)


def short_path(path, cwd=None):
    """Render ``path`` relative to ``cwd`` when possible, else as given."""
    if not path:
        return ""
    path = str(path)
    if cwd:
        try:
            rel = os.path.relpath(path, cwd)
        except (ValueError, TypeError):
            rel = path
        if not rel.startswith(".."):
            return rel
    return path


# --------------------------------------------------------------------------- #
# Hook input + transcript mining                                              #
# --------------------------------------------------------------------------- #

def read_hook_input():
    """Parse the hook-event JSON object from stdin. Returns {} on anything odd."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
    except Exception:
        return {}
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _message_text(message):
    """Extract concatenated plain text from a transcript message object."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p)


def _looks_like_prompt(text):
    """True when ``text`` reads like a genuine user prompt (not a meta wrapper)."""
    text = (text or "").strip()
    if not text:
        return False
    head = text.lstrip()
    return not head.startswith(("<command-name>", "<local-command",
                                "<command-message>", "[Request interrupted",
                                "Caveat:", "<system-reminder>"))


def parse_transcript(path):
    """Mine a Claude Code transcript (.jsonl) for notification context.

    Every field is best-effort; a missing or malformed transcript simply yields
    an empty/partial dict. Never raises.
    """
    info = {
        "prompt": "", "last_prompt": "", "summary": "", "model": "",
        "branch": "", "cwd": "", "tools": [], "files": [],
        "tokens_out": 0, "tokens_ctx": 0, "duration": None, "title": "",
    }
    if not path:
        return info
    path = os.path.expanduser(str(path))
    if not os.path.isfile(path):
        return info
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except Exception:
        return info

    # Cap pathological transcripts: keep the head (first prompt) and the tail
    # (recent activity, final summary, last timestamp).
    if len(lines) > 6000:
        lines = lines[:150] + lines[-1200:]

    first_ts = last_ts = None
    last_text = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue

        ts = _parse_ts(entry.get("timestamp"))
        if ts is not None:
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts

        branch = entry.get("gitBranch")
        if branch:
            info["branch"] = str(branch)
        cwd = entry.get("cwd")
        if cwd:
            info["cwd"] = str(cwd)

        etype = entry.get("type")
        if etype == "summary" and entry.get("summary"):
            info["title"] = str(entry.get("summary"))

        message = entry.get("message")
        if etype == "user":
            text = _message_text(message)
            if _looks_like_prompt(text):
                cleaned = text.strip()
                if not info["prompt"]:
                    info["prompt"] = cleaned
                info["last_prompt"] = cleaned
        elif etype == "assistant" and isinstance(message, dict):
            if message.get("model"):
                info["model"] = str(message.get("model"))
            usage = message.get("usage")
            if isinstance(usage, dict):
                info["tokens_out"] += _int(usage.get("output_tokens"))
                ctx = (_int(usage.get("input_tokens"))
                       + _int(usage.get("cache_read_input_tokens"))
                       + _int(usage.get("cache_creation_input_tokens")))
                if ctx:
                    info["tokens_ctx"] = ctx
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        name = str(block.get("name", "")).strip()
                        if name:
                            info["tools"].append(name)
                        if name in FILE_TOOLS:
                            inp = block.get("input") or {}
                            if isinstance(inp, dict):
                                fpath = inp.get("file_path") or inp.get("notebook_path")
                                if fpath and fpath not in info["files"]:
                                    info["files"].append(str(fpath))
                    elif block.get("type") == "text" and block.get("text"):
                        last_text = str(block.get("text"))

    info["summary"] = last_text.strip()
    if first_ts is not None and last_ts is not None and last_ts >= first_ts:
        info["duration"] = (last_ts - first_ts).total_seconds()
    return info


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_ts(value):
    """Parse an ISO-8601 timestamp string into an aware datetime, or None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def git_branch(cwd):
    """Read the current git branch from ``cwd``/.git/HEAD without spawning git."""
    if not cwd:
        return ""
    head = os.path.join(cwd, ".git", "HEAD")
    try:
        if os.path.isfile(os.path.join(cwd, ".git")):
            # Worktree: .git is a file pointing at the real gitdir.
            with open(os.path.join(cwd, ".git"), "r", encoding="utf-8") as handle:
                pointer = handle.read().strip()
            if pointer.startswith("gitdir:"):
                head = os.path.join(pointer.split(":", 1)[1].strip(), "HEAD")
        with open(head, "r", encoding="utf-8") as handle:
            ref = handle.read().strip()
    except Exception:
        return ""
    if ref.startswith("ref: refs/heads/"):
        return ref[len("ref: refs/heads/"):]
    if ref.startswith("ref:"):
        return ref.split("/")[-1]
    return ref[:12]  # detached HEAD — short sha


# --------------------------------------------------------------------------- #
# Context assembly                                                            #
# --------------------------------------------------------------------------- #

def build_context(event, hook):
    """Merge the hook-event JSON and the mined transcript into one ctx dict."""
    transcript_path = hook.get("transcript_path")
    tx = parse_transcript(transcript_path)

    cwd = hook.get("cwd") or tx.get("cwd") or os.getcwd()
    branch = tx.get("branch") or git_branch(cwd)

    ctx = {
        "event": event,
        "session_id": hook.get("session_id") or "",
        "transcript_path": transcript_path or "",
        "cwd": cwd,
        "branch": branch,
        "model": tx.get("model") or "",
        "permission_mode": hook.get("permission_mode") or "",
        "prompt": tx.get("prompt") or "",
        "last_prompt": tx.get("last_prompt") or tx.get("prompt") or "",
        "summary": hook.get("last_assistant_message") or tx.get("summary") or "",
        "title": tx.get("title") or "",
        "tools": tx.get("tools") or [],
        "files": tx.get("files") or [],
        "tokens_out": tx.get("tokens_out") or 0,
        "tokens_ctx": tx.get("tokens_ctx") or 0,
        "duration": tx.get("duration"),
        # error-specific
        "tool_name": hook.get("tool_name") or "",
        "tool_input": hook.get("tool_input"),
        "tool_use_id": hook.get("tool_use_id") or "",
        "error": hook.get("error") or "",
        "is_interrupt": bool(hook.get("is_interrupt")),
        # waiting-specific
        "message": hook.get("message") or "",
        "notif_title": hook.get("title") or "",
        "notification_type": hook.get("notification_type") or "",
    }
    duration_ms = hook.get("duration_ms")
    if duration_ms is not None:
        ctx["duration"] = _int(duration_ms) / 1000.0
    return ctx


def suggest_next_step(tool_name, error):
    """Best-effort hint for an error embed, derived from the tool + message."""
    err = (error or "").lower()
    tool = (tool_name or "").lower()
    if "permission" in err or "not allowed" in err or "denied" in err:
        return "Permission denied — check the tool allowlist in `~/.claude/settings.json`."
    if "timed out" in err or "timeout" in err:
        return "The operation timed out — retry, or raise the tool `timeout`."
    if tool in {"edit", "multiedit"} and ("multiple" in err or "more than once" in err):
        return "`old_string` is ambiguous — add surrounding context to make it unique."
    if tool in {"edit", "multiedit", "write"} and "not found" in err:
        return "The target text changed — re-read the file, then redo the edit."
    if "enoent" in err or "no such file" in err:
        return "Path does not exist — verify the file path before retrying."
    if tool == "bash" and ("non-zero" in err or "exit" in err):
        return "The shell command exited non-zero — inspect the output above."
    if "interrupt" in err:
        return "The tool was interrupted — re-run it if the result is still needed."
    return "Open the session transcript for the full failure context."


# --------------------------------------------------------------------------- #
# Embed construction                                                          #
# --------------------------------------------------------------------------- #

def _field(name, value, inline=False):
    """Build one embed field, or None when the value is empty."""
    value = "" if value is None else str(value)
    if not value.strip():
        return None
    return {
        "name": truncate(name, LIMIT_FIELD_NAME),
        "value": truncate(value, LIMIT_FIELD_VALUE),
        "inline": bool(inline),
    }


def _files_value(files, limit, cwd):
    """Render a touched-files list as a bulleted, length-bounded string."""
    if not files:
        return ""
    shown = files[:limit]
    lines = ["• `{}`".format(short_path(f, cwd)) for f in shown]
    extra = len(files) - len(shown)
    if extra > 0:
        lines.append("• …and {} more".format(extra))
    return truncate("\n".join(lines), LIMIT_FIELD_VALUE)


def _tools_value(tools, verbose):
    """Render a tool-usage summary ('7 calls — Bash ×3, Edit ×2, Read ×2')."""
    if not tools:
        return ""
    counts = Counter(tools)
    ordered = counts.most_common(None if verbose else 5)
    parts = ["{} ×{}".format(name, n) for name, n in ordered]
    hidden = len(counts) - len(ordered)
    if hidden > 0:
        parts.append("+{} more".format(hidden))
    return "{} call{} — {}".format(len(tools), "" if len(tools) == 1 else "s",
                                    ", ".join(parts))


def build_embed(event, ctx, verbosity="standard"):
    """Construct a Discord embed dict for ``event`` from ``ctx``.

    Pure and side-effect free — the unit tests exercise it directly. The result
    is always passed through :func:`finalize_embed` before sending.
    """
    budget = BUDGETS.get(verbosity, BUDGETS["standard"])
    cwd = ctx.get("cwd") or ""
    fields = []

    def add(name, value, inline=False):
        field = _field(name, value, inline)
        if field:
            fields.append(field)

    if event == "error":
        interrupted = ctx.get("is_interrupt")
        tool = ctx.get("tool_name") or "tool"
        title = ("⛔ {} interrupted".format(tool) if interrupted
                 else "❌ Tool failure — {}".format(tool))
        color = COLOR_WARNING if interrupted else COLOR_ERROR
        description = (code_block(ctx.get("error"), budget["error"])
                       or "A tool-use failure occurred.")
        add("🔧 Tool", "`{}`".format(ctx.get("tool_name") or "—"), inline=True)
        add("📂 Directory", "`{}`".format(os.path.basename(cwd.rstrip("/")) or cwd),
            inline=True)
        add("🌿 Branch", "`{}`".format(ctx.get("branch")) if ctx.get("branch") else "",
            inline=True)
        add("⏱️ Ran for", human_duration(ctx.get("duration")), inline=True)
        if budget["tool_input"]:
            add("🧾 Tool input",
                code_block(_stringify(ctx.get("tool_input")), budget["tool_input"],
                           lang="json"))
        if budget["prompt"]:
            last_prompt = ctx.get("last_prompt") or ctx.get("prompt")
            if last_prompt:
                add("🗣️ You asked",
                    redact(truncate(last_prompt, budget["prompt"])))
        add("💡 Suggested next step",
            suggest_next_step(ctx.get("tool_name"), ctx.get("error")))
        _add_session(add, ctx, budget)
        footer = "HolyClaude · tool failure"

    elif event == "waiting":
        title = "⏳ " + (ctx.get("notif_title") or "Claude is waiting for you")
        color = COLOR_WARNING
        description = (redact(ctx.get("message"))
                       or "Claude is waiting for your input.")
        add("📂 Directory", "`{}`".format(os.path.basename(cwd.rstrip("/")) or cwd),
            inline=True)
        add("🌿 Branch", "`{}`".format(ctx.get("branch")) if ctx.get("branch") else "",
            inline=True)
        if budget["extras"] and ctx.get("notification_type"):
            add("🔔 Type", "`{}`".format(ctx["notification_type"]), inline=True)
        _add_session(add, ctx, budget)
        footer = "HolyClaude · waiting"

    else:  # "stop" and any unknown event
        title = "✅ Task complete"
        if ctx.get("title"):
            title = "✅ " + ctx["title"]
        color = COLOR_SUCCESS
        description = (redact(truncate(ctx.get("summary"), budget["summary"]))
                       or "Claude finished the current task.")
        add("📂 Directory", "`{}`".format(os.path.basename(cwd.rstrip("/")) or cwd),
            inline=True)
        add("🌿 Branch", "`{}`".format(ctx.get("branch")) if ctx.get("branch") else "",
            inline=True)
        add("⏱️ Duration", human_duration(ctx.get("duration")), inline=True)
        if budget["tools"]:
            add("🔧 Tools", _tools_value(ctx.get("tools"), budget["extras"]),
                inline=True)
            tokens = _tokens_value(ctx)
            add("🧮 Tokens", tokens, inline=True)
            add("🤖 Model", "`{}`".format(ctx["model"]) if ctx.get("model") else "",
                inline=True)
        if budget["prompt"]:
            last_prompt = ctx.get("last_prompt") or ctx.get("prompt")
            if last_prompt:
                add("🗣️ You asked",
                    redact(truncate(last_prompt, budget["prompt"])))
            if ctx.get("summary"):
                add("🤖 Claude replied",
                    redact(truncate(ctx["summary"], LIMIT_FIELD_VALUE)))
            first_prompt = ctx.get("prompt")
            if (budget["extras"] and first_prompt
                    and first_prompt != last_prompt):
                add("📜 Session started with",
                    redact(truncate(first_prompt, budget["prompt"])))
        if budget["files"] and ctx.get("files"):
            add("📄 Files changed ({})".format(len(ctx["files"])),
                _files_value(ctx["files"], budget["files"], cwd))
        _add_session(add, ctx, budget)
        footer = "HolyClaude · task complete"

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "author": {"name": _author_name(ctx)},
        "footer": {"text": footer},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": fields,
    }
    return finalize_embed(embed)


def _tokens_value(ctx):
    out = human_int(ctx.get("tokens_out"))
    ctxn = human_int(ctx.get("tokens_ctx"))
    if out and ctxn:
        return "≈{} out · ≈{} context".format(out, ctxn)
    if out:
        return "≈{} generated".format(out)
    return ""


def _author_name(ctx):
    model = ctx.get("model")
    if model:
        return truncate("HolyClaude · {}".format(model), LIMIT_AUTHOR)
    return "HolyClaude"


def session_url(ctx, environ=None):
    """Render a clickable session URL from ``HOLYCLAUDE_NOTIFY_SESSION_URL``.

    Template placeholders are URL-quoted before substitution so a space or
    slash cannot break the link:

    * ``{session_id}``
    * ``{project}``         — basename of cwd
    * ``{project_slug}``    — Claude Code's dashed cwd form (``-workspace-…``)
    * ``{cwd}``
    * ``{branch}``
    * ``{transcript_path}``

    Returns ``""`` when the env var is unset, when the template references an
    unknown placeholder, or when no ``session_id`` is available.
    """
    if not ctx.get("session_id"):
        return ""
    template = ((environ if environ is not None else os.environ)
                .get("HOLYCLAUDE_NOTIFY_SESSION_URL") or "").strip()
    if not template:
        return ""
    cwd = ctx.get("cwd") or ""
    project_slug = ""
    if cwd:
        project_slug = "-" + cwd.strip("/").replace("/", "-") if cwd.startswith("/") \
            else cwd.replace("/", "-")
    values = {
        "session_id": ctx.get("session_id") or "",
        "project": os.path.basename(cwd.rstrip("/")) or cwd,
        "project_slug": project_slug,
        "cwd": cwd,
        "branch": ctx.get("branch") or "",
        "transcript_path": ctx.get("transcript_path") or "",
    }
    safe = {key: urllib.parse.quote(val, safe="") for key, val in values.items()}
    try:
        return template.format(**safe)
    except (KeyError, IndexError, ValueError):
        return ""


def _session_code(ctx):
    """Render the session-id code, wrapping it in a markdown link when set."""
    sid = ctx.get("session_id")
    if not sid:
        return ""
    url = session_url(ctx)
    return "[`{}`]({})".format(sid, url) if url else "`{}`".format(sid)


def _add_session(add, ctx, budget):
    """Append the session-id field, plus the transcript path when verbose."""
    value = _session_code(ctx)
    if budget["extras"]:
        if ctx.get("transcript_path"):
            value = (value + "\n`{}`".format(ctx["transcript_path"])).strip()
        if ctx.get("permission_mode"):
            value = (value + "\nmode: `{}`".format(ctx["permission_mode"])).strip()
    add("🧵 Session", value)


def _stringify(value):
    """Render a tool-input object as compact, readable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _embed_length(embed):
    """Total character count Discord charges against the 6000-char budget."""
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += len(embed.get("author", {}).get("name", ""))
    total += len(embed.get("footer", {}).get("text", ""))
    for field in embed.get("fields", []):
        total += len(field.get("name", "")) + len(field.get("value", ""))
    return total


def finalize_embed(embed):
    """Clamp every embed component to Discord's hard limits.

    Truncates oversized strings, caps the field count at 25, and trims trailing
    fields (then the description) until the whole embed fits in 6000 chars.
    """
    embed["title"] = truncate(embed.get("title") or "", LIMIT_TITLE)
    embed["description"] = truncate(embed.get("description") or "", LIMIT_DESC)
    author = embed.get("author") or {}
    if author.get("name"):
        author["name"] = truncate(author["name"], LIMIT_AUTHOR)
        embed["author"] = author
    footer = embed.get("footer") or {}
    if footer.get("text"):
        footer["text"] = truncate(footer["text"], LIMIT_FOOTER)
        embed["footer"] = footer

    clean = []
    for field in embed.get("fields", []):
        name = truncate(field.get("name") or "", LIMIT_FIELD_NAME)
        value = truncate(field.get("value") or "", LIMIT_FIELD_VALUE)
        if not name or not value.strip():
            continue
        clean.append({"name": name, "value": value, "inline": bool(field.get("inline"))})
    embed["fields"] = clean[:LIMIT_FIELDS]

    # Trim trailing fields, then the description, to satisfy the total budget.
    while _embed_length(embed) > LIMIT_EMBED_TOTAL and embed["fields"]:
        embed["fields"].pop()
    if _embed_length(embed) > LIMIT_EMBED_TOTAL:
        overflow = _embed_length(embed) - LIMIT_EMBED_TOTAL
        keep = max(0, len(embed["description"]) - overflow)
        embed["description"] = truncate(embed["description"], keep)
    return embed


# --------------------------------------------------------------------------- #
# Plain-text rendering (Apprise body + Discord content fallback)               #
# --------------------------------------------------------------------------- #

def build_title(event, ctx):
    """Short notification title for Apprise / the simple fallback."""
    if event == "error":
        return "HolyClaude — Tool failure: {}".format(ctx.get("tool_name") or "tool")
    if event == "waiting":
        return "HolyClaude — " + (ctx.get("notif_title") or "Waiting for you")
    if ctx.get("title"):
        return "HolyClaude — {}".format(ctx["title"])
    return "HolyClaude — Task complete"


def build_text(event, ctx, verbosity="standard"):
    """Render an enriched Markdown message (Apprise body / Discord fallback)."""
    budget = BUDGETS.get(verbosity, BUDGETS["standard"])
    cwd = ctx.get("cwd") or ""
    lines = []

    if event == "error":
        lines.append("**❌ Tool failure — {}**".format(ctx.get("tool_name") or "tool"))
        if ctx.get("error"):
            lines.append(redact(truncate(ctx["error"], budget["error"])))
        meta = _meta_line([("Tool", ctx.get("tool_name")),
                           ("Dir", os.path.basename(cwd.rstrip("/")) or cwd),
                           ("Branch", ctx.get("branch"))])
        if meta:
            lines.append(meta)
        if budget["prompt"]:
            last_prompt = ctx.get("last_prompt") or ctx.get("prompt")
            if last_prompt:
                lines.append("**You asked:** "
                             + redact(truncate(last_prompt, budget["prompt"])))
        lines.append("**Next:** " + suggest_next_step(ctx.get("tool_name"),
                                                      ctx.get("error")))
    elif event == "waiting":
        lines.append("**⏳ {}**".format(ctx.get("notif_title")
                                        or "Claude is waiting for you"))
        if ctx.get("message"):
            lines.append(redact(ctx["message"]))
        meta = _meta_line([("Dir", os.path.basename(cwd.rstrip("/")) or cwd),
                           ("Branch", ctx.get("branch"))])
        if meta:
            lines.append(meta)
    else:
        heading = ctx.get("title") or "Task complete"
        lines.append("**✅ {}**".format(heading))
        if ctx.get("summary"):
            label = "**Claude replied:** " if budget["prompt"] else ""
            lines.append(
                label + redact(truncate(ctx["summary"], budget["summary"])))
        meta = _meta_line([("Dir", os.path.basename(cwd.rstrip("/")) or cwd),
                           ("Branch", ctx.get("branch")),
                           ("Duration", human_duration(ctx.get("duration")))])
        if meta:
            lines.append(meta)
        if budget["tools"] and ctx.get("tools"):
            lines.append("**Tools:** " + _tools_value(ctx["tools"], budget["extras"]))
        if budget["prompt"]:
            last_prompt = ctx.get("last_prompt") or ctx.get("prompt")
            if last_prompt:
                lines.append("**You asked:** "
                             + redact(truncate(last_prompt, budget["prompt"])))
            first_prompt = ctx.get("prompt")
            if (budget["extras"] and first_prompt
                    and first_prompt != last_prompt):
                lines.append("**Session started with:** "
                             + redact(truncate(first_prompt, budget["prompt"])))
        if budget["files"] and ctx.get("files"):
            lines.append("**Files changed ({}):**".format(len(ctx["files"])))
            lines.append(_files_value(ctx["files"], budget["files"], cwd))

    if ctx.get("session_id"):
        lines.append("**Session:** " + _session_code(ctx))
    return truncate("\n".join(line for line in lines if line), LIMIT_CONTENT - 64)


def _meta_line(pairs):
    """Join non-empty (label, value) pairs into one '· '-separated line."""
    parts = ["**{}:** `{}`".format(label, value) for label, value in pairs if value]
    return "  ·  ".join(parts)


# --------------------------------------------------------------------------- #
# Destination handling                                                        #
# --------------------------------------------------------------------------- #

def is_discord_url(url):
    """True when ``url`` targets a Discord webhook (Apprise scheme or raw URL)."""
    low = (url or "").strip().lower()
    return (low.startswith(("discord://", "discord+", "discordapp://"))
            or "discord.com/api/webhooks/" in low
            or "discordapp.com/api/webhooks/" in low)


def discord_webhook_url(url):
    """Normalise a Discord destination to an https webhook URL, or None.

    Accepts both Apprise's ``discord://webhook_id/webhook_token`` form and a
    raw ``https://discord.com/api/webhooks/<id>/<token>`` URL.
    """
    url = (url or "").strip()
    low = url.lower()
    if low.startswith("https://") or low.startswith("http://"):
        return url.split("?")[0] if "discord" in low and "/webhooks/" in low else None
    if not low.startswith(("discord://", "discord+", "discordapp://")):
        return None
    rest = url.split("://", 1)[1].split("?")[0]
    segments = [seg for seg in rest.split("/") if seg]
    if not segments:
        return None
    # Apprise allows a `botname@webhook_id` prefix on the first segment.
    if "@" in segments[0]:
        segments[0] = segments[0].split("@", 1)[1]
    if len(segments) < 2:
        return None
    webhook_id, token = segments[0], segments[1]
    if not webhook_id.isdigit():
        return None
    return "https://discord.com/api/webhooks/{}/{}".format(webhook_id, token)


def collect_targets(environ):
    """Split configured NOTIFY_* destinations into Discord webhooks + others.

    Returns ``(discord_webhook_urls, apprise_urls)``.
    """
    raw = []
    for key, value in environ.items():
        if not key.startswith("NOTIFY_") or not value or not value.strip():
            continue
        if key == "NOTIFY_URLS":
            raw.extend(part.strip() for part in value.split(",") if part.strip())
        elif key in RESERVED_NOTIFY_KEYS:
            continue
        else:
            raw.append(value.strip())

    discord, apprise_urls = [], []
    for url in raw:
        if is_discord_url(url):
            hook = discord_webhook_url(url)
            if hook:
                discord.append(hook)
            else:
                apprise_urls.append(url)  # let Apprise try its own parser
        else:
            apprise_urls.append(url)
    return discord, apprise_urls


def post_discord(webhook_url, payload):
    """POST a JSON payload to a Discord webhook. Returns True on a 2xx reply."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook_url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "HolyClaude-notify/2"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        return 200 <= exc.code < 300
    except Exception:
        return False


def send_to_discord(webhook_url, embed, fallback_text):
    """Send an embed to a Discord webhook; fall back to a plain message.

    The plain-text path keeps notifications working when the embed is rejected
    (rate limiting, a malformed field, an API change).
    """
    if embed and post_discord(webhook_url,
                              {"username": "HolyClaude", "embeds": [embed]}):
        return True
    return post_discord(webhook_url,
                        {"username": "HolyClaude",
                         "content": truncate(fallback_text, LIMIT_CONTENT)})


def send_via_apprise(urls, title, body, notify_type):
    """Send an enriched Markdown message to non-Discord services via Apprise."""
    if not urls:
        return
    try:
        import apprise
        ap = apprise.Apprise()
        for url in urls:
            ap.add(url)
        ap.notify(title=title, body=body,
                  body_format=apprise.NotifyFormat.MARKDOWN,
                  notify_type=notify_type)
    except Exception:
        # Best-effort: a failed Apprise send must never break the parent tool.
        return


# --------------------------------------------------------------------------- #
# Entry point                                                                 #
# --------------------------------------------------------------------------- #

def get_verbosity(environ):
    value = (environ.get("HOLYCLAUDE_NOTIFY_VERBOSITY") or "standard").strip().lower()
    return value if value in BUDGETS else "standard"


def get_style(environ):
    value = (environ.get("HOLYCLAUDE_NOTIFY_STYLE") or "embed").strip().lower()
    return value if value in ("embed", "simple") else "embed"


def main():
    # Gate 1: notifications must be explicitly enabled via the flag file.
    if not os.path.isfile(FLAG_FILE):
        sys.exit(0)

    discord_urls, apprise_urls = collect_targets(os.environ)
    if not discord_urls and not apprise_urls:
        sys.exit(0)

    # Scrub the configured webhook tokens from every rendered string.
    for url in discord_urls + apprise_urls:
        for chunk in re.split(r"[/@:?&=]", url):
            if len(chunk) >= 12:
                _EXTRA_SECRETS.append(chunk)

    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    verbosity = get_verbosity(os.environ)
    style = get_style(os.environ)

    try:
        hook = read_hook_input()
        ctx = build_context(event, hook)
        title = build_title(event, ctx)
        body = build_text(event, ctx, verbosity)
        notify_type = {"stop": "success", "error": "failure",
                       "waiting": "warning"}.get(event, "info")

        # Discord webhooks: native rich embed (unless simple style is forced).
        embed = build_embed(event, ctx, verbosity) if style == "embed" else None
        for webhook_url in discord_urls:
            send_to_discord(webhook_url, embed, body)

        # Every other service: enriched Markdown via Apprise.
        send_via_apprise(apprise_urls, title, body, notify_type)
    except Exception:
        # Last-resort fallback to the original v1 plain message so a notify is
        # still attempted even if enrichment hit an unexpected error.
        try:
            ltitle, lbody, ltype = LEGACY_EVENTS.get(
                event, ("HolyClaude — Notification", "Event: {}".format(event), "info"))
            send_via_apprise(apprise_urls, ltitle, lbody, ltype)
            for webhook_url in discord_urls:
                post_discord(webhook_url,
                             {"username": "HolyClaude",
                              "content": "{}\n{}".format(ltitle, lbody)})
        except Exception:
            # Even the legacy fallback failed — stay silent, never propagate.
            return

    sys.exit(0)


if __name__ == "__main__":
    main()
