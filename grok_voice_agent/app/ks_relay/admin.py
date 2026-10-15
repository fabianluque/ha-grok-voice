"""Relay panel backend: one websocket of JSON commands behind HA ingress.

Commands (``op``): load, save_settings, save_skills, preview, import_skills,
clear_memory, status, set_secret, generate_secret. The xAI key is never sent.
The relay secret is never returned in full except once on generate.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.ks_relay.dates import DEFAULT_DATE_TEMPLATE
from app.ks_relay.settings_store import (
    DEFAULTS,
    TABLET_KEYS,
    TOOL_FAMILIES,
    SettingsError,
)
from app.ks_relay.skills import (
    BACKENDS,
    COERCE,
    RELAY_HANDLERS,
    SkillError,
    merge_import,
    merge_json,
    preview,
    validate_skills,
)

log = logging.getLogger("grok_voice.ks_relay.admin")
PAGE = Path(__file__).resolve().parent / "www" / "index.html"


def _tablets(context) -> list[dict]:
    return [
        {"device_id": t.device_id, "name": t.name, "area": t.area_name, "entity": t.satellite_entity}
        for t in context.ha.cache.tablets
    ]


def _scripts(context) -> list[dict]:
    out = []
    for entity_id, state in sorted(context.ha.cache.states.items()):
        if entity_id.startswith("script."):
            out.append({"id": entity_id, "name": (state.get("attributes") or {}).get("friendly_name") or entity_id})
    return out


def _relay_info(context) -> dict:
    listener = getattr(context, "listener", None)
    if listener is not None and hasattr(listener, "public_status"):
        return listener.public_status()
    return {"listening": False, "port": 8766, "secret_set": False, "reason": "no relay secret set"}


def _status(context) -> dict:
    memory = context.memory.items() if context.memory else []
    names = {t.device_id: t.name for t in context.ha.cache.tablets}
    for item in memory:
        item["tablet_name"] = names.get(item.get("tablet"), item.get("tablet"))
    try:
        stats = context.report()
    except Exception as exc:  # noqa: BLE001 - the panel still shows live status
        log.warning("stats report failed: %s", exc)
        stats = None
    return {
        "sessions": context.status.snapshot(),
        "memory": memory,
        "ha_ready": context.ha.ready,
        "stats": stats,
        "relay": _relay_info(context),
    }


async def _apply_secret(context, secret: str) -> dict:
    from app.ks_relay.addon import MIN_SECRET_LEN

    cleaned = (secret or "").strip()
    if len(cleaned) < MIN_SECRET_LEN:
        return {"errors": [f"relay secret must be at least {MIN_SECRET_LEN} characters"]}
    listener = getattr(context, "listener", None)
    if listener is None or not hasattr(listener, "apply_secret"):
        return {"errors": ["tablet listener is not available"]}
    status = await listener.apply_secret(cleaned)
    return {"relay": status, "secret_set": True}


async def _ha_skill_help(context) -> dict:
    """Ask HA's script.skill for each skill's help (read-only)."""
    description = context.ha.cache.skill_description
    names = re.findall(r"(?:^|;\s*|:\s*)([a-z]+)(?=[\s(=;.]|$)", description.split(":", 1)[-1])
    out = {}
    for name in dict.fromkeys(names):
        try:
            reply = await context.ha.call_service("script", "skill", {"name": name, "action": "help"}, return_response=True, timeout=20)
        except Exception as exc:  # noqa: BLE001
            log.warning("import: help for %s failed: %s", name, exc)
            continue
        if isinstance(reply, dict) and reply.get("actions"):
            out[name] = reply
    return out


async def handle(context, message: dict) -> dict:
    op = message.get("op")
    if op == "load":
        skills = context.skills.get()
        return {
            "settings": context.settings.get(),
            "defaults": DEFAULTS,
            "date_default": DEFAULT_DATE_TEMPLATE,
            "families": TOOL_FAMILIES,
            "tablet_keys": list(TABLET_KEYS),
            "skills": skills,
            "preview": preview(skills),
            "backends": list(BACKENDS),
            "relay_handlers": list(RELAY_HANDLERS),
            "coerce": list(COERCE),
            "tablets": _tablets(context),
            "scripts": _scripts(context),
            "services": context.ha.cache.services,
            "ha_skill_description": context.ha.cache.skill_description,
            "status": _status(context),
            "relay": _relay_info(context),
        }
    if op == "status":
        return {"status": _status(context)}
    if op == "save_settings":
        try:
            return {"settings": context.settings.save(message.get("settings"))}
        except SettingsError as exc:
            return {"errors": exc.errors}
    if op in ("save_skills", "preview"):
        try:
            skills = validate_skills(message.get("skills"))
        except SkillError as exc:
            return {"errors": exc.errors}
        if op == "save_skills":
            skills = context.skills.save(skills)
        return {"skills": skills, "preview": preview(skills)}
    if op == "import_skills":
        if not context.ha.cache.skill_available:
            return {"errors": ["script.skill is not in Home Assistant"]}
        merged, notes = merge_import(context.skills.get(), await _ha_skill_help(context), context.ha.cache.skill_description)
        return {"skills": merged, "preview": preview(merged), "notes": notes or ["nothing new in HA"]}
    if op == "import_json":
        try:
            merged, notes = merge_json(message.get("current") or context.skills.get(), message.get("skills"))
        except SkillError as exc:
            return {"errors": exc.errors}
        return {"skills": merged, "preview": preview(merged), "notes": notes}
    if op == "clear_memory":
        if context.memory is None:
            return {"cleared": 0}
        tablet = message.get("tablet")
        cleared = await context.memory.clear(str(tablet) if tablet else None)
        return {"cleared": cleared, "status": _status(context)}
    if op == "set_secret":
        return await _apply_secret(context, str(message.get("secret") or ""))
    if op == "generate_secret":
        generated = secrets.token_urlsafe(32)
        result = await _apply_secret(context, generated)
        if result.get("errors"):
            return result
        result["secret_once"] = generated
        return result
    return {"errors": [f"unknown op {op}"]}


async def first_run_import(context) -> None:
    """On first run (no /data/skills.json yet) pull HA's script.skill into the relay and save it."""
    if context.skills.saved or not context.ha.cache.skill_available:
        return
    try:
        merged, notes = merge_import(context.skills.get(), await _ha_skill_help(context), context.ha.cache.skill_description)
        context.skills.save(merged)
        log.info("first run: imported HA script.skill into the relay (%s)", "; ".join(notes) or "nothing new")
    except Exception as exc:  # noqa: BLE001 - the seed still works
        log.warning("first-run skill import failed: %s", exc)


