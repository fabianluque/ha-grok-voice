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
            "Authorization": bearer_header(self.token),
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


def bearer_header(token: str) -> str:
    """Authorization value for the Supervisor add-on token.

    An empty token becomes ``Bearer `` with a trailing space. httpx rejects
    that as an illegal header before the MCP request is sent.
    """
    cleaned = str(token or "").replace("\x00", "").strip()
    if not cleaned or any(char in cleaned for char in "\r\n"):
        raise McpError("MCP token is empty")
    return f"Bearer {cleaned}"


def bare_tool_name(name: str) -> str:
    """Return the Assist name from a Home Assistant 2026.9 tool name.

    Core now prefixes every LLM tool with the integration domain, so the
    MCP server lists ``intent__HassTurnOn`` and ``homeassistant__GetLiveContext``
    instead of ``HassTurnOn`` and ``GetLiveContext``.
    """
    return name.rsplit("__", 1)[-1]


def tool_domain(name: str) -> str:
    """Integration domain from a 2026.9 ``domain__Tool`` name, else empty."""
    cleaned = str(name or "")
    if "__" not in cleaned:
        return ""
    return cleaned.rsplit("__", 1)[0]


def tool_allowed(name: str, allowlist: frozenset[str] | set[str]) -> bool:
    """True when the MCP name matches an allowlist entry, ignoring a domain prefix.

    ``HassTurnOn`` and ``intent__HassTurnOn`` are the same Assist tool. A blank
    allowlist stores the bare name. A list typed with the 2026.9 name still
    matches an older server that has not prefixed it yet. An allowlist entry
    that is only a domain (``music_assistant``, ``mealie``, ``todo``) attaches
    every MCP tool from that integration.
    """
    if "*" in allowlist or name in allowlist:
        return True
    domain = tool_domain(name)
    if domain and domain in allowlist:
        return True
    bare = bare_tool_name(name)
    if bare in allowlist:
        return True
    return any(bare_tool_name(entry) == bare for entry in allowlist)


def function_tools(mcp_tools: list[dict], allowlist: frozenset[str]) -> list[dict]:
    selected = []
    for tool in mcp_tools:
        name = tool.get("name")
        if not name or not tool_allowed(str(name), allowlist):
            continue
        schema = tool.get("inputSchema")
        if schema is None:
            schema = tool.get("input_schema")
        selected.append(
            {
                "type": "function",
                "name": str(name),
                "description": tool.get("description") or str(name),
                "parameters": grok_parameters(schema),
            }
        )
    return selected


def voice_tool_log(listed: list[dict], attached: list[dict]) -> tuple[str, str]:
    """Log line for the tools actually handed to the voice session.

    ``listed`` is the raw ``tools/list`` payload. ``attached`` is the function
    tools after the allowlist. An HTTP 200 with an empty attachment is the
    failure mode where Grok says it has no way to control devices.
    """
    listed_names = [str(tool["name"]) for tool in listed if tool.get("name")]
    attached_names = [str(tool["name"]) for tool in attached if tool.get("name")]
    if not listed_names:
        return "warning", "MCP tools/list returned 0 tools"
    if not attached_names:
        return (
            "warning",
            "MCP listed "
            f"{len(listed_names)} tools but attached 0 after allowlist: "
            f"{','.join(listed_names)}",
        )
    return (
        "info",
        "voice tools "
        f"mcp_listed={len(listed_names)} attached={len(attached_names)} "
        f"names={','.join(attached_names)}",
    )


def grok_parameters(schema: object) -> dict:
    """JSON Schema xAI can compile into a function-call grammar.

    Home Assistant's ``vol.Any`` conversion sometimes emits an ``anyOf``
    branch that is an empty object. That branch makes clients reject the
    whole tool, and a rejected ``session.update`` leaves the voice session
    with no Home Assistant tools.
    """
    cleaned = _clean_schema(schema) if isinstance(schema, dict) else None
    if not isinstance(cleaned, dict):
        cleaned = {}
    properties_in = cleaned.get("properties")
    properties: dict = {}
    if isinstance(properties_in, dict):
        for name, prop in properties_in.items():
            if not isinstance(name, str):
                continue
            prop_clean = _clean_schema(prop)
            if isinstance(prop_clean, dict) and prop_clean:
                properties[name] = prop_clean
            else:
                properties[name] = {"type": "string"}
    result: dict = {"type": "object", "properties": properties}
    required = cleaned.get("required")
    if isinstance(required, list):
        names = [item for item in required if isinstance(item, str) and item in properties]
        if names:
            result["required"] = names
    return result


_SCHEMA_DROP = {"$schema", "$id", "$comment"}
_UNION_KEYS = ("anyOf", "oneOf", "allOf")


def _clean_schema(node: object) -> object:
    if isinstance(node, list):
        return [_clean_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    if not node:
        return None
    cleaned: dict = {}
    for key, value in node.items():
        if key in _SCHEMA_DROP:
            continue
        if key in _UNION_KEYS and isinstance(value, list):
            branches = []
            for branch in value:
                cleaned_branch = _clean_schema(branch)
                if isinstance(cleaned_branch, dict) and cleaned_branch:
                    branches.append(cleaned_branch)
            if not branches:
                continue
            if len(branches) == 1:
                for branch_key, branch_value in branches[0].items():
                    cleaned.setdefault(branch_key, branch_value)
                continue
            cleaned[key] = branches
            continue
        if isinstance(value, (dict, list)):
            cleaned_value = _clean_schema(value)
            if cleaned_value is None:
                continue
            cleaned[key] = cleaned_value
            continue
        cleaned[key] = value
    return cleaned or None


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
