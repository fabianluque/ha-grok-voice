"""Bounded, redacted session timelines for the relay panel.

Events are stored on the in-memory live session and persisted in stats.db.
Payloads are truncated and secret-like keys/values are stripped so the
detail view can show arguments and results without keeping keys or huge
skill payloads.
"""

from __future__ import annotations

import json
import re

MAX_EVENTS = 300
MAX_PAYLOAD = 2000
MAX_TRANSCRIPT = 500
MAX_UNKNOWN_UPSTREAM = 12
READONLY_SKILL_ACTIONS = frozenset({"help", "info", "usage", "?", "catalog", "get", "list"})
HELP_ACTIONS = frozenset({"help", "info", "usage", "?"})
# Event types the relay already handles or that are expected noise (not timeline-worthy).
KNOWN_UPSTREAM = frozenset({
    "error",
    "ping",
    "session.created",
    "session.updated",
    "rate_limits.updated",
    "input_audio_buffer.speech_started",
    "input_audio_buffer.speech_stopped",
    "input_audio_buffer.committed",
    "input_audio_buffer.cleared",
    "input_audio_buffer.append",
    "conversation.created",
    "conversation.item.created",
    "conversation.item.added",
    "conversation.item.done",
    "conversation.item.truncated",
    "conversation.item.deleted",
    "conversation.item.input_audio_transcription.completed",
    "conversation.item.input_audio_transcription.failed",
    "conversation.item.input_audio_transcription.updated",
    "response.created",
    "response.done",
    "response.output_item.added",
    "response.output_item.done",
    "response.content_part.added",
    "response.content_part.done",
    "response.output_audio.done",
    "response.audio.done",
    "response.output_audio_transcript.done",
    "response.audio_transcript.done",
    "response.text.done",
    "response.output_text.done",
    "response.function_call_arguments.done",
})

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|secret|password|passwd|"
    r"authorization|bearer|cookie|credential|private[_-]?key)",
    re.I,
)


def b64_pcm16_seconds(delta: str, rate: int = 24000) -> float:
    """Duration of a base64 PCM16 mono frame; uses length only (no decode)."""
    if not delta or rate <= 0:
        return 0.0
    pad = len(delta) - len(delta.rstrip("="))
    nbytes = (len(delta) * 3) // 4 - pad
    if nbytes <= 0:
        return 0.0
    return nbytes / 2 / float(rate)


def pcm_rate(session: dict | None, default: int = 24000) -> int:
    audio = (session or {}).get("audio") or {}
    for side in ("output", "input"):
        fmt = (audio.get(side) or {}).get("format") or {}
        rate = fmt.get("rate")
        if isinstance(rate, (int, float)) and rate:
            return int(rate)
    return default


def _redact_text(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[redacted]")
    return out


def sanitize(value, secrets: list[str] | tuple[str, ...] = (), max_chars: int = MAX_PAYLOAD, _depth: int = 0):
    """Drop secret-like keys, replace known secrets, and bound string size."""
    if _depth > 12:
        return "[…]"
    secrets = [s for s in secrets if s]
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:80]:
            name = str(key)
            if _SECRET_KEY_RE.search(name):
                out[name] = "[redacted]"
            else:
                out[name] = sanitize(item, secrets, max_chars, _depth + 1)
        return out
    if isinstance(value, list):
        return [sanitize(item, secrets, max_chars, _depth + 1) for item in value[:80]]
    if isinstance(value, str):
        text = _redact_text(value, secrets)
        if len(text) > max_chars:
            return text[:max_chars] + f"…[+{len(text) - max_chars} chars]"
        return text
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize(str(value), secrets, max_chars, _depth)


def compact_response_output(output: object) -> list[dict]:
    """[{type, name, status}] from a response.done output list."""
    rows: list[dict] = []
    if not isinstance(output, list):
        return rows
    for item in output[:40]:
        if not isinstance(item, dict):
            continue
        row = {}
        for key in ("type", "name", "status"):
            value = item.get(key)
            if value not in (None, ""):
                row[key] = value
        if row:
            rows.append(row)
    return rows


def dumps_payload(value, secrets: list[str] | tuple[str, ...] = (), max_chars: int = MAX_PAYLOAD) -> str:
    clean = sanitize(value, secrets, max_chars)
    if isinstance(clean, str):
        text = clean
    else:
        text = json.dumps(clean, ensure_ascii=False, default=str)
    if len(text) > max_chars:
        return text[:max_chars] + f"…[+{len(text) - max_chars} chars]"
    return text


def append_event(events: list, event: dict, *, force: bool = False, overflow_flag: list | None = None) -> dict | None:
    """Append one event, capping at MAX_EVENTS unless ``force`` (session_end)."""
    if len(events) >= MAX_EVENTS and not force:
        if overflow_flag is not None and not overflow_flag:
            events.append({"t": event.get("t", 0), "kind": "omitted", "detail": "further events dropped"})
            overflow_flag.append(True)
        return None
    events.append(event)
    return event
