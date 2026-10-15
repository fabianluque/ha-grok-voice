"""In-memory status for the panel: live sessions and recent ones with latency."""

from __future__ import annotations

import collections
import itertools
import time

from app.ks_relay.stats import add_usage
from app.ks_relay.timeline import append_event


class RelayStatus:
    def __init__(self, keep: int = 50) -> None:
        self.live: dict[int, dict] = {}
        self.recent: collections.deque = collections.deque(maxlen=keep)
        self._ids = itertools.count(1)

    def open(self, remote: str) -> int:
        ident = next(self._ids)
        self.live[ident] = {
            "id": ident, "tablet": "?", "device_id": "", "remote": remote, "started": time.time(),
            "ready_ms": None, "turns": [], "tools": [], "usage": {}, "timeline": [], "end_reason": "",
        }
        return ident

    def update(self, ident: int, **fields) -> None:
        if ident in self.live:
            self.live[ident].update(fields)

    def mark(self, ident: int, event: dict, *, force: bool = False) -> dict | None:
        row = self.live.get(ident)
        if not row:
            return None
        events = row.setdefault("timeline", [])
        overflow = row.setdefault("_tl_overflow", [])
        return append_event(events, event, force=force, overflow_flag=overflow)

    def turn(self, ident: int, latency_ms: int) -> None:
        if ident in self.live:
            self.live[ident]["turns"].append(latency_ms)

    def tool(self, ident: int, name: str, ok: bool, ms: int, *, cached: bool = False) -> None:
        if ident in self.live:
            self.live[ident]["tools"].append({"name": name, "ok": ok, "ms": ms, **({"cached": True} if cached else {})})

    def usage(self, ident: int, usage: object) -> None:
        """Add an xAI response.done usage object to the session's totals."""
        if ident in self.live:
            add_usage(self.live[ident]["usage"], usage)

    def close(self, ident: int, error: str = "", end_reason: str = "") -> dict | None:
        entry = self.live.pop(ident, None)
        if entry:
            entry["ended"] = time.time()
            entry["error"] = error
            entry["end_reason"] = end_reason or entry.get("end_reason") or ("error" if error else "")
            entry.pop("_tl_overflow", None)
            self.recent.appendleft(entry)
        return entry

    def snapshot(self) -> dict:
        return {"live": list(self.live.values()), "recent": list(self.recent)}
