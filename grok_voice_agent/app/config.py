"""Add-on options and the default Assist tool set."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ALLOWLIST = (
    "HassTurnOn",
    "HassTurnOff",
    "HassLightSet",
    "GetLiveContext",
    "GetDateTime",
)
SUPERVISOR_MCP_URL = "http://supervisor/core/api/mcp"
SUPERVISOR_API_URL = "http://supervisor/core"


@dataclass(frozen=True)
class Settings:
    xai_api_key: str
    instructions: str
    voice: str
    model: str
    reasoning_effort: str
    enable_web_search: bool
    enable_x_search: bool
    ha_mcp_url: str
    mcp_token: str
    allowlist: frozenset[str]
    idle_timeout_seconds: int
    ha_api_url: str
    ingress_port: int = 8099
    debug_port: int = 8080


def parse_allowlist(raw: str | None) -> frozenset[str]:
    if raw is None or not raw.strip():
        return frozenset(DEFAULT_ALLOWLIST)
    if raw.strip() == "*":
        return frozenset({"*"})
    names = {part.strip() for part in raw.split(",") if part.strip()}
    return frozenset(names)


def load_settings(path: str | Path = "/data/options.json") -> Settings:
    options_path = Path(path)
    options = json.loads(options_path.read_text()) if options_path.exists() else {}
    long_lived = str(options.get("longlived_token") or "")
    supervisor_token = ""
    # Imported lazily so tests can load settings without the add-on env.
    import os

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
    mcp_url = str(options.get("ha_mcp_url") or "").strip() or SUPERVISOR_MCP_URL
    return Settings(
        xai_api_key=str(options.get("xai_api_key") or ""),
        instructions=str(options.get("instructions") or ""),
        voice=str(options.get("voice") or "eve"),
        model=str(options.get("model") or "grok-voice-think-fast-2.0"),
        reasoning_effort=str(options.get("reasoning_effort") or "none"),
        enable_web_search=bool(options.get("enable_web_search", True)),
        enable_x_search=bool(options.get("enable_x_search", False)),
        ha_mcp_url=mcp_url,
        mcp_token=long_lived or supervisor_token,
        allowlist=parse_allowlist(options.get("mcp_tool_allowlist")),
        idle_timeout_seconds=int(options.get("idle_timeout_seconds") or 20),
        ha_api_url=SUPERVISOR_API_URL,
    )
