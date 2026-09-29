"""Allowlisted Home Assistant MCP tool calls."""

from __future__ import annotations

import json

from app.config import DEFAULT_ALLOWLIST


class ToolGateway:
    def __init__(self, mcp, allowlist: frozenset[str] | set[str] | None = None) -> None:
        self.mcp = mcp
        self.allowlist = frozenset(allowlist) if allowlist is not None else frozenset(DEFAULT_ALLOWLIST)

    def allowed(self, name: str) -> bool:
        return "*" in self.allowlist or name in self.allowlist

    async def execute(self, name: str, arguments: dict) -> str:
        if not self.allowed(name):
            return json.dumps({"error": "tool_not_allowed", "name": name})
        result = await self.mcp.call_tool(name, arguments)
        if isinstance(result, str):
            return result
        return json.dumps(result)
