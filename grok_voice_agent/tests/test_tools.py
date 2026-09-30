"""MCP calls stay inside the allowlist."""

import asyncio
import json

from app.config import parse_allowlist
from app.mcp_client import function_tools, grok_parameters, voice_tool_log
from app.tools import ToolGateway, apply_default_area


class FakeMcp:
    def __init__(self) -> None:
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return json.dumps({"speech": "The attic light is on."})


def test_room_scoped_tool_gets_the_session_area_when_omitted():
    mcp = FakeMcp()
    area = {"name": "Attic", "id": "attic"}
    gateway = ToolGateway(mcp, parse_allowlist(""), default_area=area)

    async def run():
        await gateway.execute("intent__HassTurnOn", {"name": "lights"})
        await gateway.execute("intent__HassTurnOn", {"name": "kitchen lights", "area": "Kitchen"})
        await gateway.execute("homeassistant__GetLiveContext", {})
        await gateway.execute(
            "intent__HassMediaSearchAndPlay", {"search_query": "Night Tracks"}
        )
        await gateway.execute(
            "music_assistant__play_media", {"media_id": "Radiohead", "media_type": "artist"}
        )
        await gateway.execute("todo__HassListAddItem", {"name": "Shopping", "item": "milk"})
        await gateway.execute("mealie__get_mealplan", {})

    asyncio.run(run())
    assert mcp.calls[0] == ("intent__HassTurnOn", {"name": "lights", "area": "Attic", "area_id": "attic"})
    assert mcp.calls[1] == ("intent__HassTurnOn", {"name": "kitchen lights", "area": "Kitchen"})
    assert mcp.calls[2] == ("homeassistant__GetLiveContext", {})
    assert mcp.calls[3] == (
        "intent__HassMediaSearchAndPlay",
        {"search_query": "Night Tracks", "area": "Attic", "area_id": "attic"},
    )
    assert mcp.calls[4] == (
        "music_assistant__play_media",
        {
            "media_id": "Radiohead",
            "media_type": "artist",
            "area": "Attic",
            "area_id": "attic",
            "target": {"area_id": "attic"},
        },
    )
    assert mcp.calls[5] == ("todo__HassListAddItem", {"name": "Shopping", "item": "milk"})
    assert mcp.calls[6] == ("mealie__get_mealplan", {})
    assert apply_default_area("HassTurnOff", {}, area)["area"] == "Attic"


def test_blank_allowlist_is_the_assist_control_set():
    names = parse_allowlist("")
    assert "HassTurnOn" in names
    assert "GetLiveContext" in names
    assert "HassGetState" in names
    assert "HassMediaSearchAndPlay" in names
    assert "HassListAddItem" in names
    assert "music_assistant" in names
    assert "mealie" in names
    assert "todo" in names
    assert "HassBroadcast" not in names


def test_allowlisted_tool_is_forwarded():
    mcp = FakeMcp()
    gateway = ToolGateway(mcp, parse_allowlist("HassTurnOn,GetLiveContext"))

    async def run():
        return await gateway.execute("HassTurnOn", {"name": "attic light"})

    result = asyncio.run(run())
    assert json.loads(result)["speech"] == "The attic light is on."
    assert mcp.calls == [("HassTurnOn", {"name": "attic light"})]


def test_prefixed_assist_tool_is_forwarded_under_its_mcp_name():
    mcp = FakeMcp()
    gateway = ToolGateway(mcp, parse_allowlist(""))

    async def run():
        return await gateway.execute("intent__HassTurnOff", {"name": "attic light"})

    result = asyncio.run(run())
    assert json.loads(result)["speech"] == "The attic light is on."
    assert mcp.calls == [("intent__HassTurnOff", {"name": "attic light"})]


def test_unrelated_prefixed_tool_is_refused_without_calling_mcp():
    mcp = FakeMcp()
    gateway = ToolGateway(mcp, parse_allowlist(""))

    async def run():
        return await gateway.execute("script__party_mode", {})

    result = json.loads(asyncio.run(run()))
    assert result["error"] == "tool_not_allowed"
    assert mcp.calls == []


def test_other_tool_is_refused_without_calling_mcp():
    mcp = FakeMcp()
    gateway = ToolGateway(mcp, parse_allowlist("HassTurnOn"))

    async def run():
        return await gateway.execute("HassTurnOff", {"name": "attic light"})

    result = json.loads(asyncio.run(run()))
    assert result["error"] == "tool_not_allowed"
    assert mcp.calls == []


def test_2026_9_tool_names_pass_the_blank_allowlist_and_are_logged():
    listed = [
        {
            "name": "intent__HassTurnOn",
            "description": "Turn on",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {"name": "intent__HassTurnOff", "description": "Turn off"},
        {"name": "light__HassLightSet", "description": "Set a light"},
        {"name": "homeassistant__GetLiveContext", "description": "Live state"},
        {"name": "homeassistant__GetDateTime", "description": "Clock"},
        {"name": "intent__HassMediaSearchAndPlay", "description": "Play media"},
        {"name": "todo__HassListAddItem", "description": "Add a to-do"},
        {"name": "mealie__get_mealplan", "description": "Meal plan"},
        {"name": "music_assistant__play_media", "description": "Play with Music Assistant"},
        {"name": "script__party_mode", "description": "Not assist"},
    ]
    attached = function_tools(listed, parse_allowlist(""))
    names = [tool["name"] for tool in attached]
    assert names == [
        "intent__HassTurnOn",
        "intent__HassTurnOff",
        "light__HassLightSet",
        "homeassistant__GetLiveContext",
        "homeassistant__GetDateTime",
        "intent__HassMediaSearchAndPlay",
        "todo__HassListAddItem",
        "mealie__get_mealplan",
        "music_assistant__play_media",
    ]
    level, message = voice_tool_log(listed, attached)
    assert level == "info"
    assert message == (
        "voice tools mcp_listed=10 attached=9 names="
        "intent__HassTurnOn,intent__HassTurnOff,light__HassLightSet,"
        "homeassistant__GetLiveContext,homeassistant__GetDateTime,"
        "intent__HassMediaSearchAndPlay,todo__HassListAddItem,"
        "mealie__get_mealplan,music_assistant__play_media"
    )


def test_bare_names_still_attach_for_older_home_assistant():
    listed = [{"name": "HassTurnOn", "description": "Turn on"}]
    attached = function_tools(listed, parse_allowlist(""))
    assert [tool["name"] for tool in attached] == ["HassTurnOn"]


def test_empty_tool_list_is_logged():
    level, message = voice_tool_log([], [])
    assert level == "warning"
    assert message == "MCP tools/list returned 0 tools"


def test_allowlist_miss_logs_the_names_home_assistant_returned():
    listed = [{"name": "script__party_mode"}]
    level, message = voice_tool_log(listed, [])
    assert level == "warning"
    assert message == (
        "MCP listed 1 tools but attached 0 after allowlist: script__party_mode"
    )


def test_empty_anyof_branch_is_removed_before_xai_sees_the_schema():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "domain": {
                "description": "Filter by domain",
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                    {},
                ],
            }
        },
        "required": ["domain"],
    }
    cleaned = grok_parameters(schema)
    assert "$schema" not in json.dumps(cleaned)
    assert cleaned["required"] == ["domain"]
    assert cleaned["properties"]["domain"] == {
        "description": "Filter by domain",
        "anyOf": [
            {"type": "string"},
            {"type": "array", "items": {"type": "string"}},
        ],
    }
