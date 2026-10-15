"""Relay settings in /data/settings.json, edited from the panel, read at each session.

Only bootstrap values (xAI key, relay secret, port) live in the add-on options.
Everything here applies to the next session without a restart. A tablet can
override the session keys (``TABLET_KEYS``) under ``tablets.<device_id>``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
from pathlib import Path

from app.ks_relay.dates import template_errors

log = logging.getLogger("grok_voice.ks_relay.settings")

TOOL_FAMILIES = {
    "power": "Turn things on and off (lights, switches, fans, media players, scenes)",
    "lights": "Light brightness and color",
    "covers": "Garage doors, doors, gates, blinds",
    "locks": "Locks",
    "climate": "Thermostat temperature and mode",
    "fans": "Fan speed",
    "timers": "Timers on the tablet",
    "todo": "To-do and shopping lists",
    "state": "State lookup (is the garage open, temperature, ...)",
}
FAMILY_VALUES = ("direct", "off")

DEFAULTS: dict = {
    "model": "",
    "reasoning_effort": "none",
    "vad": {"mode": "unset", "silence_ms": 500},
    "inject_datetime": True,
    "date_template": "",
    "annotate_tool_dates": True,
    "extra_instructions": (
        "Home control and timers run through your tools; call them right away with sensible "
        "defaults instead of asking which device or room."
    ),
    "tools": {family: "direct" for family in TOOL_FAMILIES},
    "memory": {
        "enabled": True,
        "mode": "summary",
        "window_minutes": 45,
        "max_items": 5,
        "scope": "per_tablet",
        "house_window_minutes": 10,
        "strip_ks_history": True,
        "summary_model": "grok-4.20-0309-non-reasoning",
        "summary_max_chars": 400,
    },
    "costs": {
        "realtime_per_min": 0.08,
        "realtime_basis": "session",
        "summary_input_per_m": 1.25,
        "summary_output_per_m": 2.5,
        "retention_days": 90,
    },
    "ha_sensors": False,
    "tablets": {},
}
TABLET_KEYS = ("reasoning_effort", "vad", "inject_datetime", "annotate_tool_dates", "extra_instructions", "tools", "memory_enabled")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{0,64}$")


class SettingsError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _int(value, name, lo, hi, errors):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        errors.append(f"{name} must be a whole number")
        return None
    if not lo <= int(value) <= hi:
        errors.append(f"{name} must be between {lo} and {hi}")
        return None
    return int(value)


def _choice(value, name, options, errors):
    if value not in options:
        errors.append(f"{name} must be one of {', '.join(options)}")
        return None
    return value


def _money(value, name, hi, errors):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= hi:
        errors.append(f"{name} must be a number between 0 and {hi}")
        return None
    return float(value)


def _bool(value, name, errors):
    if not isinstance(value, bool):
        errors.append(f"{name} must be true or false")
        return None
    return value


def _text(value, name, limit, errors):
    if not isinstance(value, str):
        errors.append(f"{name} must be text")
        return None
    if len(value) > limit:
        errors.append(f"{name} is longer than {limit} characters")
        return None
    return value.strip()


def _vad(value, name, errors):
    if not isinstance(value, dict):
        errors.append(f"{name} must be an object")
        return None
    mode = _choice(value.get("mode"), f"{name}.mode", ("unset", "kiosk", "custom"), errors)
    silence = _int(value.get("silence_ms", 500), f"{name}.silence_ms", 0, 3000, errors)
    if mode is None or silence is None:
        return None
    return {"mode": mode, "silence_ms": silence}


def _tools(value, name, errors, *, partial=False):
    if not isinstance(value, dict):
        errors.append(f"{name} must be an object")
        return None
    out = {}
    for key, item in value.items():
        if key in TOOL_FAMILIES:
            ok = _choice(item, f"{name}.{key}", FAMILY_VALUES, errors)
        else:
            errors.append(f"{name}.{key} is not a tool family")
            continue
        if ok is not None:
            out[key] = ok
    if not partial:
        out = {**DEFAULTS["tools"], **out}
    return out


def validate(data: object) -> dict:
    """Return a complete, clean settings dict or raise SettingsError."""
    errors: list[str] = []
    if not isinstance(data, dict):
        raise SettingsError(["settings must be an object"])
    merged = copy.deepcopy(DEFAULTS)
    for key in data:
        if key not in DEFAULTS:
            errors.append(f"unknown setting {key}")
    model = data.get("model", merged["model"])
    if not isinstance(model, str) or not _MODEL_RE.match(model.strip()):
        errors.append("model may only use letters, digits, dot, dash and underscore")
    else:
        merged["model"] = model.strip()
    effort = _choice(data.get("reasoning_effort", merged["reasoning_effort"]), "reasoning_effort", ("none", "high", "default"), errors)
    if effort:
        merged["reasoning_effort"] = effort
    if "vad" in data:
        vad = _vad(data["vad"], "vad", errors)
        if vad:
            merged["vad"] = vad
    if "inject_datetime" in data:
        flag = _bool(data["inject_datetime"], "inject_datetime", errors)
        if flag is not None:
            merged["inject_datetime"] = flag
    if "annotate_tool_dates" in data:
        flag = _bool(data["annotate_tool_dates"], "annotate_tool_dates", errors)
        if flag is not None:
            merged["annotate_tool_dates"] = flag
    if "date_template" in data:
        text = _text(data["date_template"], "date_template", 1000, errors)
        if text is not None:
            problems = template_errors(text)
            errors.extend(problems)
            if not problems:
                merged["date_template"] = text
    if "extra_instructions" in data:
        text = _text(data["extra_instructions"], "extra_instructions", 4000, errors)
        if text is not None:
            merged["extra_instructions"] = text
    if "tools" in data:
        tools = _tools(data["tools"], "tools", errors)
        if tools:
            merged["tools"] = tools
    memory = data.get("memory", {})
    if not isinstance(memory, dict):
        errors.append("memory must be an object")
        memory = {}
    for key in memory:
        if key not in DEFAULTS["memory"]:
            errors.append(f"unknown memory setting {key}")
    m = merged["memory"]
    checks = {
        "enabled": lambda v: _bool(v, "memory.enabled", errors),
        "mode": lambda v: _choice(v, "memory.mode", ("summary", "raw", "off"), errors),
        "window_minutes": lambda v: _int(v, "memory.window_minutes", 1, 1440, errors),
        "max_items": lambda v: _int(v, "memory.max_items", 1, 20, errors),
        "scope": lambda v: _choice(v, "memory.scope", ("per_tablet", "house"), errors),
        "house_window_minutes": lambda v: _int(v, "memory.house_window_minutes", 0, 240, errors),
        "strip_ks_history": lambda v: _bool(v, "memory.strip_ks_history", errors),
        "summary_model": lambda v: v.strip() if isinstance(v, str) and _MODEL_RE.match(v.strip()) and v.strip() else errors.append("memory.summary_model is not a model name"),
        "summary_max_chars": lambda v: _int(v, "memory.summary_max_chars", 80, 2000, errors),
    }
    for key, check in checks.items():
        if key in memory:
            value = check(memory[key])
            if value is not None:
                m[key] = value
    costs = data.get("costs", {})
    if not isinstance(costs, dict):
        errors.append("costs must be an object")
        costs = {}
    for key in costs:
        if key not in DEFAULTS["costs"]:
            errors.append(f"unknown costs setting {key}")
    cost_checks = {
        "realtime_per_min": lambda v: _money(v, "costs.realtime_per_min", 10, errors),
        "summary_input_per_m": lambda v: _money(v, "costs.summary_input_per_m", 100, errors),
        "summary_output_per_m": lambda v: _money(v, "costs.summary_output_per_m", 100, errors),
        "retention_days": lambda v: _int(v, "costs.retention_days", 1, 3650, errors),
        "realtime_basis": lambda v: _choice(v, "costs.realtime_basis", ("session", "billable_audio"), errors),
    }
    for key, check in cost_checks.items():
        if key in costs:
            value = check(costs[key])
            if value is not None:
                merged["costs"][key] = value
    if "ha_sensors" in data:
        flag = _bool(data["ha_sensors"], "ha_sensors", errors)
        if flag is not None:
            merged["ha_sensors"] = flag
    tablets = data.get("tablets", {})
    if not isinstance(tablets, dict):
        errors.append("tablets must be an object")
        tablets = {}
    for device_id, override in tablets.items():
        label = f"tablets.{device_id}"
        if not isinstance(device_id, str) or not re.match(r"^[A-Za-z0-9_.-]{1,64}$", device_id):
            errors.append(f"{label}: bad device id")
            continue
        if not isinstance(override, dict):
            errors.append(f"{label} must be an object")
            continue
        clean = {}
        for key, value in override.items():
            if key not in TABLET_KEYS:
                errors.append(f"{label}.{key} cannot be set per tablet")
            elif key == "reasoning_effort":
                clean[key] = _choice(value, f"{label}.reasoning_effort", ("none", "high", "default"), errors)
            elif key == "vad":
                clean[key] = _vad(value, f"{label}.vad", errors)
            elif key in ("inject_datetime", "annotate_tool_dates", "memory_enabled"):
                clean[key] = _bool(value, f"{label}.{key}", errors)
            elif key == "extra_instructions":
                clean[key] = _text(value, f"{label}.extra_instructions", 4000, errors)
            elif key == "tools":
                clean[key] = _tools(value, f"{label}.tools", errors, partial=True)
        clean = {k: v for k, v in clean.items() if v is not None}
        if clean:
            merged["tablets"][device_id] = clean
    if errors:
        raise SettingsError(errors)
    return merged


def effective(settings: dict, device_id: str = "") -> dict:
    """Settings for one tablet: the house settings with its overrides on top."""
    out = copy.deepcopy(settings)
    override = (settings.get("tablets") or {}).get(device_id) or {}
    for key, value in override.items():
        if key == "tools":
            out["tools"] = {**out["tools"], **value}
        elif key == "memory_enabled":
            out["memory"] = {**out["memory"], "enabled": value}
        else:
            out[key] = value
    out.pop("tablets", None)
    return out


class SettingsStore:
    """Thread-safe JSON file; reloads when the file changes on disk."""

    def __init__(self, path: str | Path = "/data/settings.json") -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._mtime = None
        self._data = copy.deepcopy(DEFAULTS)
        self._load()

    def _load(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return
        if stat.st_mtime == self._mtime:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = validate(raw)
        except (ValueError, OSError) as exc:
            log.error("ignoring %s: %s", self.path, exc)
        self._mtime = stat.st_mtime

    def get(self) -> dict:
        with self._lock:
            self._load()
            return copy.deepcopy(self._data)

    def for_tablet(self, device_id: str = "") -> dict:
        return effective(self.get(), device_id)

    def save(self, data: object) -> dict:
        clean = validate(data)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            self._data = clean
            self._mtime = self.path.stat().st_mtime
        log.info("relay settings saved")
        return copy.deepcopy(clean)
