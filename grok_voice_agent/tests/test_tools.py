"""MCP calls stay inside the allowlist."""

import asyncio
import json

from app.config import parse_allowlist
from app.tools import ToolGateway


class FakeMcp:
    def __init__(self) -> None:
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return json.dumps({"speech": "The attic light is on."})


def test_blank_allowlist_is_the_assist_control_set():
    names = parse_allowlist("")
    assert "HassTurnOn" in names
    assert "GetLiveContext" in names
    assert "HassMediaPlayer" not in names


def test_allowlisted_tool_is_forwarded():
    mcp = FakeMcp()
    gateway = ToolGateway(mcp, parse_allowlist("HassTurnOn,GetLiveContext"))

    async def run():
        return await gateway.execute("HassTurnOn", {"name": "attic light"})

    result = asyncio.run(run())
    assert json.loads(result)["speech"] == "The attic light is on."
    assert mcp.calls == [("HassTurnOn", {"name": "attic light"})]


def test_other_tool_is_refused_without_calling_mcp():
    mcp = FakeMcp()
    gateway = ToolGateway(mcp, parse_allowlist("HassTurnOn"))

    async def run():
        return await gateway.execute("HassTurnOff", {"name": "attic light"})

    result = json.loads(asyncio.run(run()))
    assert result["error"] == "tool_not_allowed"
    assert mcp.calls == []
