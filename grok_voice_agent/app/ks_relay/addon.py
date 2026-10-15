"""Run the KS relay inside the add-on, next to the browser voice server."""

from __future__ import annotations

import logging

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
DATA = "/data"


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


async def run_relay(settings, context: RelayContext) -> None:
    from app.ks_relay.admin import first_run_import

    await context.ha.start()
    await first_run_import(context)
    try:
        await serve_relay(
            RelaySettings(xai_api_key=settings.xai_api_key, secret=settings.ks_relay_secret, port=settings.ks_relay_port),
            context,
        )
    finally:
        await context.ha.close()
