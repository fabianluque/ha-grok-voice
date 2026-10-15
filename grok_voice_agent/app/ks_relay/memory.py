"""Short-term memory for the KS relay.

Each session records what the user said, what Grok said and the relay's tool
calls (name and ok/failed only). After the session ends (off the hot path) a
fast xAI text model turns it into 2-3 lines; the result is stored per tablet
in /data. The next session gets a compact "Recent context" block in its
instructions. Empty summaries are not stored. If summarizing fails, the
trimmed exchanges are stored instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("grok_voice.ks_relay.memory")

XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
KS_HISTORY_RE = re.compile(
    r"\n*Earlier conversations with the user, oldest first\. Use them when the user refers back to something:\n.*?(?=\n\n|\Z)",
    re.DOTALL,
)
SUMMARY_PROMPT = (
    "Summarize this voice conversation with a smart-home assistant for the assistant's own short-term "
    "memory, in at most 3 short lines: open threads or pending requests, decisions and actions taken, and "
    "names, numbers, places or times mentioned. Leave out anything that did not come up; never write lines "
    "like 'no pending requests'. No preamble. If nothing is worth remembering, reply with exactly: NONE"
)


@dataclass(frozen=True)
class MemorySettings:
    enabled: bool = True
    mode: str = "summary"  # summary | raw | off
    window_minutes: int = 45
    max_items: int = 5
    scope: str = "per_tablet"  # per_tablet | house
    house_window_minutes: int = 10  # per_tablet: other rooms' items this recent are added too (0 = never)
    summary_model: str = "grok-4.20-0309-non-reasoning"
    summary_max_chars: int = 400
    strip_ks_history: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "MemorySettings":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (data or {}).items() if k in fields})

    @property
    def active(self) -> bool:
        return self.enabled and self.mode != "off"


@dataclass
class SessionLog:
    tablet: str
    area: str = ""
    started: float = field(default_factory=time.time)
    lines: list[tuple[str, str]] = field(default_factory=list)  # (role, text)
    facts: list[str] = field(default_factory=list)  # compact tool lines: "turn_on ok"

    def said(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if text:
            self.lines.append((role, text))

    def tool(self, name: str, arguments: str, output: str, ok: bool) -> None:
        line = compact_tool_line(name, arguments, ok)
        self.facts.append(line)
        self.lines.append(("tool", line))

    @property
    def empty(self) -> bool:
        return not any(role == "user" for role, _ in self.lines)

    def transcript(self, max_chars: int = 6000) -> str:
        names = {"user": "User", "assistant": "Assistant", "tool": "Tool"}
        text = "\n".join(f"{names[r]}: {t}" for r, t in self.lines)
        return text[-max_chars:]

    def raw(self, max_chars: int) -> str:
        pairs = [f"{'User' if r == 'user' else 'You'}: {t[:120]}" for r, t in self.lines if r in ("user", "assistant")]
        out = " / ".join(pairs[-6:])
        return out[-max_chars:]


def strip_ks_history(instructions: str) -> str:
    return KS_HISTORY_RE.sub("", instructions or "").rstrip()


_FACT_ARROW_RE = re.compile(r"^(?P<name>\S+)(?:\s+(?P<rest>.*?))?\s*->\s*(?P<status>ok|failed)\b", re.DOTALL)
_SKILL_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]+)"')
_SKILL_ACTION_RE = re.compile(r'"action"\s*:\s*"([^"]+)"')


def _skill_name_action(arguments: str) -> tuple[str, str]:
    raw = arguments or ""
    try:
        args = json.loads(raw) if raw.strip() else {}
        if isinstance(args, dict):
            return str(args.get("name") or "").strip(), str(args.get("action") or "").strip()
    except (TypeError, json.JSONDecodeError):
        pass
    name = _SKILL_NAME_RE.search(raw)
    action = _SKILL_ACTION_RE.search(raw)
    return (name.group(1) if name else ""), (action.group(1) if action else "")


def tool_label(name: str, arguments: str = "") -> str:
    """``turn_on`` or ``skill dashboard.set`` (skill gateway includes action)."""
    name = (name or "").strip() or "?"
    if name != "skill":
        return name
    skill, action = _skill_name_action(arguments)
    if skill and action:
        return f"skill {skill}.{action}"
    if skill:
        return f"skill {skill}"
    return "skill"


def compact_tool_line(name: str, arguments: str = "", ok: bool = True) -> str:
    return f"{tool_label(name, arguments)} {'ok' if ok else 'failed'}"


def compact_stored_fact(fact: str) -> str:
    """Trim a stored tool line (old rows included args/results) to name + status."""
    fact = (fact or "").strip()
    if not fact:
        return ""
    match = _FACT_ARROW_RE.match(fact)
    if match:
        name = match.group("name")
        rest = (match.group("rest") or "").strip()
        status = match.group("status")
        if name == "skill":
            return compact_tool_line("skill", rest, status == "ok")
        return f"{name} {status}"
    parts = fact.split()
    if len(parts) >= 2 and parts[-1] in ("ok", "failed"):
        return fact
    return parts[0]


def compact_memory_item(item: dict) -> dict | None:
    """Drop empty summaries and compact tool facts. None = skip this row."""
    text = str(item.get("text") or "").strip()
    if not text:
        return None
    facts = [compact_stored_fact(f) for f in item.get("facts") or []]
    return {**item, "text": text, "facts": [f for f in facts if f]}


class RelayMemory:
    def __init__(self, path: str | Path, xai_api_key: str, *, http: httpx.AsyncClient | None = None) -> None:
        self.path = Path(path)
        self._key = xai_api_key
        self._http = http
        self._items: list[dict] = []
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.on_usage = None  # (tablet, model, input_tokens, output_tokens) -> None, set by the relay for cost stats
        self._load()

    # storage ------------------------------------------------------------------
    def _load(self) -> None:
        path = self.path
        with contextlib.suppress(FileNotFoundError, ValueError, OSError):
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._items = [i for i in data if isinstance(i, dict) and "ts" in i]

    def _save(self) -> None:
        path = self.path
        horizon = time.time() - 2 * 24 * 3600
        self._items = [i for i in self._items if i["ts"] >= horizon][-400:]
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._items, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)

    # session end ---------------------------------------------------------------
    def finish(self, session: SessionLog, settings: MemorySettings) -> None:
        """Store a finished session in the background."""
        if not settings.active or session.empty:
            return
        task = asyncio.create_task(self._store(session, settings))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def complete(self, session: SessionLog, settings: MemorySettings) -> dict | None:
        """Awaitable store; returns a memory_summary timeline event, or None."""
        if not settings.active or session.empty:
            return None
        return await self._store(session, settings)

    async def _store(self, session: SessionLog, settings: MemorySettings) -> dict | None:
        text, kind, meta = "", "raw", {}
        summarized = False
        if settings.mode == "summary":
            text, meta, ok = await self._summarize(session, settings)
            if ok:
                summarized = True
                kind = "summary"
        if summarized:
            text = "\n".join(
                line.strip() for line in text.splitlines() if line.strip() and line.strip().upper() != "NONE"
            )
            if not text:
                return None
        if kind == "raw":
            text = session.raw(settings.summary_max_chars)
        if not text:
            return None
        item = {
            "ts": time.time(),
            "started": session.started,
            "tablet": session.tablet,
            "area": session.area,
            "kind": kind,
            "text": text[: settings.summary_max_chars],
            "facts": session.facts[-8:],
        }
        async with self._lock:
            self._items.append(item)
            await asyncio.to_thread(self._save)
        log.info("memory stored for %s (%s, %d chars, %d tool facts)", session.tablet, kind, len(item["text"]), len(item["facts"]))
        return {
            "kind": "memory_summary",
            "model": meta.get("model") or settings.summary_model,
            "summary_kind": kind,
            "ms": meta.get("ms"),
            "input_tokens": meta.get("input_tokens"),
            "output_tokens": meta.get("output_tokens"),
            "chars": len(item["text"]),
        }

    async def _summarize(self, session: SessionLog, settings: MemorySettings) -> tuple[str, dict, bool]:
        """-> (text, usage meta, ok). ok is False when the call failed (raw fallback)."""
        if not self._key:
            return "", {}, False
        body = {
            "model": settings.summary_model,
            "messages": [
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": session.transcript()},
            ],
            "max_tokens": 200,
            "temperature": 0.2,
        }
        own = self._http is None
        http = self._http or httpx.AsyncClient(timeout=20)
        try:
            started = time.monotonic()
            response = await http.post(XAI_CHAT_URL, json=body, headers={"Authorization": f"Bearer {self._key}"})
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage") or {}
            inp = int(usage.get("prompt_tokens") or 0)
            out = int(usage.get("completion_tokens") or 0)
            if self.on_usage is not None:
                try:
                    self.on_usage(session.tablet, settings.summary_model, inp, out)
                except Exception as exc:  # noqa: BLE001 - stats must never break memory
                    log.warning("summary usage not recorded: %s", exc)
            ms = int((time.monotonic() - started) * 1000)
            log.info("memory summary %s %dms", settings.summary_model, ms)
            return text, {"ms": ms, "input_tokens": inp, "output_tokens": out, "model": settings.summary_model}, True
        except Exception as exc:  # noqa: BLE001 - raw fallback
            log.warning("memory summary failed (%s); keeping raw exchanges", type(exc).__name__)
            return "", {}, False
        finally:
            if own:
                await http.aclose()

    # next session ----------------------------------------------------------------
    def context_block(self, tablet: str, settings: MemorySettings, time_zone: str = "UTC", *, now: float | None = None) -> str:
        if not settings.active:
            return ""
        now = now or time.time()
        window = settings.window_minutes * 60
        house = settings.house_window_minutes * 60
        chosen = []
        for item in self._items:
            age = now - item["ts"]
            if age < 0:
                continue
            here = item.get("tablet") == tablet
            if settings.scope == "house" and age <= window:
                chosen.append(item)
            elif here and age <= window:
                chosen.append(item)
            elif not here and house and age <= house:
                chosen.append(item)
        chosen = chosen[-settings.max_items :]
        compact = [c for item in chosen if (c := compact_memory_item(item))]
        if not compact:
            return ""
        zone = ZoneInfo(time_zone or "UTC")
        lines = ["Recent context (earlier conversations; use it only if the user refers back):"]
        for item in compact:
            moment = datetime.fromtimestamp(item["started"], zone)
            when = f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
            minutes = int((now - item["ts"]) // 60)
            where = "" if item.get("tablet") == tablet else f", {item.get('area') or item.get('tablet')}"
            head = f"- {when} ({minutes} min ago{where}): {item.get('text') or ''}".rstrip(": ")
            lines.append(head)
            for fact in item.get("facts") or []:
                lines.append(f"  tool: {fact}")
        return "\n".join(lines)

    def items(self) -> list[dict]:
        return [c for item in self._items if (c := compact_memory_item(item))]

    async def clear(self, tablet: str | None = None) -> int:
        async with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if tablet is not None and i.get("tablet") != tablet]
            await asyncio.to_thread(self._save)
            return before - len(self._items)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
