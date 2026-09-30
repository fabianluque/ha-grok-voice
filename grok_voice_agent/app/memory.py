"""Short per-device conversation memory, Assist-style.

A new wake on the same satellite reuses recent turns for follow-ups
("that one", "and the kitchen", "do I have a meeting today?"). Entries
expire after a short TTL (default eight minutes) and are dropped on a
goodbye hang-up. Idle hang-up keeps the turns.

Keyed by the kiosk's stable device id (not area). Attic vs dining stay
independent even when the first wake still has the Attic area fallback.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass

DEFAULT_TTL_SECONDS = 480
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 3600
MAX_TURNS = 16


def clamp_memory_ttl(value: object, default: int = DEFAULT_TTL_SECONDS) -> int:
    try:
        seconds = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, seconds))


def _slug(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).lower()
    folded = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")
    return folded[:80]


@dataclass(frozen=True)
class Turn:
    role: str
    text: str


@dataclass
class MemoryEntry:
    turns: list[Turn]
    updated_at: float
    key: str


def conversation_key(device: dict[str, str] | None, area: dict[str, str] | None) -> str:
    """Same tablet across wakes: stable device id, then name, then area.

    Area is only a fallback when the client sent no device. Mixing device+area
    in one key dropped history when the first wake used the Attic fallback and
    a later wake had the real dining-room area.
    """
    if device:
        ident = (device.get("id") or device.get("name") or "").strip()
        if ident:
            return f"device:{_slug(ident)}"
    if area:
        ident = (area.get("id") or area.get("name") or "").strip()
        if ident:
            return f"area:{_slug(ident)}"
    return "default"


def _history_lines(turns: list[Turn] | None) -> list[str]:
    lines: list[str] = []
    for turn in (turns or [])[-MAX_TURNS:]:
        speaker = "User" if turn.role == "user" else "Assistant"
        text = " ".join(str(turn.text or "").split())
        if not text:
            continue
        if len(text) > 400:
            text = f"{text[:397]}..."
        lines.append(f"{speaker}: {text}")
    return lines


def with_history_instructions(base: str, turns: list[Turn] | None) -> str:
    lines = _history_lines(turns)
    if not lines:
        return base
    extra = (
        "Recent conversation on this device (continue it; do not recap unless asked). "
        "Facts the user already told you here are true even if calendar or other tools "
        "do not list them. Do not say those events are missing.\n"
        + "\n".join(lines)
    )
    root = (base or "").rstrip()
    return f"{root}\n\n{extra}" if root else extra


def history_conversation_events(turns: list[Turn] | None) -> list[dict]:
    """Realtime items so the new session actually contains the prior turns.

    Instruction text alone is easy for the model to ignore once a calendar
    tool returns empty. Do not follow these with ``response.create``.
    """
    events: list[dict] = []
    for turn in (turns or [])[-MAX_TURNS:]:
        text = " ".join(str(turn.text or "").split())
        if not text or turn.role not in ("user", "assistant"):
            continue
        content_type = "input_text" if turn.role == "user" else "text"
        events.append(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": turn.role,
                    "content": [{"type": content_type, "text": text}],
                },
            }
        )
    return events


class SessionTranscript:
    """Final user/assistant transcripts for one duplex session."""

    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self._pending: Turn | None = None

    def add(self, role: object, text: object, final: bool = True) -> None:
        if role not in ("user", "assistant"):
            return
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return
        if not final:
            self._pending = Turn(role=str(role), text=cleaned)
            return
        self._pending = None
        if self.turns and self.turns[-1].role == role and self.turns[-1].text == cleaned:
            return
        self.turns.append(Turn(role=str(role), text=cleaned))
        if len(self.turns) > MAX_TURNS:
            self.turns = self.turns[-MAX_TURNS:]

    def flush_pending(self, role: str | None = None) -> None:
        """Commit the last partial transcript when VAD ends the turn.

        xAI sometimes only sends cumulative ``*.updated`` snapshots and never
        a ``*.completed`` event. Without this, user-stated facts never land
        in memory.
        """
        pending = self._pending
        if pending is None:
            return
        if role and pending.role != role:
            return
        self._pending = None
        self.add(pending.role, pending.text, final=True)


class ConversationMemory:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, clock=time.monotonic) -> None:
        self.ttl_seconds = clamp_memory_ttl(ttl_seconds)
        self._clock = clock
        self._store: dict[str, MemoryEntry] = {}

    def get(self, key: str, now: float | None = None) -> list[Turn]:
        entry = self._store.get(key)
        if entry is None:
            return []
        current = self._clock() if now is None else now
        if current - entry.updated_at >= self.ttl_seconds:
            self._store.pop(key, None)
            return []
        return list(entry.turns)

    def remember(self, key: str, turns: list[Turn], now: float | None = None) -> None:
        if not key or not turns:
            return
        current = self._clock() if now is None else now
        existing = self.get(key, now=current)
        merged = _merge_turns(existing, turns)[-MAX_TURNS:]
        self._store[key] = MemoryEntry(turns=merged, updated_at=current, key=key)

    def forget(self, key: str) -> None:
        self._store.pop(key, None)


def _merge_turns(existing: list[Turn], new: list[Turn]) -> list[Turn]:
    if not existing:
        return list(new)
    if not new:
        return list(existing)
    overlap = 0
    max_overlap = min(len(existing), len(new))
    for size in range(max_overlap, 0, -1):
        if existing[-size:] == new[:size]:
            overlap = size
            break
    return existing + new[overlap:]
