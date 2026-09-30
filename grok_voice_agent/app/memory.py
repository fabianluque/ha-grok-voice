"""Short per-device conversation memory, Assist-style.

A new wake on the same satellite reuses recent turns for follow-ups
("that one", "and the kitchen"). Entries expire after a short TTL
(default eight minutes) and are dropped on a goodbye hang-up.
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
    """Attic tablet vs other satellites: device id/name, then area."""
    parts: list[str] = []
    if device:
        ident = (device.get("id") or device.get("name") or "").strip()
        if ident:
            parts.append(f"device:{_slug(ident)}")
    if area:
        ident = (area.get("id") or area.get("name") or "").strip()
        if ident:
            parts.append(f"area:{_slug(ident)}")
    return "|".join(parts) or "default"


def with_history_instructions(base: str, turns: list[Turn] | None) -> str:
    if not turns:
        return base
    lines = ["Recent conversation on this device (continue it; do not recap unless asked):"]
    for turn in turns[-MAX_TURNS:]:
        speaker = "User" if turn.role == "user" else "Assistant"
        text = " ".join(str(turn.text or "").split())
        if not text:
            continue
        if len(text) > 400:
            text = f"{text[:397]}..."
        lines.append(f"{speaker}: {text}")
    if len(lines) == 1:
        return base
    extra = "\n".join(lines)
    root = (base or "").rstrip()
    return f"{root}\n\n{extra}" if root else extra


class SessionTranscript:
    """Final user/assistant transcripts for one duplex session."""

    def __init__(self) -> None:
        self.turns: list[Turn] = []

    def add(self, role: object, text: object, final: bool = True) -> None:
        if not final:
            return
        if role not in ("user", "assistant"):
            return
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return
        if self.turns and self.turns[-1].role == role and self.turns[-1].text == cleaned:
            return
        self.turns.append(Turn(role=str(role), text=cleaned))
        if len(self.turns) > MAX_TURNS:
            self.turns = self.turns[-MAX_TURNS:]


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
