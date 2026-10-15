"""Session stats and cost estimates, persisted in SQLite (/data/stats.db).

Costs are estimates: realtime is priced per minute of session (connection) time,
or of xAI's reported ``billable_audio_seconds`` (settings: costs.realtime_basis),
and summaries per token, at the prices set in the panel.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.ks_relay.timeline import MAX_EVENTS

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tablet TEXT, device_id TEXT, started REAL, ended REAL, duration_s REAL,
  ready_ms INTEGER, turns INTEGER, latencies TEXT, tools TEXT, usage TEXT,
  realtime_cost REAL, error TEXT, timeline TEXT, end_reason TEXT
);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started);
CREATE TABLE IF NOT EXISTS summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, tablet TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost REAL
);
CREATE INDEX IF NOT EXISTS summaries_ts ON summaries(ts);
"""


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (p50 = median of odd lists)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))
    return ordered[int(rank) - 1]


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def add_usage(total: dict, usage: object) -> dict:
    """Sum numeric fields of xAI usage objects, flattening *_details ({'input_token_details': {'audio_tokens': 3}} -> 'input_audio_tokens')."""
    if not isinstance(usage, dict):
        return total
    for key, value in usage.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value
        elif isinstance(value, dict) and key.endswith("_details"):
            prefix = key.split("_", 1)[0]
            for sub, number in value.items():
                if isinstance(number, (int, float)) and not isinstance(number, bool):
                    name = sub if sub.startswith(prefix) else f"{prefix}_{sub}"
                    total[name] = total.get(name, 0) + number
    return total


