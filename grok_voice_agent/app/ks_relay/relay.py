"""OpenAI-realtime-protocol relay for Kiosk Satellite's xAI provider.

Kiosk Satellite (KS) points its xAI **Endpoint** here and puts a shared secret in
its API key field. For each KS socket the relay:

* opens the xAI socket at once, in parallel with everything else;
* rewrites KS's ``session.update``: keeps KS's instructions (location, history),
  sets ``reasoning.effort``, drops ``silence_duration_ms``/``prefix_padding_ms``
  and replaces the function tools with the relay's own set (KS's
  ``end_conversation`` and xAI's provider search tools pass through);
* runs calls to its own tools here, against Home Assistant, and hides them from
  KS: KS only sees the spoken answer. ``end_conversation`` still reaches KS.

While a relay tool runs, the ``response.done`` of the answer that called it is
held back, so KS keeps "responding" and its idle countdown does not run.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.auth import peer_host, redact
from app.ks_relay.dates import annotate_dates, clock_time, date_line  # noqa: F401 - clock_time re-exported
from app.ks_relay.ha import HaLink, identify_tablet, usable_ipv4
from app.ks_relay.memory import (
    MemorySettings,
    RelayMemory,
    SessionLog,
    strip_ks_history,
)
from app.ks_relay.settings_store import SettingsStore
from app.ks_relay.skills import SkillStore, skill_specs
from app.ks_relay.stats import StatsStore, sensor_states
from app.ks_relay.status import RelayStatus
from app.ks_relay.tools import END_CONVERSATION, RelayTools, run_tool, tool_specs

log = logging.getLogger("grok_voice.ks_relay")

XAI_REALTIME_URL = "wss://api.x.ai/v1/realtime"
_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_DEVICE_RE = re.compile(r'You run on a device named "([^"]+)"')
_AREA_RE = re.compile(r"The device is in the (.+?) area\.")
HELD_DONE_LIMIT = 45.0
CLOCK_REFRESH_SECONDS = 600.0
_DATETIME_TOOL_RE = re.compile(
    r"(?:^|(?<=[.!?\n]))[ \t]*[^.!?\n]*\b(?:GetDateTime|get_datetime|HassGetCurrentTime|HassGetCurrentDate)\b[^.!?\n]*[.!?]?"
)


def clock_line(time_zone: str, now: datetime | None = None, template: str = "") -> str:
    """The date line (settings: date_template; default is the measured winner in dates.py)."""
    return date_line(time_zone, template, now)


def strip_datetime_tool_hints(instructions: str) -> str:
    """Drop sentences telling the model to call a date/time tool: the relay puts the time in the prompt."""
    return _DATETIME_TOOL_RE.sub("", instructions or "").strip()


@dataclass(frozen=True)
class RelaySettings:
    """Bootstrap only; everything else is in the panel's settings (settings_store)."""

    xai_api_key: str
    secret: str
    port: int = 8766
    host: str = "0.0.0.0"
    upstream_url: str = XAI_REALTIME_URL
    tool_timeout: float = 30.0
    dry_run: bool = False  # tests: tools answer without touching Home Assistant
    dry_run_delay: float = 0.0


@dataclass
class RelayContext:
    """What every session shares: the HA link, the stores, memory and status."""

    ha: HaLink
    settings: SettingsStore = field(default_factory=lambda: SettingsStore("./settings.json"))
    skills: SkillStore = field(default_factory=lambda: SkillStore("./skills.json"))
    memory: RelayMemory | None = None
    status: RelayStatus = field(default_factory=RelayStatus)
    stats: StatsStore | None = None
    listener: object | None = None

    def __post_init__(self) -> None:
        if self.memory is not None and self.stats is not None:
            self.memory.on_usage = self._summary_usage

    def _summary_usage(self, tablet: str, model: str, input_tokens: int, output_tokens: int) -> None:
        assert self.stats is not None
        self.stats.record_summary(tablet, model, input_tokens, output_tokens, self.settings.get().get("costs") or {})
        with contextlib.suppress(RuntimeError):
            background(publish_sensors(self))

    def report(self) -> dict | None:
        if self.stats is None:
            return None
        names = {t.device_id: t.name for t in self.ha.cache.tablets}
        return self.stats.report(self.ha.cache.time_zone, names=names)


_BACKGROUND: set[asyncio.Task] = set()


def background(coro) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


