"""Grok events become one function result, and the mic stays open during playback."""

import asyncio
import json

from app.config import Settings
from app.grok_session import GrokBridge, build_session
from app.tools import ToolGateway


class FakeMcp:
    async def call_tool(self, name, arguments):
        return json.dumps({"ok": True, "name": name, "arguments": arguments})


def _settings() -> Settings:
    return Settings(
        xai_api_key="secret-key",
        instructions="speak briefly",
        voice="eve",
        model="grok-voice-think-fast-2.0",
        reasoning_effort="none",
        enable_web_search=True,
        enable_x_search=False,
        ha_mcp_url="http://supervisor/core/api/mcp",
        mcp_token="supervisor-token",
        allowlist=frozenset({"HassTurnOn"}),
        idle_timeout_seconds=20,
        ha_api_url="http://supervisor/core",
    )


def test_function_call_becomes_one_function_call_output():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))

    async def run():
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "HassTurnOn",
                "call_id": "call-1",
                "arguments": json.dumps({"name": "attic light"}),
            }
        )

    message = asyncio.run(run())
    assert message["type"] == "conversation.item.create"
    assert message["item"]["type"] == "function_call_output"
    assert message["item"]["call_id"] == "call-1"
    assert json.loads(message["item"]["output"])["ok"] is True
    followup = bridge.followup_after_tools()
    assert followup == {"type": "response.create"}


def test_speech_started_is_forwarded_immediately():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    bridge.playing = True
    assert bridge.client_messages({"type": "input_audio_buffer.speech_started"}) == [
        {"type": "speech_started"}
    ]
    assert bridge.playing is False


def test_uplink_continues_while_audio_is_playing():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    bridge.playing = True
    payload = bridge.client_audio(b"\x01\x02")
    assert payload["type"] == "input_audio_buffer.append"
    assert payload["audio"]


def test_session_is_full_duplex_server_vad():
    payload = build_session(_settings(), [{"type": "function", "name": "HassTurnOn"}])
    session = payload["session"]
    assert session["turn_detection"] == {"type": "server_vad"}
    assert session["audio"]["input"]["format"]["rate"] == 24000
    assert "interruptible" not in json.dumps(payload)
    assert {"type": "web_search"} in session["tools"]
