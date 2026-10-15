"""Short-term memory for the KS relay.

Each session records what the user said, what Grok said and the relay's tool
calls. After the session ends (off the hot path) a fast xAI text model turns it
into 2-3 lines; the result is stored per tablet in /data. The next session gets a
compact "Recent context" block in its instructions. If summarizing fails, the
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
    facts: list[str] = field(default_factory=list)  # tool calls, verbatim

    def said(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if text:
            self.lines.append((role, text))

    def tool(self, name: str, arguments: str, output: str, ok: bool) -> None:
        try:
            result = json.loads(output)
        except (TypeError, json.JSONDecodeError):
            result = {}
        detail = ""
        if isinstance(result, dict):
            detail = str(result.get("say") or result.get("error") or "")[:80]
        args = re.sub(r"\s+", " ", arguments or "{}")[:160]
        self.facts.append(f"{name} {args} -> {'ok' if ok else 'failed'}{': ' + detail if detail else ''}")
        self.lines.append(("tool", self.facts[-1]))

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

    async def _store(self, session: SessionLog, settings: MemorySettings) -> None:
        text, kind = "", "raw"
        if settings.mode == "summary":
            with contextlib.suppress(Exception):
                text = await self._summarize(session, settings)
                kind = "summary"
            if not text:
                kind = "raw"
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip() and line.strip().upper() != "NONE")
        if kind == "raw":
            text = session.raw(settings.summary_max_chars)
        if not text and not session.facts:
            return
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

    async def _summarize(self, session: SessionLog, settings: MemorySettings) -> str:
        if not self._key:
            return ""
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
            if self.on_usage is not None:
                usage = data.get("usage") or {}
                try:
                    self.on_usage(session.tablet, settings.summary_model, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))
                except Exception as exc:  # noqa: BLE001 - stats must never break memory
                    log.warning("summary usage not recorded: %s", exc)
            log.info("memory summary %s %dms", settings.summary_model, (time.monotonic() - started) * 1000)
            return text
        except Exception as exc:  # noqa: BLE001 - raw fallback
            log.warning("memory summary failed (%s); keeping raw exchanges", type(exc).__name__)
            return ""
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
        if not chosen:
            return ""
        zone = ZoneInfo(time_zone or "UTC")
        lines = ["Recent context (earlier conversations; use it only if the user refers back):"]
        for item in chosen:
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
        return [dict(i) for i in self._items]

    async def clear(self, tablet: str | None = None) -> int:
        async with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if tablet is not None and i.get("tablet") != tablet]
            await asyncio.to_thread(self._save)
            return before - len(self._items)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
