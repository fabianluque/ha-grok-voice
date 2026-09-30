"""Allowlisted Home Assistant MCP tool calls."""

from __future__ import annotations

import json

from app.config import DEFAULT_ALLOWLIST
from app.mcp_client import bare_tool_name, tool_allowed

ROOM_SCOPED_TOOLS = frozenset(
    {
        "HassTurnOn",
        "HassTurnOff",
        "HassLightSet",
        "HassMediaPause",
        "HassMediaUnpause",
        "HassSetVolume",
        "HassVolumeSet",
        "HassMediaNext",
        "HassMediaPrevious",
    }
)


def apply_default_area(name: str, arguments: dict, area: dict[str, str] | None) -> dict:
    """Fill area on room-scoped tools when the model omitted it."""
    if not area or not isinstance(arguments, dict):
        return arguments
    if bare_tool_name(name) not in ROOM_SCOPED_TOOLS:
        return arguments
    if arguments.get("area") or arguments.get("area_id"):
        return arguments
    filled = dict(arguments)
    if area.get("name"):
        filled["area"] = area["name"]
    if area.get("id"):
        filled["area_id"] = area["id"]
    return filled


class ToolGateway:
    def __init__(
        self,
        mcp,
        allowlist: frozenset[str] | set[str] | None = None,
        default_area: dict[str, str] | None = None,
    ) -> None:
        self.mcp = mcp
        self.allowlist = frozenset(allowlist) if allowlist is not None else frozenset(DEFAULT_ALLOWLIST)
        self.default_area = default_area

    def allowed(self, name: str) -> bool:
        return tool_allowed(name, self.allowlist)

    async def execute(self, name: str, arguments: dict) -> str:
        if not self.allowed(name):
            return json.dumps({"error": "tool_not_allowed", "name": name})
        payload = apply_default_area(name, arguments if isinstance(arguments, dict) else {}, self.default_area)
        result = await self.mcp.call_tool(name, payload)
        if isinstance(result, str):
            return result
        return json.dumps(result)
