"""Standalone relay for testing off the add-on (a Mac mini, a dev box).

Environment: XAI_API_KEY, KS_RELAY_SECRET or KS_RELAY_SECRET_FILE, HA_URL
(e.g. http://homeassistant.local:8123), HA_TOKEN or HA_TOKEN_FILE, and optional
KS_RELAY_PORT, KS_RELAY_HOST, KS_RELAY_DATA (settings/skills/memory dir),
KS_RELAY_ADMIN_PORT (panel on 127.0.0.1, no auth), KS_RELAY_DRY_RUN=1.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from app.ks_relay.admin import serve_admin_standalone
from app.ks_relay.ha import HaLink
from app.ks_relay.memory import RelayMemory
from app.ks_relay.relay import RelayContext, RelaySettings, serve_relay
from app.ks_relay.settings_store import SettingsStore
from app.ks_relay.skills import SkillStore
from app.ks_relay.stats import StatsStore


def _secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    path = os.environ.get(f"{name}_FILE", "").strip()
    if not value and path:
        value = Path(path).expanduser().read_text(encoding="utf-8").strip()
    return value


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if os.environ.get("KS_RELAY_TRACE") == "1":
        logging.getLogger("grok_voice.ks_relay").setLevel(logging.DEBUG)
    ha_url = os.environ.get("HA_URL", "http://homeassistant.local:8123").rstrip("/")
    ws_url = ha_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    settings = RelaySettings(
        xai_api_key=_secret("XAI_API_KEY"),
        secret=_secret("KS_RELAY_SECRET"),
        port=int(os.environ.get("KS_RELAY_PORT", "8766")),
        host=os.environ.get("KS_RELAY_HOST", "0.0.0.0"),
        dry_run=os.environ.get("KS_RELAY_DRY_RUN", "") == "1",
        dry_run_delay=float(os.environ.get("KS_RELAY_DRY_RUN_DELAY_MS", "0")) / 1000,
    )
    data = Path(os.environ.get("KS_RELAY_DATA", "./relay-data"))
    data.mkdir(parents=True, exist_ok=True)
    context = RelayContext(
        ha=HaLink(ws_url, ha_url, _secret("HA_TOKEN")),
        settings=SettingsStore(data / "settings.json"),
        skills=SkillStore(data / "skills.json"),
        memory=RelayMemory(data / "ks_relay_memory.json", settings.xai_api_key),
        stats=StatsStore(data / "stats.db"),
    )
    await context.ha.start()
    from app.ks_relay.admin import first_run_import

    await first_run_import(context)
    tasks = [serve_relay(settings, context)]
    admin_port = int(os.environ.get("KS_RELAY_ADMIN_PORT", "0"))
    if admin_port:
        # Unauthenticated panel for local testing: bound to 127.0.0.1 only.
        tasks.append(serve_admin_standalone(context, "127.0.0.1", admin_port))
    try:
        await asyncio.gather(*tasks)
    finally:
        await context.ha.close()

if __name__ == "__main__":
    asyncio.run(_main())