class StatsStore:
    def __init__(self, path: str | Path = "/data/stats.db") -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {row[1] for row in self._db.execute("PRAGMA table_info(sessions)")}
        if "timeline" not in cols:
            self._db.execute("ALTER TABLE sessions ADD COLUMN timeline TEXT")
        if "end_reason" not in cols:
            self._db.execute("ALTER TABLE sessions ADD COLUMN end_reason TEXT")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # write ------------------------------------------------------------------
    def record_session(self, entry: dict, costs: dict) -> dict:
        started = float(entry.get("started") or time.time())
        ended = float(entry.get("ended") or time.time())
        duration = max(0.0, ended - started)
        billable = duration
        if costs.get("realtime_basis") == "billable_audio" and (entry.get("usage") or {}).get("billable_audio_seconds") is not None:
            billable = float(entry["usage"]["billable_audio_seconds"])
        realtime = round(billable / 60 * float(costs.get("realtime_per_min", 0.08)), 6)
        timeline = list(entry.get("timeline") or [])
        if len(timeline) > MAX_EVENTS:
            ending = [e for e in timeline if isinstance(e, dict) and e.get("kind") == "session_end"]
            keep = MAX_EVENTS - (1 if ending else 0)
            timeline = timeline[: max(0, keep)] + ending[-1:]
        row = (
            entry.get("tablet") or "?", entry.get("device_id") or "", started, ended, duration,
            entry.get("ready_ms"), len(entry.get("turns") or []), json.dumps(entry.get("turns") or []),
            json.dumps(entry.get("tools") or []), json.dumps(entry.get("usage") or {}), realtime, entry.get("error") or "",
            json.dumps(timeline, ensure_ascii=False, default=str), entry.get("end_reason") or "",
        )
        with self._lock:
            self._db.execute(
                "INSERT INTO sessions (tablet, device_id, started, ended, duration_s, ready_ms, turns, latencies, tools, usage, realtime_cost, error, timeline, end_reason)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            self._prune(costs)
            self._db.commit()
        return {"duration_s": duration, "realtime_cost": realtime}

    def record_summary(self, tablet: str, model: str, input_tokens: int, output_tokens: int, costs: dict, ts: float | None = None) -> float:
        cost = round(
            input_tokens / 1e6 * float(costs.get("summary_input_per_m", 1.25))
            + output_tokens / 1e6 * float(costs.get("summary_output_per_m", 2.5)),
            8,
        )
        with self._lock:
            self._db.execute(
                "INSERT INTO summaries (ts, tablet, model, input_tokens, output_tokens, cost) VALUES (?,?,?,?,?,?)",
                (ts or time.time(), tablet, model, int(input_tokens), int(output_tokens), cost),
            )
            self._db.commit()
        return cost

    def _prune(self, costs: dict) -> None:
        cutoff = time.time() - int(costs.get("retention_days", 90)) * 86400
        self._db.execute("DELETE FROM sessions WHERE started < ?", (cutoff,))
        self._db.execute("DELETE FROM summaries WHERE ts < ?", (cutoff,))

    # read -------------------------------------------------------------------
    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def report(self, time_zone: str = "UTC", now: float | None = None, recent: int = 50, names: dict | None = None) -> dict:
        zone = ZoneInfo(time_zone or "UTC")
        now = now or time.time()
        today = datetime.fromtimestamp(now, zone).replace(hour=0, minute=0, second=0, microsecond=0)
        starts = {"today": today.timestamp(), "7d": (today - timedelta(days=6)).timestamp(), "30d": (today - timedelta(days=29)).timestamp()}
        sessions = self._rows("SELECT * FROM sessions WHERE started >= ? ORDER BY started", (starts["30d"],))
        summaries = self._rows("SELECT * FROM summaries WHERE ts >= ? ORDER BY ts", (starts["30d"],))
        names = names or {}

        def label(row) -> str:
            return names.get(row["device_id"]) or row["tablet"] or "?"

        totals = {}
        for key, since in starts.items():
            ses = [r for r in sessions if r["started"] >= since]
            summ = [r for r in summaries if r["ts"] >= since]
            lat = [ms for r in ses for ms in json.loads(r["latencies"])]
            realtime = sum(r["realtime_cost"] for r in ses)
            summary = sum(r["cost"] for r in summ)
            totals[key] = {
                "sessions": len(ses), "minutes": round(sum(r["duration_s"] for r in ses) / 60, 2),
                "turns": sum(r["turns"] for r in ses), "tool_calls": sum(len(json.loads(r["tools"])) for r in ses),
                "realtime_cost": round(realtime, 4), "summary_cost": round(summary, 4), "cost": round(realtime + summary, 4),
                "summaries": len(summ), "latency_median": median(lat), "latency_p90": percentile(lat, 90),
            }

        tablets: dict[str, dict] = {}
        for r in sessions:
            t = tablets.setdefault(label(r), {"tablet": label(r), "sessions": 0, "minutes": 0.0, "realtime_cost": 0.0, "summary_cost": 0.0, "lat": []})
            t["sessions"] += 1
            t["minutes"] += r["duration_s"] / 60
            t["realtime_cost"] += r["realtime_cost"]
            t["lat"] += json.loads(r["latencies"])
        by_device = {r["device_id"]: label(r) for r in sessions}
        for r in summaries:
            name = names.get(r["tablet"]) or by_device.get(r["tablet"]) or r["tablet"] or "?"
            t = tablets.setdefault(name, {"tablet": name, "sessions": 0, "minutes": 0.0, "realtime_cost": 0.0, "summary_cost": 0.0, "lat": []})
            t["summary_cost"] += r["cost"]
        per_tablet = []
        for t in tablets.values():
            lat = t.pop("lat")
            per_tablet.append({
                **t, "minutes": round(t["minutes"], 2), "realtime_cost": round(t["realtime_cost"], 4),
                "summary_cost": round(t["summary_cost"], 4), "cost": round(t["realtime_cost"] + t["summary_cost"], 4),
                "latency_median": median(lat), "latency_p90": percentile(lat, 90),
            })
        per_tablet.sort(key=lambda t: -t["cost"])

        daily = []
        for back in range(29, -1, -1):
            day = today - timedelta(days=back)
            lo, hi = day.timestamp(), (day + timedelta(days=1)).timestamp()
            ses = [r for r in sessions if lo <= r["started"] < hi]
            daily.append({
                "date": day.date().isoformat(), "sessions": len(ses),
                "minutes": round(sum(r["duration_s"] for r in ses) / 60, 2),
                "realtime_cost": round(sum(r["realtime_cost"] for r in ses), 4),
                "summary_cost": round(sum(r["cost"] for r in summaries if lo <= r["ts"] < hi), 4),
            })

        rows = self._rows("SELECT * FROM sessions ORDER BY started DESC LIMIT ?", (recent,))
        recent_rows = []
        for r in rows:
            lat = json.loads(r["latencies"] or "[]")
            keys = r.keys()
            recent_rows.append({
                "id": r["id"], "tablet": label(r), "started": r["started"], "ended": r["ended"],
                "duration_s": round(r["duration_s"], 1), "ready_ms": r["ready_ms"], "turns": r["turns"],
                "latencies": lat, "latency_median": median(lat), "latency_p90": percentile(lat, 90),
                "tools": json.loads(r["tools"] or "[]"), "usage": json.loads(r["usage"] or "{}"),
                "realtime_cost": r["realtime_cost"], "error": r["error"],
                "timeline": json.loads(r["timeline"] or "[]") if "timeline" in keys else [],
                "end_reason": (r["end_reason"] if "end_reason" in keys else "") or "",
            })
        return {"totals": totals, "tablets": per_tablet, "daily": daily, "recent": recent_rows, "estimate": True}


SENSORS = {
    "sensor.grok_relay_cost_today": ("cost", "Grok relay cost today (estimate)", "USD", "mdi:currency-usd"),
    "sensor.grok_relay_sessions_today": ("sessions", "Grok relay sessions today", None, "mdi:account-voice"),
    "sensor.grok_relay_minutes_today": ("minutes", "Grok relay minutes today", "min", "mdi:timer-outline"),
    "sensor.grok_relay_latency_median": ("latency_median", "Grok relay latency median today", "ms", "mdi:timer-sand"),
}


def sensor_states(today: dict) -> list[tuple[str, object, dict]]:
    """(entity_id, state, attributes) for the optional HA sensors."""
    out = []
    for entity_id, (key, name, unit, icon) in SENSORS.items():
        value = today.get(key)
        attrs = {"friendly_name": name, "icon": icon}
        if unit:
            attrs["unit_of_measurement"] = unit
        if key == "cost":
            attrs.update({"realtime_cost": today.get("realtime_cost"), "summary_cost": today.get("summary_cost"), "estimate": True})
        if key == "latency_median":
            attrs["p90"] = today.get("latency_p90")
        out.append((entity_id, "unknown" if value is None else value, attrs))
    return out
