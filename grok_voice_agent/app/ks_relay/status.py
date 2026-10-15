"""In-memory status for the panel: live sessions and recent ones with latency."""

from __future__ import annotations

import collections
import itertools
import time

from app.ks_relay.stats import add_usage


class RelayStatus:
    def __init__(self, keep: int = 50) -> None:
        self.live: dict[int, dict] = {}
        self.recent: collections.deque = collections.deque(maxlen=keep)
        self._ids = itertools.count(1)

    def open(self, remote: str) -> int:
        ident = next(self._ids)
        self.live[ident] = {"id": ident, "tablet": "?", "device_id": "", "remote": remote, "started": time.time(),
                            "ready_ms": None, "turns": [], "tools": [], "usage": {}}
        return ident

    def update(self, ident: int, **fields) -> None:
        if ident in self.live:
            self.live[ident].update(fields)

    def turn(self, ident: int, latency_ms: int) -> None:
        if ident in self.live:
            self.live[ident]["turns"].append(latency_ms)

    def tool(self, ident: int, name: str, ok: bool, ms: int) -> None:
        if ident in self.live:
            self.live[ident]["tools"].append({"name": name, "ok": ok, "ms": ms})

    def usage(self, ident: int, usage: object) -> None:
        """Add an xAI response.done usage object to the session's totals."""
        if ident in self.live:
            add_usage(self.live[ident]["usage"], usage)

    def close(self, ident: int, error: str = "") -> dict | None:
        entry = self.live.pop(ident, None)
        if entry:
            entry["ended"] = time.time()
            entry["error"] = error
            self.recent.appendleft(entry)
        return entry

    def snapshot(self) -> dict:
        return {"live": list(self.live.values()), "recent": list(self.recent)}
