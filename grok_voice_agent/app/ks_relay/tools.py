"""The relay's own voice tools. Small schemas, run against Home Assistant here.

Device actions go through Home Assistant's intents (``/api/intent/handle``) with
``assistant: conversation``, so only entities exposed to Assist are reachable,
names and aliases match the way Assist matches them, and the tablet's device id
gives timers and "here" their usual meaning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from app.ks_relay.ha import HaLink, Tablet
from app.ks_relay.skills import SKILL_TOOL, resolve

log = logging.getLogger("grok_voice.ks_relay.tools")

END_CONVERSATION = "end_conversation"

_TARGET = {
    "name": {"type": "string", "description": "Device or entity name as the user said it."},
    "area": {"type": "string", "description": "Room. Omit for this tablet's room."},
}
_DOMAINS = ["light", "switch", "fan", "media_player", "input_boolean", "scene", "script", "climate", "humidifier", "vacuum"]
_COVER_CLASSES = ["garage", "door", "gate", "window", "blind", "shade", "curtain", "shutter", "awning"]


def _fn(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    parameters: dict = {"type": "object", "properties": properties or {}}
    if required:
        parameters["required"] = required
    return {"type": "function", "name": name, "description": description, "parameters": parameters}


FAMILY_OF = {
    "turn_on": "power",
    "turn_off": "power",
    "set_light": "lights",
    "open_close": "covers",
    "lock": "locks",
    "climate": "climate",
    "fan_speed": "fans",
    "timer": "timers",
    "todo": "todo",
    "get_state": "state",
}


def tool_specs(families: dict | None = None) -> list[dict]:
    """The built-in tool families; families maps a family to "direct" or "off"."""
    families = families or {}
    specs = [
        _fn(
            "turn_on",
            "Turn on lights, switches, fans, media players, scenes, scripts.",
            {**_TARGET, "domain": {"type": "string", "enum": _DOMAINS}},
        ),
        _fn(
            "turn_off",
            "Turn off lights, switches, fans, media players.",
            {**_TARGET, "domain": {"type": "string", "enum": _DOMAINS}},
        ),
        _fn(
            "set_light",
            "Set light brightness or color.",
            {
                **_TARGET,
                "brightness": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Percent."},
                "color": {"type": "string"},
            },
        ),
        _fn(
            "open_close",
            "Open, close or set the position of garage doors, doors, gates, blinds, shades, windows.",
            {
                **_TARGET,
                "action": {"type": "string", "enum": ["open", "close", "position"]},
                "position": {"type": "integer", "minimum": 0, "maximum": 100},
                "device_class": {"type": "string", "enum": _COVER_CLASSES},
            },
            ["action"],
        ),
        _fn(
            "lock",
            "Lock or unlock a lock.",
            {**_TARGET, "action": {"type": "string", "enum": ["lock", "unlock"]}},
            ["action"],
        ),
        _fn(
            "climate",
            "Set the thermostat temperature and/or mode.",
            {
                **_TARGET,
                "temperature": {"type": "number"},
                "mode": {"type": "string", "enum": ["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"]},
            },
        ),
        _fn(
            "fan_speed",
            "Set a fan's speed in percent.",
            {**_TARGET, "percentage": {"type": "integer", "minimum": 0, "maximum": 100}},
            ["percentage"],
        ),
        _fn(
            "timer",
            "Timers on this tablet. Not alarms.",
            {
                "action": {
                    "type": "string",
                    "enum": ["start", "cancel", "cancel_all", "status", "add_time", "remove_time", "pause", "resume"],
                },
                "hours": {"type": "integer"},
                "minutes": {"type": "integer"},
                "seconds": {"type": "integer"},
                "name": {"type": "string"},
            },
            ["action"],
        ),
        _fn(
            "todo",
            "Add to or complete an item on a to-do or shopping list.",
            {
                "action": {"type": "string", "enum": ["add", "complete"]},
                "item": {"type": "string"},
                "list": {"type": "string", "description": "List name. Omit for the shopping list."},
            },
            ["action", "item"],
        ),
        _fn(
            "get_state",
            "Current state of devices and sensors (on/off, temperature, open, locked, ...).",
            {**_TARGET, "domain": {"type": "string"}},
        ),
    ]
    specs = [
        spec
        for spec in specs
        if families.get(FAMILY_OF[spec["name"]], "direct") == "direct"
    ]
    return specs


TOOL_NAMES = frozenset(spec["name"] for spec in tool_specs())


def _clean(args: dict, *keys: str) -> dict:
    out = {}
    for key in keys:
        value = args.get(key)
        if value not in (None, "", []):
            out[key] = value
    return out


def _intent_summary(result: dict) -> dict:
    speech = (((result or {}).get("speech") or {}).get("plain") or {}).get("speech") or ""
    data = (result or {}).get("data") or {}
    summary: dict = {"ok": result.get("response_type") != "error"}
    if speech:
        summary["say"] = speech
    if result.get("response_type") == "error":
        summary["error"] = data.get("code") or "failed"
    names = [t.get("name") for t in data.get("success") or [] if t.get("type") == "entity"]
    if names:
        summary["done"] = names[:12]
    failed = [t.get("name") for t in data.get("failed") or []]
    if failed:
        summary["failed"] = failed[:12]
    return summary


class RelayTools:
    """Executes the relay's tools for one tablet."""

    def __init__(
        self,
        ha: HaLink,
        tablet: Tablet | None = None,
        *,
        skills: list[dict] | None = None,
        dry_run: bool = False,
        dry_run_delay: float = 0.0,
    ) -> None:
        self.ha = ha
        self.skills = skills or []
        self.direct_skills = {sk["name"] for sk in self.skills if sk["enabled"] and sk["direct"]}
        self.tablet = tablet
        self.dry_run = dry_run
        self.dry_run_delay = dry_run_delay

    def _with_area(self, args: dict) -> dict:
        if not args.get("name") and not args.get("area") and self.tablet and self.tablet.area_name:
            return {**args, "area": self.tablet.area_name}
        return args

    async def _intent(self, name: str, data: dict) -> dict:
        if self.dry_run:
            await asyncio.sleep(self.dry_run_delay)
            return {"ok": True, "dry_run": True, "intent": name, "data": data}
        result = await self.ha.intent(
            name,
            data,
            device_id=self.tablet.device_id if self.tablet else "",
            satellite_id=self.tablet.satellite_entity if self.tablet else "",
        )
        return _intent_summary(result)

    async def execute(self, name: str, args: dict) -> dict:
        args = args if isinstance(args, dict) else {}
        if name == SKILL_TOOL:
            return await self.run_skill(args.get("name"), args.get("action"), args.get("args"))
        if name in self.direct_skills:
            rest = {k: v for k, v in args.items() if k != "action"}
            return await self.run_skill(name, args.get("action"), rest)
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return {"ok": False, "error": f"unknown tool {name}"}
        return await handler(args)

    async def _t_turn_on(self, args):
        data = _clean(self._with_area(args), "name", "area")
        if args.get("domain"):
            data["domain"] = [args["domain"]]
        return await self._intent("HassTurnOn", data)

    async def _t_turn_off(self, args):
        data = _clean(self._with_area(args), "name", "area")
        if args.get("domain"):
            data["domain"] = [args["domain"]]
        return await self._intent("HassTurnOff", data)

    async def _t_set_light(self, args):
        data = _clean(self._with_area(args), "name", "area", "brightness", "color")
        if "brightness" not in data and "color" not in data:
            return {"ok": False, "error": "give brightness or color"}
        data["domain"] = ["light"]
        return await self._intent("HassLightSet", data)

    async def _t_open_close(self, args):
        data = _clean(args, "name", "area")
        if args.get("device_class"):
            data["device_class"] = [args["device_class"]]
        if not data.get("name") and not data.get("device_class"):
            data = _clean(self._with_area(data), "name", "area")
        action = args.get("action")
        if action == "position":
            if args.get("position") is None:
                return {"ok": False, "error": "position needed"}
            data["position"] = int(args["position"])
            return await self._intent("HassSetPosition", data)
        data["domain"] = ["cover"]
        return await self._intent("HassTurnOn" if action == "open" else "HassTurnOff", data)

    async def _t_lock(self, args):
        data = _clean(args, "name", "area")
        data["domain"] = ["lock"]
        return await self._intent("HassTurnOn" if args.get("action") == "lock" else "HassTurnOff", data)

    async def _t_climate(self, args):
        target = _clean(self._with_area(args), "name", "area")
        out: dict = {"ok": True}
        if args.get("temperature") is not None:
            out = await self._intent("HassClimateSetTemperature", {**target, "temperature": args["temperature"]})
        if args.get("mode"):
            entities = self._match(target, "climate")
            if not entities:
                entities = self._match({}, "climate") if len(self._match({}, "climate")) == 1 else []
            if not entities:
                return {"ok": False, "error": "no matching thermostat"}
            if not self.dry_run:
                await self.ha.call_service(
                    "climate", "set_hvac_mode", {"hvac_mode": args["mode"]}, target={"entity_id": entities}
                )
            out = {**out, "mode": args["mode"], "done": [self._friendly(e) for e in entities]}
        if args.get("temperature") is None and not args.get("mode"):
            return {"ok": False, "error": "give temperature or mode"}
        return out

    async def _t_fan_speed(self, args):
        data = _clean(self._with_area(args), "name", "area")
        data["percentage"] = int(args.get("percentage") or 0)
        return await self._intent("HassFanSetSpeed", data)

    async def _t_timer(self, args):
        action = args.get("action")
        span = _clean(args, "hours", "minutes", "seconds")
        name = _clean(args, "name")
        if action == "start":
            if not span:
                return {"ok": False, "error": "give a duration"}
            return await self._intent("HassStartTimer", {**span, **name})
        if action == "cancel_all":
            return await self._intent("HassCancelAllTimers", {})
        intent = {
            "cancel": "HassCancelTimer",
            "status": "HassTimerStatus",
            "pause": "HassPauseTimer",
            "resume": "HassUnpauseTimer",
            "add_time": "HassIncreaseTimer",
            "remove_time": "HassDecreaseTimer",
        }.get(action)
        if not intent:
            return {"ok": False, "error": "unknown timer action"}
        data = dict(name)
        if action in ("add_time", "remove_time"):
            if not span:
                return {"ok": False, "error": "give the time to add or remove"}
            data.update(span)
        return await self._intent(intent, data)

    async def _t_todo(self, args):
        list_name = args.get("list") or self._default_list()
        if not list_name:
            return {"ok": False, "error": "which list?"}
        intent = "HassListAddItem" if args.get("action") == "add" else "HassListCompleteItem"
        return await self._intent(intent, {"item": str(args.get("item") or ""), "name": list_name})

    def _default_list(self) -> str:
        lists = [e for e in self.ha.cache.exposed if e.startswith("todo.")]
        names = [self._friendly(e) for e in sorted(lists)]
        for name in names:
            if "shopping" in name.lower():
                return name
        return names[0] if len(names) == 1 else ""

    async def _t_get_state(self, args):
        entities = self._match(_clean(args, "name", "area"), args.get("domain") or "")
        if not entities and args.get("name"):
            entities = self._match(_clean(args, "name"), args.get("domain") or "")
        cache = self.ha.cache
        rows = []
        for entity_id in entities[:40]:
            state = cache.states.get(entity_id) or {}
            attrs = state.get("attributes") or {}
            row = {
                "name": self._friendly(entity_id),
                "area": cache.area_name(cache.entity_area_id(entity_id)),
                "state": state.get("state"),
            }
            for key in (
                "unit_of_measurement",
                "brightness",
                "current_temperature",
                "temperature",
                "hvac_action",
                "current_position",
                "media_title",
                "media_artist",
                "volume_level",
                "percentage",
            ):
                if attrs.get(key) not in (None, ""):
                    value = attrs[key]
                    if key == "brightness" and isinstance(value, (int, float)):
                        value = round(value * 100 / 255)
                    row[key] = value
            rows.append(row)
        if not rows:
            return {"ok": False, "error": "nothing exposed matches"}
        return {"ok": True, "entities": rows}

    # skills -----------------------------------------------------------------
    async def run_skill(self, name, action, args) -> dict:
        skill, chosen, data = resolve(self.skills, name, action, args or {})
        if chosen is None:
            return data
        backend = chosen["backend"]
        payload = {**backend.get("fixed", {}), **data}
        if self.dry_run:
            await asyncio.sleep(self.dry_run_delay)
            return {"ok": True, "dry_run": True, "skill": skill["name"], "action": chosen["name"], "backend": backend["type"], "data": payload}
        kind, target = backend["type"], backend.get("target", "")
        if kind == "relay":
            return await self._relay_handler(target, payload)
        if kind == "ha_skill":
            if not self.ha.cache.skill_available:
                return {"ok": False, "error": "script.skill is not available in Home Assistant"}
            response = await self.ha.call_service(
                "script", "skill", {"name": skill["name"], "action": chosen["name"], "args": data}, return_response=True, timeout=120
            )
        else:
            domain, service = target.split(".", 1)
            wants = kind == "script" or backend.get("response", True)
            response = await self.ha.call_service(domain, service, payload, return_response=wants, timeout=120)
        if response in (None, {}) or not isinstance(response, dict):
            return {"ok": True, "skill": skill["name"], "action": chosen["name"], "say": "Done.", **({"response": response} if response else {})}
        return response

    async def _relay_handler(self, target: str, data: dict) -> dict:
        target_args = self._with_area(_clean(data, "name", "area"))
        target_args = _clean(target_args, "name", "area")
        if target == "media.play":
            slots = {**target_args, "search_query": str(data.get("query") or "")}
            if data.get("media_class"):
                slots["media_class"] = str(data["media_class"])
            return await self._intent("HassMediaSearchAndPlay", slots)
        if target == "media.volume":
            return await self._intent("HassSetVolume", {**target_args, "volume_level": int(float(data.get("volume") or 0))})
        intent = {
            "media.pause": "HassMediaPause",
            "media.resume": "HassMediaUnpause",
            "media.next": "HassMediaNext",
            "media.previous": "HassMediaPrevious",
        }.get(target)
        if not intent:
            return {"ok": False, "error": f"unknown relay handler {target}"}
        return await self._intent(intent, target_args)

    # matching -------------------------------------------------------------
    def _friendly(self, entity_id: str) -> str:
        state = self.ha.cache.states.get(entity_id) or {}
        return str((state.get("attributes") or {}).get("friendly_name") or entity_id)

    def _match(self, target: dict, domain: str) -> list[str]:
        """Exposed entities in the area whose name contains the asked name (else the reverse)."""
        cache = self.ha.cache
        name = _words(target.get("name"))
        area = _words(target.get("area"))
        pool = []
        for entity_id in sorted(cache.exposed):
            if domain and not entity_id.startswith(f"{domain}."):
                continue
            if area:
                area_id = cache.entity_area_id(entity_id)
                names = {_words(cache.area_name(area_id)), _words(area_id)}
                names |= {_words(a) for a in (cache.areas.get(area_id) or {}).get("aliases") or []}
                if area not in names:
                    continue
            pool.append(entity_id)
        if not name:
            return pool

        def candidates(entity_id):
            entry = cache.entities.get(entity_id) or {}
            found = [_words(self._friendly(entity_id)), _words(entity_id.split(".", 1)[1])]
            return [c for c in found + [_words(a) for a in entry.get("aliases") or [] if isinstance(a, str)] if c]

        exact = [e for e in pool if any(name in c for c in candidates(e))]
        return exact or [e for e in pool if any(c in name for c in candidates(e))]


def _words(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


async def run_tool(tools: RelayTools, name: str, arguments: str, timeout: float) -> tuple[str, bool]:
    """Run one call and return (output text for the model, ok)."""
    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "error": "unreadable arguments"}), False
    try:
        result = await asyncio.wait_for(tools.execute(name, args), timeout)
    except asyncio.TimeoutError:
        return json.dumps({"ok": False, "error": "timed out"}), False
    except Exception as exc:  # noqa: BLE001 - the model gets the failure
        log.warning("tool %s failed: %s", name, type(exc).__name__)
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}), False
    ok = not (isinstance(result, dict) and result.get("ok") is False)
    return json.dumps(result, ensure_ascii=False, default=str)[:8000], ok
