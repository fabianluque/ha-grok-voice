"""Streamable HTTP client for the Home Assistant MCP server."""

from __future__ import annotations

import json
from typing import Any


class McpError(RuntimeError):
    pass


class McpHttpClient:
    def __init__(self, url: str, token: str, http) -> None:
        self.url = url
        self.token = token
        self.http = http
        self._session_id: str | None = None
        self._next_id = 1
        self._ready = False

    async def list_tools(self) -> list[dict]:
        await self._ensure_ready()
        payload = await self._rpc("tools/list", {})
        return list(payload.get("tools") or [])

    async def call_tool(self, name: str, arguments: dict) -> str:
        await self._ensure_ready()
        payload = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        if payload.get("isError"):
            return json.dumps({"error": "mcp_tool_failed", "detail": _content_text(payload)})
        return _content_text(payload) or json.dumps(payload)

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        await self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "grok-voice-agent", "version": "0.1.0"},
            },
        )
        await self._notify("notifications/initialized", {})
        self._ready = True

    async def _rpc(self, method: str, params: dict) -> dict:
        message_id = self._next_id
        self._next_id += 1
        body = await self._post({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        if "error" in body:
            raise McpError(str(body["error"]))
        result = body.get("result", body)
        return result if isinstance(result, dict) else {"result": result}

    async def _notify(self, method: str, params: dict) -> None:
        await self._post({"jsonrpc": "2.0", "method": method, "params": params})

    async def _post(self, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = await self.http.post(self.url, json=payload, headers=headers)
        session_id = _header(response, "mcp-session-id")
        if session_id:
            self._session_id = session_id
        if getattr(response, "status_code", 200) >= 400:
            raise McpError(f"MCP HTTP {response.status_code}")
        return _decode_body(getattr(response, "text", "") or "")


def function_tools(mcp_tools: list[dict], allowlist: frozenset[str]) -> list[dict]:
    selected = []
    for tool in mcp_tools:
        name = tool.get("name")
        if not name:
            continue
        if "*" not in allowlist and name not in allowlist:
            continue
        selected.append(
            {
                "type": "function",
                "name": name,
                "description": tool.get("description") or name,
                "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
            }
        )
    return selected


def _header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", {}) or {}
    if hasattr(headers, "get"):
        value = headers.get(name) or headers.get(name.title()) or headers.get(name.upper())
        return str(value) if value else None
    return None


def _decode_body(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    for line in stripped.splitlines():
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                parsed = json.loads(data)
                return parsed if isinstance(parsed, dict) else {}
    return {}


def _content_text(payload: dict) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        if parts:
            return "\n".join(parts)
    return json.dumps(payload)