async def record_session(context: RelayContext, entry: dict | None) -> None:
    """Persist a finished session's stats (never on the hot path) and refresh the HA sensors."""
    if context.stats is None or not entry:
        return
    try:
        await asyncio.to_thread(context.stats.record_session, entry, context.settings.get().get("costs") or {})
    except Exception as exc:  # noqa: BLE001 - stats must never break a session
        log.warning("session stats not recorded: %s", exc)
        return
    await publish_sensors(context)


async def publish_sensors(context: RelayContext) -> None:
    """Optional sensor.grok_relay_* states in HA (settings: ha_sensors)."""
    if context.stats is None or not context.settings.get().get("ha_sensors"):
        return
    try:
        today = (await asyncio.to_thread(context.report))["totals"]["today"]
        for entity_id, state, attrs in sensor_states(today):
            await context.ha.set_state(entity_id, state, attrs)
    except Exception as exc:  # noqa: BLE001
        log.warning("relay sensors not updated: %s", exc)


def bearer_ok(header: str | None, secret: str) -> bool:
    if not secret:
        return False
    given = (header or "").strip()
    if given.lower().startswith("bearer "):
        given = given[7:].strip()
    return hmac.compare_digest(given.encode(), secret.encode())


def rewrite_session(session: dict, cfg: dict, tools: list[dict], *, extra_instructions: str = "") -> dict:
    """KS's session.update session -> the one sent to xAI, per the panel settings ``cfg``."""
    out = dict(session or {})
    detection = dict(out.get("turn_detection") or {"type": "server_vad"})
    vad = cfg.get("vad") or {"mode": "unset"}
    if detection.get("type") in (None, "server_vad") and vad.get("mode") != "kiosk":
        kept = {k: v for k, v in detection.items() if k in ("type", "idle_timeout_ms")}
        kept["type"] = "server_vad"
        if vad.get("mode") == "custom":
            kept["silence_duration_ms"] = int(vad.get("silence_ms", 500))
        out["turn_detection"] = kept
    effort = cfg.get("reasoning_effort", "none")
    if effort in ("none", "high"):
        out["reasoning"] = {"effort": effort}
    else:
        out.pop("reasoning", None)
    passthrough = [
        tool
        for tool in out.get("tools") or []
        if isinstance(tool, dict) and (tool.get("type") != "function" or tool.get("name") == END_CONVERSATION)
    ]
    out["tools"] = [*tools, *passthrough]
    instructions = str(out.get("instructions") or "").strip()
    if cfg.get("inject_datetime", True):
        instructions = strip_datetime_tool_hints(instructions)
    added = "\n".join(line for line in (cfg.get("extra_instructions") or "", extra_instructions) if line)
    out["instructions"] = f"{instructions}\n\n{added}".strip() if added else instructions
    return out


def _new_entry() -> dict:
    return {"ours": [], "ks": False, "end": False}