async def admin_socket(websocket, context) -> None:
    try:
        async for raw in websocket:
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("not an object")
            except ValueError:
                await websocket.send(json.dumps({"errors": ["bad message"]}))
                continue
            try:
                reply = await handle(context, message)
            except Exception as exc:  # noqa: BLE001 - keep the panel alive
                log.exception("panel op %s failed", message.get("op"))
                reply = {"errors": [f"{type(exc).__name__}: {exc}"]}
            reply["id"] = message.get("id")
            await websocket.send(json.dumps(reply, default=str))
    except ConnectionClosed:
        return


async def serve_admin_standalone(context, host: str, port: int) -> None:
    """Panel without HA ingress, for local testing only (bind to 127.0.0.1)."""

    def process_request(connection, request):
        if request.path.split("?")[0].rstrip("/").endswith("/ws"):
            return None
        body = PAGE.read_bytes()
        response = connection.respond(HTTPStatus.OK, "")
        response.body = body
        del response.headers["Content-Type"]
        del response.headers["Content-Length"]
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["Content-Length"] = str(len(body))
        return response

    async with serve(lambda ws: admin_socket(ws, context), host, port, process_request=process_request) as server:
        log.info("relay panel (no auth) on http://%s:%s/relay/", host, port)
        await server.serve_forever()
