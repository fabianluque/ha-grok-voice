"""Add-on entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from app.config import load_settings
from app.server import install_redacting_logs, serve_voice

log = logging.getLogger("grok_voice")


async def _main() -> None:
    options_path = os.environ.get("OPTIONS_PATH", "/data/options.json")
    settings = load_settings(options_path)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    install_redacting_logs([settings.xai_api_key, settings.mcp_token])
    if not settings.xai_api_key:
        log.error("xAI API key is empty; set it in the add-on configuration")
    log.info(
        "starting model=%s voice=%s mcp=%s mcp_auth=%s",
        settings.model,
        settings.voice,
        settings.ha_mcp_url,
        settings.mcp_token_source,
    )
    if not settings.mcp_token:
        log.error("MCP token is empty; Home Assistant tools cannot authenticate")
    async with httpx.AsyncClient(timeout=30) as http:
        await serve_voice(settings, http)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