class RelaySession:
    def __init__(self, ks, settings: RelaySettings, context: RelayContext, *, query: dict[str, list[str]]) -> None:
        self.ks = ks
        self.context = context
        ha = context.ha
        self.memory = context.memory
        self.cfg: dict = context.settings.get()
        self.status_id = context.status.open(str((getattr(ks, "remote_address", None) or ("?",))[0]))
        self.record: SessionLog | None = None
        self.settings = settings
        self.ha = ha
        self.query = query
        self.upstream = None
        self.tools: RelayTools | None = None
        self.tool_names = frozenset()
        self.t0 = time.monotonic()
        self.hidden_items: set[str] = set()
        self.current: dict | None = None  # calls of the answer in progress
        self.held_done: dict | None = None
        self.held_since = 0.0
        self.active_response = False
        self.speech_stopped_at: float | None = None
        self.first_audio_logged = True
        self.label = "?"
        self.pending_create_from_relay = False
        self.our_call_ids: set[str] = set()
        self.instructions_base: str | None = None
        self.clock_text = ""
        self.clock_at = 0.0
        self.clock_day = None
        self.hide_session_updated = 0

    def ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def _model(self) -> str:
        if self.cfg.get("model"):
            return self.cfg["model"]
        asked = (self.query.get("model") or [""])[0]
        return asked if _MODEL_RE.match(asked or "") else "grok-voice-latest"

    async def _open_upstream(self):
        url = f"{self.settings.upstream_url}?model={self._model()}"
        socket = await ws_connect(
            url,
            additional_headers={"Authorization": f"Bearer {self.settings.xai_api_key}"},
            max_size=None,
            compression=None,
            open_timeout=10,
        )
        log.info("[%s] upstream open %dms", self.label, self.ms())
        return socket

    async def run(self) -> None:
        upstream_task = asyncio.create_task(self._open_upstream())
        try:
            ks_task = asyncio.create_task(self._pump_ks(upstream_task))
            try:
                self.upstream = await upstream_task
            except Exception as exc:  # noqa: BLE001
                log.error("upstream connect failed: %s", type(exc).__name__)
                ks_task.cancel()
                await self._ks_send({"type": "error", "error": {"code": "relay_upstream", "message": "relay could not reach xAI"}})
                return
            up_task = asyncio.create_task(self._pump_upstream())
            clock_task = asyncio.create_task(self._keep_clock())
            done, pending = await asyncio.wait({ks_task, up_task}, return_when=asyncio.FIRST_COMPLETED)
            clock_task.cancel()
            for task in pending:
                task.cancel()
            for task in done:
                if task.exception() and not isinstance(task.exception(), ConnectionClosed):
                    log.warning("[%s] pump ended: %r", self.label, task.exception())
        finally:
            if self.upstream is not None:
                with contextlib.suppress(Exception):
                    await self.upstream.close()
            elif not upstream_task.done():
                upstream_task.cancel()
            log.info("[%s] session closed after %dms", self.label, self.ms())
            finished = self.context.status.close(self.status_id)
            if finished is not None:
                background(record_session(self.context, finished))
            if self.memory is not None and self.record is not None:
                self.memory.finish(self.record, MemorySettings.from_dict(self.cfg.get("memory")))

    # KS -> xAI ------------------------------------------------------------
    async def _pump_ks(self, upstream_task: asyncio.Task) -> None:
        async for raw in self.ks:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = message.get("type")
            if kind == "session.update":
                message = {**message, "session": self._session_for_xai(message.get("session") or {})}
                raw = json.dumps(message)
                log.info("[%s] session.update from KS %dms, %d tools", self.label, self.ms(), len(message["session"]["tools"]))
            upstream = self.upstream or await upstream_task
            await upstream.send(raw)

    def _remote_ip(self) -> str:
        return usable_ipv4(peer_host(getattr(self.ks, "remote_address", None)))

    def _session_for_xai(self, session: dict) -> dict:
        instructions = str(session.get("instructions") or "")
        device_hint = (self.query.get("device") or [""])[0]
        named = _DEVICE_RE.search(instructions)
        area = _AREA_RE.search(instructions)
        remote_ip = self._remote_ip()
        house = self.context.settings.get()
        tablet, method = identify_tablet(
            self.ha.cache,
            device=device_hint,
            name=named.group(1) if named else "",
            area=area.group(1) if area else "",
            remote_ip=remote_ip,
            ip_tablets=house.get("ip_tablets") or {},
        )
        snippet = redact(instructions[:200].replace("\n", " "), [self.settings.xai_api_key, self.settings.secret])
        log.debug("[%s] KS instructions: %s", remote_ip or "?", snippet)
        if tablet:
            log.info("[%s] identified via %s from %s", tablet.name, method, remote_ip or "-")
            self.label = tablet.name
        else:
            self.label = f"unknown tablet ({remote_ip})" if remote_ip else "unknown tablet"
            log.info("[%s] unmatched from %s", self.label, remote_ip or "-")
        self.cfg = self.context.settings.for_tablet(tablet.device_id if tablet else "")
        skills = self.context.skills.get()
        self.tools = RelayTools(
            self.ha, tablet, skills=skills, dry_run=self.settings.dry_run, dry_run_delay=self.settings.dry_run_delay
        )
        memory_key = tablet.device_id if tablet else self.label
        self.context.status.update(self.status_id, tablet=self.label, device_id=tablet.device_id if tablet else "")
        self.record = SessionLog(tablet=memory_key, area=tablet.area_name if tablet else (area.group(1) if area else ""))
        memory_cfg = MemorySettings.from_dict(self.cfg.get("memory"))
        extra = []
        if self.memory is not None and memory_cfg.active:
            if memory_cfg.strip_ks_history:
                session = {**session, "instructions": strip_ks_history(instructions)}
            block = self.memory.context_block(memory_key, memory_cfg, self.ha.cache.time_zone)
            if block:
                extra.append(block)
        if tablet and tablet.area_name and not area:
            extra.append(f"The device is in the {tablet.area_name} area. When the user doesn't name an area, use this one.")
        self.clock_at = time.monotonic()
        self.clock_day = self._today()
        if self.cfg.get("inject_datetime", True):
            self.clock_text = clock_line(self.ha.cache.time_zone, template=self.cfg.get("date_template", ""))
            extra.append(self.clock_text)
        specs = [*tool_specs(self.cfg.get("tools")), *skill_specs(skills)]
        self.tool_names = frozenset(spec["name"] for spec in specs)
        rewritten = rewrite_session(session, self.cfg, specs, extra_instructions="\n".join(extra))
        if self.cfg.get("inject_datetime", True):
            text = rewritten["instructions"]
            self.instructions_base = text[: -len(self.clock_text)].rstrip() if text.endswith(self.clock_text) else text
        return rewritten

    def _today(self):
        return datetime.now(ZoneInfo(self.ha.cache.time_zone or "UTC")).date()

    async def _keep_clock(self) -> None:
        """Re-send the time when the session crosses midnight or runs past ~10 minutes."""
        while True:
            await asyncio.sleep(30)
            if self.instructions_base is None or self.upstream is None:
                continue
            stale = time.monotonic() - self.clock_at > CLOCK_REFRESH_SECONDS
            if not stale and self._today() == self.clock_day:
                continue
            self.clock_at = time.monotonic()
            self.clock_day = self._today()
            self.hide_session_updated += 1
            self.clock_text = clock_line(self.ha.cache.time_zone, template=self.cfg.get("date_template", ""))
            instructions = f"{self.instructions_base}\n{self.clock_text}"
            await self.upstream.send(json.dumps({"type": "session.update", "session": {"instructions": instructions}}))
            log.info("[%s] clock refreshed in instructions", self.label)

    # xAI -> KS ------------------------------------------------------------
    async def _ks_send(self, event: dict | str) -> None:
        with contextlib.suppress(ConnectionClosed):
            await self.ks.send(event if isinstance(event, str) else json.dumps(event))

    def _ours(self, name: object) -> bool:
        return isinstance(name, str) and name in self.tool_names

    async def _pump_upstream(self) -> None:
        async for raw in self.upstream:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            forward = await self._on_upstream(event)
            if log.isEnabledFor(logging.DEBUG) and not str(event.get("type", "")).endswith(".delta"):
                log.debug("[%s] %6dms %s %s", self.label, self.ms(), event.get("type"), "fwd" if forward else "hidden")
            if forward is True:
                await self._ks_send(raw)
            elif isinstance(forward, dict):
                await self._ks_send(forward)
            self._check_held()

    async def _on_upstream(self, event: dict):
        kind = event.get("type", "")
        if kind == "session.updated":
            if self.hide_session_updated:
                self.hide_session_updated -= 1
                return False
            log.info("[%s] session.updated %dms", self.label, self.ms())
            self.context.status.update(self.status_id, ready_ms=self.ms())
            return True
        if self.record is not None:
            if kind == "conversation.item.input_audio_transcription.completed":
                self.record.said("user", str(event.get("transcript") or ""))
            elif kind in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
                self.record.said("assistant", str(event.get("transcript") or ""))
        if kind == "response.done":
            self.context.status.usage(self.status_id, (event.get("response") or {}).get("usage"))
        if kind == "input_audio_buffer.speech_stopped":
            self.speech_stopped_at = time.monotonic()
            self.first_audio_logged = False
            return True
        if kind in ("response.output_audio.delta", "response.audio.delta") and not self.first_audio_logged:
            self.first_audio_logged = True
            if self.speech_stopped_at:
                latency = int((time.monotonic() - self.speech_stopped_at) * 1000)
                log.info("[%s] speech_stopped -> first audio %dms", self.label, latency)
                self.context.status.turn(self.status_id, latency)
            return True
        if kind == "response.created":
            self.active_response = True
            self.current = _new_entry()
            if self.held_done is not None:
                self.held_done = None  # the follow-up answer continues the held one
                self.pending_create_from_relay = False
            return True
        if kind == "error":
            code = str((event.get("error") or {}).get("code") or "")
            if self.pending_create_from_relay and "active_response" in code:
                self.pending_create_from_relay = False
                return False
            if self.held_done is not None:
                await self._release_held()
            return True

        item = event.get("item") if isinstance(event.get("item"), dict) else None
        entry = self.current if self.current is not None else _new_entry()
        self.current = entry
        if kind == "response.output_item.added" and item and item.get("type") == "function_call":
            if self._ours(item.get("name")):
                self.hidden_items.add(str(item.get("id")))
                return False
            entry["ks"] = True
            if item.get("name") == END_CONVERSATION:
                entry["end"] = True
            return True
        if kind == "response.function_call_arguments.delta" and str(event.get("item_id")) in self.hidden_items:
            return False
        if kind == "response.function_call_arguments.done":
            if not self._ours(event.get("name")):
                if event.get("name") == END_CONVERSATION:
                    entry["ks"] = entry["end"] = True
                elif event.get("name"):
                    entry["ks"] = True
                return True
            if event.get("item_id"):
                self.hidden_items.add(str(event["item_id"]))
            entry["ours"].append(asyncio.create_task(self._run_tool(event)))
            return False
        if kind.startswith(("response.output_item.", "conversation.item.")) and item and (
            str(item.get("id")) in self.hidden_items
            or (item.get("type") in ("function_call", "function_call_output") and self._ours(item.get("name")))
        ):
            return False
        if kind.startswith("conversation.item.") and str(event.get("item_id")) in self.hidden_items:
            return False
        if kind.startswith("conversation.item.") and item and item.get("type") == "function_call_output":
            if str(item.get("call_id")) in self.our_call_ids:
                return False
        if kind == "response.done":
            self.active_response = False
            self.current = None
            if not entry["ours"]:
                return True
            response = event.get("response") or {}
            output = [
                o
                for o in response.get("output") or []
                if not (isinstance(o, dict) and (str(o.get("id")) in self.hidden_items or self._ours(o.get("name"))))
            ]
            cleaned = {**event, "response": {**response, "output": output}}
            asyncio.create_task(self._answer(entry))
            if entry["ks"]:
                return cleaned  # KS has its own call (end_conversation) in this answer
            self.held_done = cleaned
            self.held_since = time.monotonic()
            return False
        return True

    async def _run_tool(self, event: dict) -> dict:
        assert self.tools is not None
        name = str(event.get("name"))
        self.our_call_ids.add(str(event.get("call_id")))
        started = time.monotonic()
        output, ok = await run_tool(self.tools, name, str(event.get("arguments") or ""), self.settings.tool_timeout)
        if self.record is not None:
            self.record.tool(name, str(event.get("arguments") or ""), output, ok)
        if self.cfg.get("annotate_tool_dates", True):
            output = annotate_dates(output, self.ha.cache.time_zone)
        self.context.status.tool(self.status_id, name, ok, int((time.monotonic() - started) * 1000))
        log.info(
            "[%s] tool %s %s %dms args=%s",
            self.label,
            name,
            "ok" if ok else "failed",
            (time.monotonic() - started) * 1000,
            str(event.get("arguments") or "")[:200],
        )
        return {
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": event.get("call_id"), "output": output},
        }

    async def _answer(self, entry: dict) -> None:
        results = await asyncio.gather(*entry["ours"], return_exceptions=True)
        for result in results:
            if isinstance(result, dict):
                await self.upstream.send(json.dumps(result))
        if entry["end"]:
            return  # KS ends the conversation; no spoken follow-up
        if entry["ks"]:
            return  # KS sends response.create once its own output is in
        self.pending_create_from_relay = True
        await self.upstream.send(json.dumps({"type": "response.create"}))

    async def _release_held(self) -> None:
        held, self.held_done = self.held_done, None
        if held is not None:
            await self._ks_send(held)

    def _check_held(self) -> None:
        if self.held_done is not None and time.monotonic() - self.held_since > HELD_DONE_LIMIT:
            asyncio.create_task(self._release_held())


