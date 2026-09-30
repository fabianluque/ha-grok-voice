"""Add-on options and the default Assist tool set."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.memory import DEFAULT_TTL_SECONDS, clamp_memory_ttl

DEFAULT_ALLOWLIST = (
    "HassTurnOn",
    "HassTurnOff",
    "HassLightSet",
    "GetLiveContext",
    "GetDateTime",
    "HassGetState",
    "HassMediaPause",
    "HassMediaUnpause",
    "HassMediaNext",
    "HassMediaPrevious",
    "HassSetVolume",
    "HassSetVolumeRelative",
    "HassVolumeSet",
    "HassMediaPlayerMute",
    "HassMediaPlayerUnmute",
    "HassMediaSearchAndPlay",
    "play_media",
    "HassListAddItem",
    "HassListCompleteItem",
    "HassListRemoveItem",
    "HassShoppingListAddItem",
    "HassShoppingListCompleteItem",
    # Home Assistant 2026.9 prefixes tools as domain__name. These entries
    # attach every MCP tool from that integration (Music Assistant, Mealie, …).
    "media_player",
    "music_assistant",
    "todo",
    "mealie",
    "calendar",
)
SUPERVISOR_MCP_URL = "http://supervisor/core/api/mcp"
# User access tokens are checked against Core on the host. This add-on sets
# host_network, so the published Home Assistant port is on loopback.
# Supervisor's ``/core/api`` proxy accepts only the add-on token.
HOME_ASSISTANT_API_URL = "http://127.0.0.1:8123"
# s6-overlay keeps Docker's environment here and does not export it into a
# service started with a plain ``#!/bin/sh`` script. Supervisor still writes
# SUPERVISOR_TOKEN (and the legacy HASSIO_TOKEN alias) into that directory.
S6_CONTAINER_ENV_DIRS = (
    Path("/run/s6/container_environment"),
    Path("/var/run/s6/container_environment"),
)
_ADDON_TOKEN_NAMES = ("SUPERVISOR_TOKEN", "HASSIO_TOKEN")


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
    default_area: str = ""
    default_area_id: str = ""
    home_location: str = ""
    conversation_memory_ttl_seconds: int = DEFAULT_TTL_SECONDS
    ingress_port: int = 8099
    debug_port: int = 8080
    mcp_token_source: str = "missing"


def parse_allowlist(raw: str | None) -> frozenset[str]:
    if raw is None or not raw.strip():
        return frozenset(DEFAULT_ALLOWLIST)
    if raw.strip() == "*":
        return frozenset({"*"})
    names = {part.strip() for part in raw.split(",") if part.strip()}
    return frozenset(names)


def clean_token(value: object) -> str:
    return str(value or "").replace("\x00", "").strip()


def read_container_env(name: str, directories: tuple[Path, ...] = S6_CONTAINER_ENV_DIRS) -> str:
    """Return one container variable from the process or from s6's env dir."""
    import os

    from_process = clean_token(os.environ.get(name))
    if from_process:
        return from_process
    for directory in directories:
        path = directory / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        token = clean_token(text)
        if token:
            return token
    return ""


def supervisor_access_token(directories: tuple[Path, ...] = S6_CONTAINER_ENV_DIRS) -> str:
    """Add-on token Supervisor injects for homeassistant_api."""
    for name in _ADDON_TOKEN_NAMES:
        token = read_container_env(name, directories)
        if token:
            return token
    return ""


def load_settings(
    path: str | Path = "/data/options.json",
    env_dirs: tuple[Path, ...] | None = None,
) -> Settings:
    options_path = Path(path)
    options = json.loads(options_path.read_text()) if options_path.exists() else {}
    long_lived = clean_token(options.get("longlived_token"))
    directories = S6_CONTAINER_ENV_DIRS if env_dirs is None else env_dirs
    addon_token = supervisor_access_token(directories)
    if long_lived:
        mcp_token = long_lived
        mcp_token_source = "long-lived"
    elif addon_token:
        mcp_token = addon_token
        mcp_token_source = "supervisor"
    else:
        mcp_token = ""
        mcp_token_source = "missing"
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
        mcp_token=mcp_token,
        allowlist=parse_allowlist(options.get("mcp_tool_allowlist")),
        idle_timeout_seconds=int(options.get("idle_timeout_seconds") or 30),
        ha_api_url=HOME_ASSISTANT_API_URL,
        default_area=str(options.get("default_area") or "").strip(),
        default_area_id=str(options.get("default_area_id") or "").strip(),
        home_location=str(options.get("home_location") or "").strip(),
        conversation_memory_ttl_seconds=clamp_memory_ttl(
            options.get("conversation_memory_ttl_seconds"),
            DEFAULT_TTL_SECONDS,
        ),
        mcp_token_source=mcp_token_source,
    )
