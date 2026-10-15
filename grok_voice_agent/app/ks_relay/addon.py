"""Run the KS relay inside the add-on, next to the browser voice server.

The ingress panel always has a RelayContext. Only the tablet listener on
``ks_relay_port`` (default 8766) depends on the relay secret; it can be
started later from the panel without restarting the add-on.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

import httpx

from app.ks_relay.ha import HaLink
from app.ks_relay.memory import RelayMemory
from app.ks_relay.relay import RelayContext, RelaySettings, serve_relay
from app.ks_relay.settings_store import SettingsStore
from app.ks_relay.skills import SkillStore
from app.ks_relay.stats import StatsStore

log = logging.getLogger("grok_voice.ks_relay")

SUPERVISOR_WS = "ws://supervisor/core/websocket"
SUPERVISOR_REST = "http://supervisor/core"
LOCAL_WS = "ws://127.0.0.1:8123/api/websocket"
LOCAL_REST = "http://127.0.0.1:8123"
SUPERVISOR_OPTIONS_URL = "http://supervisor/addons/self/options"
DATA = "/data"
OFF_REASON = "no relay secret set"
MIN_SECRET_LEN = 12


def ha_endpoints(settings) -> tuple[str, str]:
    """A long-lived token talks to Core on the host; the add-on token goes through Supervisor."""
    if settings.mcp_token_source == "long-lived":
        return LOCAL_WS, LOCAL_REST
    return SUPERVISOR_WS, SUPERVISOR_REST


def build_context(settings, data_dir: str = DATA) -> RelayContext:
    ws_url, rest_url = ha_endpoints(settings)
    return RelayContext(
        ha=HaLink(ws_url, rest_url, settings.mcp_token),
        settings=SettingsStore(f"{data_dir}/settings.json"),
        skills=SkillStore(f"{data_dir}/skills.json"),
        memory=RelayMemory(f"{data_dir}/ks_relay_memory.json", settings.xai_api_key),
        stats=StatsStore(f"{data_dir}/stats.db"),
    )


def persist_relay_secret(
    secret: str,
    options_path: str | Path,
    token: str | None = None,
    http: httpx.Client | None = None,
) -> None:
    """Write ks_relay_secret into options.json and, when possible, Supervisor.

    Supervisor's options POST replaces the stored options object, so this
    only calls Supervisor when the local file already looks like a full
    add-on options dict. The secret is never written to the log.
    """
    from app.config import supervisor_access_token

    path = Path(options_path)
    try:
        options = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        options = {}
    if not isinstance(options, dict):
        options = {}
    options["ks_relay_secret"] = secret
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(options, indent=2) + "\n", encoding="utf-8")
    if token is None:
        token = supervisor_access_token()
    if not token:
        return
    if "xai_api_key" not in options and "instructions" not in options:
        log.warning("not updating Supervisor options: local options.json looks incomplete")
        return
    own_client = http is None
    client = http or httpx.Client(timeout=5)
    try:
        response = client.post(
            SUPERVISOR_OPTIONS_URL,
            json={"options": options},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code >= 400:
            log.warning("Supervisor options update returned %s", response.status_code)
    except Exception as exc:  # noqa: BLE001 - panel save must not fail closed on Supervisor
        log.warning("could not update Supervisor add-on options: %s", type(exc).__name__)
    finally:
        if own_client:
            client.close()


class RelayListener:
    """Tablet WebSocket on ks_relay_port. Not required for the ingress panel."""

    def __init__(
        self,
        settings,
        context: RelayContext,
        *,
        redact_secrets: list[str] | None = None,
        options_path: str = "/data/options.json",
        host: str = "0.0.0.0",
    ) -> None:
        self.xai_api_key = settings.xai_api_key
        self.port = int(settings.ks_relay_port or 8766)
        self.host = host
        self.context = context
        self.secret = settings.ks_relay_secret or ""
        self.options_path = options_path
        self.redact_secrets = redact_secrets if redact_secrets is not None else []
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.listening = False
        self.off_reason = "" if self.secret else OFF_REASON

    def public_status(self) -> dict:
        if self.listening:
            return {"listening": True, "port": self.port, "secret_set": True, "reason": ""}
        reason = self.off_reason or (OFF_REASON if not self.secret else "tablet listener is not running")
        return {"listening": False, "port": self.port, "secret_set": bool(self.secret), "reason": reason}

    def _remember_secret(self, secret: str) -> None:
        self.secret = secret
        if secret and secret not in self.redact_secrets:
            self.redact_secrets.append(secret)

    async def _start_server(self) -> None:
        if not self.secret:
            self.listening = False
            self.off_reason = OFF_REASON
            return
        relay = RelaySettings(
            xai_api_key=self.xai_api_key,
            secret=self.secret,
            port=self.port,
            host=self.host,
        )
        self._task = asyncio.create_task(serve_relay(relay, self.context), name="ks-relay-listen")
        await asyncio.sleep(0)
        if self._task.done():
            exc = self._task.exception()
            self.listening = False
            self.off_reason = f"listener failed to start: {type(exc).__name__}" if exc else "listener exited"
            log.error("KS relay listener failed: %s", self.off_reason)
            return
        self.listening = True
        self.off_reason = ""
        log.info("Kiosk Satellite relay on port %s", self.port)

    async def _stop_server(self) -> None:
        task = self._task
        self._task = None
        self.listening = False
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def apply_secret(self, secret: str) -> dict:
        cleaned = (secret or "").strip()
        async with self._lock:
            await self._stop_server()
            self._remember_secret(cleaned)
            persist_relay_secret(cleaned, self.options_path)
            if cleaned:
                await self._start_server()
            else:
                self.off_reason = OFF_REASON
                log.info("Kiosk Satellite relay off (%s)", OFF_REASON)
            return self.public_status()

    async def run_forever(self) -> None:
        if self.secret:
            await self._start_server()
        else:
            log.info("Kiosk Satellite relay off (%s)", OFF_REASON)
        try:
            await asyncio.Future()
        finally:
            await self._stop_server()


async def run_addon(
    settings,
    context: RelayContext,
    *,
    redact_secrets: list[str] | None = None,
    options_path: str = "/data/options.json",
) -> None:
    """HA cache + optional 8766 listener. The ingress panel uses ``context`` either way."""
    from app.ks_relay.admin import first_run_import

    listener = RelayListener(
        settings,
        context,
        redact_secrets=redact_secrets,
        options_path=options_path,
    )
    context.listener = listener
    await context.ha.start()
    await first_run_import(context)
    try:
        await listener.run_forever()
    finally:
        await context.ha.close()


async def run_relay(settings, context: RelayContext) -> None:
    """Back-compat wrapper used by tests and ``python -m app.ks_relay``."""
    await run_addon(settings, context)