async def serve_relay(settings: RelaySettings, context: RelayContext) -> None:
    ha = context.ha
    if not settings.secret:
        log.error("KS relay secret is empty; refusing every connection until it is set")
    if not settings.xai_api_key:
        log.error("xAI API key is empty; the KS relay cannot reach xAI")

    def process_request(connection, request):
        path = urlsplit(request.path).path
        if path in ("/health", "/healthz"):
            return connection.respond(HTTPStatus.OK, "ok\n" if ha.ready else "ha not ready\n")
        if not bearer_ok(request.headers.get("Authorization"), settings.secret):
            log.warning("KS relay rejected a connection from %s", getattr(connection, "remote_address", ("?",))[0])
            return connection.respond(HTTPStatus.UNAUTHORIZED, "unauthorized\n")
        return None

    async def handler(ks):
        query = parse_qs(urlsplit(ks.request.path).query)
        session = RelaySession(ks, settings, context, query=query)
        log.info("KS connected from %s", (ks.remote_address or ("?",))[0])
        await session.run()

    async with serve(
        handler,
        settings.host,
        settings.port,
        process_request=process_request,
        max_size=16 * 1024 * 1024,
        compression=None,
        ping_interval=20,
    ) as server:
        log.info("KS relay listening on %s:%s", settings.host, settings.port)
        await server.serve_forever()
