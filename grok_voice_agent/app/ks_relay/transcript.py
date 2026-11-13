"""Assistant transcript hygiene for what the relay forwards to the tablet.

Grok voice sometimes speaks or writes internal markup after its answer
(``Goodbye! <turn_end> <system_warning>…</system_warning>``) or says the same
confirmation twice in one reply. ``TranscriptFilter`` cleans the streamed text
and reports the point where a reply should be cut.

Only ``<system…>`` blocks are leaks. End-of-turn markers (``<turn_end>``,
``<end_of_turn>``) and any other tag are stripped silently: xAI often ends a
normal reply's streamed text with ``<turn_end>``.
"""

from __future__ import annotations

import re

_TAG_RE = re.compile(r"</?[A-Za-z_][\w:.-]*(?:\s[^<>]*)?/?>")
# Blocks whose content is internal too, not just the tags around it.
_BLOCK_RE = re.compile(r"<(system[\w-]*)\b[^<>]*>.*?</\1\s*>", re.S | re.I)
_OPEN_BLOCK_RE = re.compile(r"<system[\w-]*\b[^<>]*>", re.I)
_LEAK_RE = re.compile(r"<\s*/?\s*system[\w-]*\b[^<>]*>", re.I)
_PARTIAL_TAG_RE = re.compile(r"<[^<>]*$")
_SENTENCE_RE = re.compile(r"[^.!?]*[.!?]+")


def clean_transcript(text: str, *, partial: bool = False) -> str:
    """Text without internal blocks and tags.

    ``partial`` is for a stream still growing: an unclosed ``<system…>`` block
    or a trailing ``<…`` that may become a tag is held back.
    """
    out = _BLOCK_RE.sub(" ", text or "")
    if partial:
        opened = _OPEN_BLOCK_RE.search(out)
        if opened:
            out = out[: opened.start()]
        out = _PARTIAL_TAG_RE.sub("", out)
    out = _TAG_RE.sub(" ", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def first_leak(text: str) -> int | None:
    """Index of the first complete ``<system…>`` / ``</system…>`` tag, or None."""
    match = _LEAK_RE.search(text or "")
    return match.start() if match else None


def _key(sentence: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sentence.lower()).split())


def first_repeat(text: str) -> int | None:
    """Index where a complete sentence said earlier in the same reply starts again."""
    seen: set[str] = set()
    for match in _SENTENCE_RE.finditer(text or ""):
        key = _key(match.group(0))
        if not key:
            continue
        if key in seen:
            return match.start()
        seen.add(key)
    return None


def _hold_possible_repeat(text: str) -> str:
    """Hold back an unfinished last sentence while it could still be a repeat of an earlier one."""
    sentences = list(_SENTENCE_RE.finditer(text))
    end = sentences[-1].end() if sentences else 0
    tail = _key(text[end:])
    if tail and any(_key(m.group(0)).startswith(tail) for m in sentences):
        return text[:end].rstrip()
    return text


def _clip(raw: str, *, partial: bool) -> tuple[str, str]:
    """(clean text up to any leak or repeat, reason) for raw transcript text."""
    reason = ""
    leak = first_leak(raw)
    if leak is not None:
        raw, reason = raw[:leak], "leak"
    text = clean_transcript(raw, partial=partial and not reason)
    repeat = first_repeat(text)
    if repeat is not None:
        text, reason = text[:repeat].rstrip(), reason or "repeat"
    elif partial and not reason:
        text = _hold_possible_repeat(text)
    return text, reason


class TranscriptFilter:
    """One reply's transcript stream: feed deltas, forward only clean new text."""

    def __init__(self) -> None:
        self.raw = ""
        self.sent = ""
        self.reason = ""

    @property
    def cut(self) -> bool:
        return bool(self.reason)

    def feed(self, delta: str) -> tuple[str, bool]:
        """(text to forward, True when the reply must be cut here)."""
        if self.cut:
            return "", False
        self.raw += delta or ""
        text, reason = _clip(self.raw, partial=True)
        forward = text[len(self.sent):] if text.startswith(self.sent) else ""
        self.sent += forward
        if reason:
            self.reason = reason
            return forward, True
        return forward, False

    def final(self, transcript: str) -> str:
        """The clean full transcript for a done event (what was forwarded, once cut)."""
        if self.cut:
            return self.sent
        text, reason = _clip(transcript or self.raw, partial=False)
        if reason:
            self.reason = reason
        return text
