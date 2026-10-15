"""Skills that live in the relay.

A skill has an index line (what goes into the ``skill`` tool description), help
text (returned on ``action=help`` or on a bad call) and actions. Each action
maps to a backend:

* ``script``  -- a Home Assistant script (``script.x``), called with fixed data
  plus the mapped args, response returned;
* ``service`` -- any Home Assistant service ``domain.service``;
* ``relay``   -- a handler built into the relay (``media.play``, ``media.pause``...);
* ``ha_skill``-- pass through to HA's ``script.skill`` (what an import keeps
  for actions the relay has no mapping for). An empty args spec forwards every
  argument; a declared spec still validates.

A skill can be promoted to a direct tool: one function named after the skill
with an ``action`` enum and the union of its args. Stored in /data/skills.json,
seeded from ``data/seed_skills.json`` (the former pyscript ``ks_skill`` + HA
``script.skill``), and re-read when the file changes.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
from pathlib import Path

log = logging.getLogger("grok_voice.ks_relay.skills")

SEED_PATH = Path(__file__).resolve().parent / "data" / "seed_skills.json"
BACKENDS = ("script", "service", "relay", "ha_skill")
RELAY_HANDLERS = ("media.play", "media.pause", "media.resume", "media.next", "media.previous", "media.volume")
COERCE = ("", "json_string", "day_list", "integer", "number")
SKILL_TOOL = "skill"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_TARGET_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
RESERVED = {"skill", "end_conversation", "turn_on", "turn_off", "set_light", "open_close", "lock", "climate",
            "fan_speed", "timer", "todo", "get_state"}


def key(text: object) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(text or "").strip().lower()).strip("_")


class SkillError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _strs(value, label, errors) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        errors.append(f"{label} must be a list of names")
        return []
    return [key(v) for v in value if key(v)]


def validate_skills(data: object) -> list[dict]:
    errors: list[str] = []
    skills = data.get("skills") if isinstance(data, dict) else data
    if not isinstance(skills, list):
        raise SkillError(["skills must be a list"])
    out, seen = [], set()
    for i, skill in enumerate(skills):
        label = f"skill #{i + 1}"
        if not isinstance(skill, dict):
            errors.append(f"{label} must be an object")
            continue
        name = key(skill.get("name"))
        if not _NAME_RE.match(name):
            errors.append(f"{label}: name must be lowercase letters, digits or _ (max 32)")
            continue
        label = f"skill {name}"
        if name in seen:
            errors.append(f"{label}: duplicate name")
        if skill.get("direct") and name in RESERVED:
            errors.append(f"{label}: cannot be direct, {name} is a built-in tool name")
        seen.add(name)
        index = str(skill.get("index") or name).strip()
        if len(index) > 300:
            errors.append(f"{label}: index line is longer than 300 characters")
        help_text = str(skill.get("help") or "").strip()
        if len(help_text) > 4000:
            errors.append(f"{label}: help is longer than 4000 characters")
        actions, act_seen = [], set()
        for j, action in enumerate(skill.get("actions") or []):
            alabel = f"{label} action #{j + 1}"
            if not isinstance(action, dict):
                errors.append(f"{alabel} must be an object")
                continue
            aname = key(action.get("name"))
            if not _NAME_RE.match(aname) or aname == "help":
                errors.append(f"{alabel}: bad action name")
                continue
            alabel = f"{label}.{aname}"
            if aname in act_seen:
                errors.append(f"{alabel}: duplicate action")
            act_seen.add(aname)
            backend = action.get("backend") or {}
            btype = backend.get("type")
            target = str(backend.get("target") or "").strip()
            if btype not in BACKENDS:
                errors.append(f"{alabel}: backend type must be one of {', '.join(BACKENDS)}")
            elif btype == "script" and not (target.startswith("script.") and _TARGET_RE.match(target)):
                errors.append(f"{alabel}: script backend needs script.<id>")
            elif btype == "service" and not _TARGET_RE.match(target):
                errors.append(f"{alabel}: service backend needs domain.service")
            elif btype == "relay" and target not in RELAY_HANDLERS:
                errors.append(f"{alabel}: relay handler must be one of {', '.join(RELAY_HANDLERS)}")
            fixed = backend.get("fixed") or {}
            if not isinstance(fixed, dict):
                errors.append(f"{alabel}: fixed data must be an object")
                fixed = {}
            args, arg_seen = [], set()
            for arg in action.get("args") or []:
                if not isinstance(arg, dict) or not _NAME_RE.match(key(arg.get("name"))):
                    errors.append(f"{alabel}: bad arg")
                    continue
                argname = key(arg.get("name"))
                if argname in arg_seen:
                    errors.append(f"{alabel}: duplicate arg {argname}")
                arg_seen.add(argname)
                coerce = str(arg.get("coerce") or "")
                if coerce not in COERCE:
                    errors.append(f"{alabel}.{argname}: coerce must be one of {', '.join(c or '(none)' for c in COERCE)}")
                entry = {
                    "name": argname,
                    "required": bool(arg.get("required")),
                    "aliases": _strs(arg.get("aliases"), f"{alabel}.{argname} aliases", errors),
                }
                if coerce:
                    entry["coerce"] = coerce
                if arg.get("description"):
                    entry["description"] = str(arg["description"])[:200]
                args.append(entry)
            item = {
                "name": aname,
                "aliases": _strs(action.get("aliases"), f"{alabel} aliases", errors),
                "backend": {"type": btype, "target": target, "fixed": fixed},
                "args": args,
            }
            if btype == "service":
                item["backend"]["response"] = bool(backend.get("response", True))
            guard = action.get("guard")
            if guard:
                try:
                    re.compile(str(guard.get("pattern") or ""))
                    item["guard"] = {
                        "arg": key(guard.get("arg")),
                        "pattern": str(guard.get("pattern") or ""),
                        "say": str(guard.get("say") or "I can't do that.")[:300],
                    }
                except (re.error, AttributeError):
                    errors.append(f"{alabel}: guard pattern is not a valid regex")
            actions.append(item)
        if not actions:
            errors.append(f"{label}: needs at least one action")
        out.append(
            {
                "name": name,
                "enabled": bool(skill.get("enabled", True)),
                "direct": bool(skill.get("direct", False)),
                "index": index,
                "help": help_text,
                "aliases": _strs(skill.get("aliases"), f"{label} aliases", errors),
                "actions": actions,
            }
        )
    if errors:
        raise SkillError(errors)
    return out


def load_seed() -> list[dict]:
    return validate_skills(json.loads(SEED_PATH.read_text(encoding="utf-8")))


# tool descriptions ---------------------------------------------------------------
def skill_tool_description(skills: list[dict]) -> str:
    entries = [s["index"] for s in skills if s["enabled"] and not s["direct"]]
    if not entries:
        return ""
    return f"Skills (name, action, args): {'; '.join(entries)}. action=help for details."


def skill_tool_spec(skills: list[dict]) -> dict | None:
    description = skill_tool_description(skills)
    if not description:
        return None
    return {
        "type": "function",
        "name": SKILL_TOOL,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "action": {"type": "string"}, "args": {"type": "object"}},
            "required": ["name"],
        },
    }


def direct_tool_spec(skill: dict) -> dict:
    props: dict = {"action": {"type": "string", "enum": [a["name"] for a in skill["actions"]]}}
    for action in skill["actions"]:
        for arg in action["args"]:
            if arg["name"] in props:
                continue
            kind = {"integer": "integer", "number": "number"}.get(arg.get("coerce", ""), "string")
            spec = {"type": kind}
            if arg.get("description"):
                spec["description"] = arg["description"]
            props[arg["name"]] = spec
    description = skill["index"]
    if skill.get("help"):
        description = f"{description}. {skill['help']}"
    return {
        "type": "function",
        "name": skill["name"],
        "description": description[:700],
        "parameters": {"type": "object", "properties": props, "required": ["action"]},
    }


def skill_specs(skills: list[dict]) -> list[dict]:
    specs = [direct_tool_spec(s) for s in skills if s["enabled"] and s["direct"]]
    gateway = skill_tool_spec(skills)
    return [*specs, gateway] if gateway else specs


def preview(skills: list[dict]) -> dict:
    specs = skill_specs(skills)
    text = json.dumps(specs, ensure_ascii=False)
    return {
        "skill_description": skill_tool_description(skills),
        "tools": specs,
        "bytes": len(text.encode("utf-8")),
        "tokens_estimate": round(len(text) / 4),
    }


# calls ---------------------------------------------------------------------------
def _help(skill: dict, error: str | None = None) -> dict:
    out = {"ok": error is None, "skill": skill["name"], "actions": [a["name"] for a in skill["actions"]], "help": skill["help"]}
    if error:
        out["error"] = error
        out["say"] = error
    return out


def resolve(skills: list[dict], name: object, action: object, args: object) -> tuple[dict | None, dict | None, dict]:
    """-> (skill, action, data) for a valid call, or (skill|None, None, reply)."""
    enabled = [s for s in skills if s["enabled"]]
    wanted = key(name)
    skill = next((s for s in enabled if s["name"] == wanted or wanted in s["aliases"]), None)
    if skill is None:
        names = ", ".join(s["name"] for s in enabled)
        return None, None, {"ok": False, "error": f"unknown skill '{str(name)[:40]}'", "say": f"Unknown skill. Use one of: {names}."}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return skill, None, _help(skill, "args must be a JSON object")
    args = dict(args) if isinstance(args, dict) else {}
    wanted_action = key(action)
    if wanted_action in ("help", "info", "usage", "?"):
        return skill, None, _help(skill)
    by_name = {a["name"]: a for a in skill["actions"]}

    def find(text):
        k = key(text)
        return by_name.get(k) or next((a for a in skill["actions"] if k in a["aliases"]), None)

    chosen = find(wanted_action) if wanted_action else None
    if chosen is None and "action" in args:
        chosen = find(args.pop("action"))
    if chosen is None and not wanted_action and len(skill["actions"]) == 1:
        chosen = skill["actions"][0]
    if chosen is None:
        return skill, None, _help(skill, f"unknown action '{str(action)[:40]}' for {skill['name']}" if wanted_action else f"action is required for {skill['name']}")
    spec = {a["name"]: a for a in chosen["args"]}
    passthrough = (chosen.get("backend") or {}).get("type") == "ha_skill" and not spec
    data, bad = {}, []
    for raw_key, value in args.items():
        if passthrough:
            if value is None:
                continue
            data[str(raw_key)] = value
            continue
        k = key(raw_key)
        if k not in spec:
            k = next((a["name"] for a in chosen["args"] if k in a["aliases"] or k.replace("_", "") == a["name"].replace("_", "")), k)
        if k not in spec:
            bad.append(str(raw_key))
            continue
        if value is None:
            continue
        coerce = spec[k].get("coerce", "")
        if coerce == "json_string" and isinstance(value, (dict, list)):
            value = json.dumps(value, separators=(",", ":"))
        elif coerce == "day_list" and isinstance(value, str):
            value = [d.strip()[:3].lower() for d in re.split(r"[,\s]+", value) if d.strip()]
        elif coerce in ("integer", "number"):
            try:
                value = int(float(value)) if coerce == "integer" else float(value)
            except (TypeError, ValueError):
                bad.append(str(raw_key))
                continue
        data[k] = value
    missing = [a["name"] for a in chosen["args"] if a["required"] and data.get(a["name"]) in (None, "")]
    if bad or missing:
        parts = []
        if bad:
            parts.append("unknown args: " + ", ".join(bad))
        if missing:
            parts.append("missing: " + ", ".join(missing))
        return skill, None, _help(skill, f"{skill['name']} {chosen['name']}: {'; '.join(parts)}")
    guard = chosen.get("guard")
    if guard and re.search(guard["pattern"], str(data.get(guard["arg"]) or ""), re.IGNORECASE):
        return skill, None, {"ok": False, "error_kind": "guard", "say": guard["say"]}
    return skill, chosen, data


class SkillStore:
    def __init__(self, path: str | Path = "/data/skills.json") -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._mtime = None
        self._skills = load_seed()
        self._load()

    def _load(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return
        if stat.st_mtime == self._mtime:
            return
        try:
            self._skills = validate_skills(json.loads(self.path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            log.error("ignoring %s: %s", self.path, exc)
        self._mtime = stat.st_mtime

    @property
    def saved(self) -> bool:
        """False until skills were saved to /data (first run)."""
        return self.path.exists()

    def get(self) -> list[dict]:
        with self._lock:
            self._load()
            return copy.deepcopy(self._skills)

    def save(self, data: object) -> list[dict]:
        clean = validate_skills(data)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"skills": clean}, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            self._skills = clean
            self._mtime = self.path.stat().st_mtime
        log.info("relay skills saved (%d)", len(clean))
        return copy.deepcopy(clean)


_HELP_BRACE_RE = re.compile(
    r"([a-z][a-z0-9_]{0,31}(?:\s*,\s*[a-z][a-z0-9_]{0,31})*)\s*\{([^{}\n]{0,200})\}",
    re.I,
)


def parse_help_args(text: str, action: str) -> list[dict]:
    """Simple ``ask {request}`` / ``lookup {food, grams}`` lists from HA help or index.

    Every token inside the braces must be a plain name (``[a-z][a-z0-9_]{0,31}``).
    Anything with a colon, space, or other punctuation is skipped so a partial
    spec cannot reject real arguments; empty means pass-through.
    """
    wanted = key(action)
    if not text or not wanted:
        return []
    for match in _HELP_BRACE_RE.finditer(text):
        names = [key(part) for part in match.group(1).split(",") if key(part)]
        if wanted not in names:
            continue
        tokens = [part.strip() for part in match.group(2).split(",") if part.strip()]
        if not tokens:
            return []
        simple: list[str] = []
        for token in tokens:
            if not _NAME_RE.match(token):
                return []
            if token not in simple:
                simple.append(token)
        return [{"name": name, "required": False, "aliases": []} for name in simple]
    return []


def index_entries(description: str) -> dict[str, str]:
    """'Skills (name, action, args): page = ...; memory; game (watch). action=help ...' -> {name: entry}."""
    body = description.split(":", 1)[-1] if description.lower().startswith("skills") else description
    body = re.split(r"\.\s*action\s*=\s*help", body)[0]
    out = {}
    for part in body.split(";"):
        part = part.strip()
        match = re.match(r"([a-z][a-z0-9_]*)", part)
        if match:
            out[match.group(1)] = part
    return out


def merge_json(current: list[dict], incoming: object) -> tuple[list[dict], list[str]]:
    """Merge an exported skills file: same-named skills are replaced, new ones added."""
    new = validate_skills(incoming)
    skills = copy.deepcopy(current)
    notes = []
    for skill in new:
        for i, old in enumerate(skills):
            if old["name"] == skill["name"]:
                skills[i] = skill
                notes.append(f"replaced {skill['name']}")
                break
        else:
            skills.append(skill)
            notes.append(f"added {skill['name']}")
    return validate_skills(skills), notes


def merge_import(current: list[dict], ha_help: dict[str, dict], description: str = "") -> tuple[list[dict], list[str]]:
    """Fold HA's script.skill (action=help per skill) into the relay's skills.

    Help text from HA replaces the stored one; an action HA lists that the relay
    lacks is added as an ``ha_skill`` pass-through; a skill the relay lacks is added
    whole as pass-through. Nothing the relay already has is removed.
    """
    skills = copy.deepcopy(current)
    notes = []
    by_name = {s["name"]: s for s in skills}
    entries = index_entries(description)
    for name, info in ha_help.items():
        name = key(name)
        if not _NAME_RE.match(name) or not isinstance(info, dict):
            continue
        actions = [key(a) for a in info.get("actions") or [] if _NAME_RE.match(key(a))]
        skill = by_name.get(name)
        if skill is None:
            skill = {"name": name, "enabled": True, "direct": False, "index": entries.get(name, name), "help": "", "aliases": [], "actions": []}
            skills.append(skill)
            by_name[name] = skill
            notes.append(f"added skill {name}")
        if info.get("help"):
            if skill["help"] != info["help"]:
                notes.append(f"{name}: help updated from HA")
            skill["help"] = str(info["help"])
        have = {a["name"]: a for a in skill["actions"]}
        for action in actions:
            parsed = parse_help_args(str(info.get("help") or ""), action) or parse_help_args(entries.get(name, ""), action)
            existing = have.get(action)
            if existing is None:
                skill["actions"].append(
                    {"name": action, "aliases": [], "args": parsed, "backend": {"type": "ha_skill", "target": "", "fixed": {}}}
                )
                notes.append(f"{name}.{action}: added as pass-through to script.skill")
            elif existing["backend"]["type"] == "ha_skill" and not existing["args"] and parsed:
                existing["args"] = parsed
                notes.append(f"{name}.{action}: args from HA help")
    return validate_skills(skills), notes
