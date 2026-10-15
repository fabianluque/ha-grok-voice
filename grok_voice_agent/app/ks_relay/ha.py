"""One persistent Home Assistant link for the relay.

The websocket stays open for the life of the add-on and keeps a cache of the
registries, the entities exposed to Assist, their states, the time zone and the
``script.skill`` description, so nothing is fetched while a tablet waits.
Intents go to ``/api/intent/handle`` over a pooled HTTP client on the same host.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("grok_voice.ks_relay.ha")

REGISTRY_EVENTS = (
    "entity_registry_updated",
    "device_registry_updated",
    "area_registry_updated",
    "core_config_updated",
)


@dataclass(frozen=True)
class Tablet:
    """A voice satellite device (a Kiosk Satellite tablet, a Voice PE, ...)."""

    device_id: str
    satellite_entity: str
    name: str
    area_id: str = ""
    area_name: str = ""


@dataclass
class HaCache:
    states: dict[str, dict] = field(default_factory=dict)
    areas: dict[str, dict] = field(default_factory=dict)
    devices: dict[str, dict] = field(default_factory=dict)
    entities: dict[str, dict] = field(default_factory=dict)
    exposed: frozenset[str] = frozenset()
    tablets: tuple[Tablet, ...] = ()
    time_zone: str = "UTC"
    language: str = "en"
    skill_description: str = ""
    skill_available: bool = False
    services: dict[str, list[str]] = field(default_factory=dict)
    loaded_at: float = 0.0

    def entity_area_id(self, entity_id: str) -> str:
        entry = self.entities.get(entity_id) or {}
        if entry.get("area_id"):
            return entry["area_id"]
        device = self.devices.get(entry.get("device_id") or "") or {}
        return device.get("area_id") or ""

    def area_name(self, area_id: str) -> str:
        return str((self.areas.get(area_id) or {}).get("name") or "")


def _norm(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def build_tablets(cache: HaCache) -> tuple[Tablet, ...]:
    tablets = []
    for entity_id, entry in cache.entities.items():
        if not entity_id.startswith("assist_satellite.") or not entry.get("device_id"):
            continue
        device = cache.devices.get(entry["device_id"]) or {}
        area_id = entry.get("area_id") or device.get("area_id") or ""
        tablets.append(
            Tablet(
                device_id=entry["device_id"],
                satellite_entity=entity_id,
                name=str(device.get("name_by_user") or device.get("name") or ""),
                area_id=area_id,
                area_name=cache.area_name(area_id),
            )
        )
    return tuple(sorted(tablets, key=lambda t: t.name))


def find_tablet(cache: HaCache, *, device: str = "", name: str = "", area: str = "") -> Tablet | None:
    """Pick the tablet from an explicit id/name hint, then the KS device name, then a unique area."""
    for hint in (device, name):
        key = _norm(hint)
        if not key:
            continue
        for tablet in cache.tablets:
            if hint in (tablet.device_id, tablet.satellite_entity) or _norm(tablet.name) == key:
                return tablet
    key = _norm(area)
    if key:
        matches = [t for t in cache.tablets if _norm(t.area_name) == key or t.area_id == area]
        if len(matches) == 1:
            return matches[0]
    return None


class HaLink:
    """Persistent websocket plus pooled REST client to Home Assistant."""

    def __init__(self, ws_url: str, rest_url: str, token: str, *, http: httpx.AsyncClient | None = None) -> None:
        self.ws_url = ws_url
        self.rest_url = rest_url.rstrip("/")
        self._token = token
        self._http = http or httpx.AsyncClient(
            timeout=20, limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120)
        )
        self._warm_task: asyncio.Task | None = None
        self.cache = HaCache()
        self._ws = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._refresh_task: asyncio.Task | None = None
        self._closed = False

    # lifecycle -----------------------------------------------------------
    async def start(self, wait: float = 15.0) -> None:
        self._task = asyncio.create_task(self._run(), name="ha-link")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._ready.wait(), wait)
        self._warm_task = asyncio.create_task(self._keep_warm(), name="ha-http-warm")

    async def _keep_warm(self) -> None:
        """Keep a pooled HTTP connection open so an intent never pays for a handshake."""
        while True:
            with contextlib.suppress(Exception):
                await self._http.get(f"{self.rest_url}/api/", headers=self._headers())
            await asyncio.sleep(45)

    async def close(self) -> None:
        self._closed = True
        if self._warm_task:
            self._warm_task.cancel()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
        await self._http.aclose()

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _run(self) -> None:
        delay = 1.0
        while not self._closed:
            try:
                async with ws_connect(self.ws_url, max_size=None, compression=None, open_timeout=15) as ws:
                    first = json.loads(await ws.recv())
                    if first.get("type") != "auth_required":
                        raise RuntimeError(f"unexpected HA hello {first.get('type')}")
                    await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
                    reply = json.loads(await ws.recv())
                    if reply.get("type") != "auth_ok":
                        raise RuntimeError("Home Assistant refused the token")
                    self._ws = ws
                    reader = asyncio.create_task(self._read(ws))
                    try:
                        for event in ("state_changed", *REGISTRY_EVENTS):
                            await self.call({"type": "subscribe_events", "event_type": event})
                        await self.refresh()
                        delay = 1.0
                        log.info(
                            "HA link ready: %s states, %s exposed, %s satellites, skill=%s",
                            len(self.cache.states),
                            len(self.cache.exposed),
                            len(self.cache.tablets),
                            self.cache.skill_available,
                        )
                        self._ready.set()
                        await reader
                    finally:
                        reader.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on anything
                log.warning("HA link down (%s: %s); retrying in %.0fs", type(exc).__name__, exc, delay)
            finally:
                self._ws = None
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("HA link closed"))
                self._pending.clear()
            if self._closed:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _read(self, ws) -> None:
        try:
            async for raw in ws:
                message = json.loads(raw)
                messages = message if isinstance(message, list) else [message]
                for item in messages:
                    self._dispatch(item)
        except ConnectionClosed:
            return

    def _dispatch(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "result":
            future = self._pending.pop(message.get("id"), None)
            if future and not future.done():
                if message.get("success"):
                    future.set_result(message.get("result"))
                else:
                    error = message.get("error") or {}
                    future.set_exception(RuntimeError(f"{error.get('code')}: {error.get('message')}"))
            return
        if kind != "event":
            return
        event = message.get("event") or {}
        name = event.get("event_type")
        if name == "state_changed":
            data = event.get("data") or {}
            entity_id = data.get("entity_id")
            new_state = data.get("new_state")
            if entity_id:
                if new_state is None:
                    self.cache.states.pop(entity_id, None)
                else:
                    self.cache.states[entity_id] = new_state
        elif name in REGISTRY_EVENTS:
            self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            return

        async def later():
            await asyncio.sleep(2.0)
            with contextlib.suppress(Exception):
                await self.refresh()

        self._refresh_task = asyncio.create_task(later())

    # requests ------------------------------------------------------------
    async def call(self, message: dict, timeout: float = 15.0):
        ws = self._ws
        if ws is None:
            raise ConnectionError("HA link is not connected")
        self._next_id += 1
        ident = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[ident] = future
        await ws.send(json.dumps({**message, "id": ident}))
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(ident, None)

    async def refresh(self) -> None:
        states, areas, devices, entities, exposed, config, services = await asyncio.gather(
            self.call({"type": "get_states"}),
            self.call({"type": "config/area_registry/list"}),
            self.call({"type": "config/device_registry/list"}),
            self.call({"type": "config/entity_registry/list"}),
            self.call({"type": "homeassistant/expose_entity/list"}),
            self.call({"type": "get_config"}),
            self.call({"type": "get_services"}),
        )
        cache = HaCache(
            states={s["entity_id"]: s for s in states},
            areas={a["area_id"]: a for a in areas},
            devices={d["id"]: d for d in devices},
            entities={e["entity_id"]: e for e in entities},
            exposed=frozenset(
                entity_id
                for entity_id, assistants in (exposed or {}).get("exposed_entities", {}).items()
                if (assistants or {}).get("conversation")
            ),
            time_zone=str(config.get("time_zone") or "UTC"),
            language=str(config.get("language") or "en"),
            loaded_at=time.time(),
        )
        cache.services = {domain: sorted(items) for domain, items in sorted((services or {}).items())}
        skill = ((services or {}).get("script") or {}).get("skill")
        if skill:
            cache.skill_available = True
            cache.skill_description = str(skill.get("description") or "")
        cache.tablets = build_tablets(cache)
        self.cache = cache

    async def intent(self, name: str, data: dict, *, device_id: str = "", satellite_id: str = "") -> dict:
        body: dict = {"name": name, "data": data, "assistant": "conversation", "language": self.cache.language}
        if device_id:
            body["device_id"] = device_id
        if satellite_id:
            body["satellite_id"] = satellite_id
        response = await self._http.post(f"{self.rest_url}/api/intent/handle", json=body, headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def set_state(self, entity_id: str, state: object, attributes: dict) -> None:
        """POST /api/states/<entity_id> (a state-only entity, used for the relay's stats sensors)."""
        response = await self._http.post(
            f"{self.rest_url}/api/states/{entity_id}", json={"state": state, "attributes": attributes}, headers=self._headers()
        )
        response.raise_for_status()

    async def call_service(
        self,
        domain: str,
        service: str,
        data: dict | None = None,
        *,
        target: dict | None = None,
        return_response: bool = False,
        timeout: float = 60.0,
    ):
        message: dict = {"type": "call_service", "domain": domain, "service": service, "service_data": data or {}}
        if target:
            message["target"] = target
        if return_response:
            message["return_response"] = True
        result = await self.call(message, timeout=timeout)
        return (result or {}).get("response") if return_response else result
