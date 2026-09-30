"""Grok events become one function result, and the mic stays open during playback."""

import asyncio
import json

from app.config import Settings
from app.grok_session import GrokBridge, build_session, xai_realtime_error_log, xai_session_tools_log
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
    assert bridge.client_messages({"type": "input_audio_buffer.speech_stopped"}) == [
        {"type": "speech_stopped"}
    ]


def test_completed_user_transcript_is_marked_final():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    assert bridge.client_messages(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "thank you",
        }
    ) == [{"type": "transcript", "role": "user", "text": "thank you", "final": True}]
    assert bridge.client_messages(
        {
            "type": "conversation.item.input_audio_transcription.updated",
            "transcript": "thank you",
        }
    ) == [{"type": "transcript", "role": "user", "text": "thank you", "final": False}]


def test_uplink_continues_while_audio_is_playing():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    bridge.playing = True
    payload = bridge.client_audio(b"\x01\x02")
    assert payload["type"] == "input_audio_buffer.append"
    assert payload["audio"]


def test_session_update_echo_and_rejection_are_logged():
    registered = xai_session_tools_log(
        {
            "type": "session.updated",
            "session": {
                "tools": [
                    {"type": "function", "name": "intent__HassTurnOn"},
                    {"type": "function", "name": "homeassistant__GetLiveContext"},
                    {"type": "web_search"},
                ]
            },
        }
    )
    assert registered == (
        "xAI session tools count=3 names="
        "intent__HassTurnOn,homeassistant__GetLiveContext,web_search"
    )
    assert xai_realtime_error_log({"type": "response.created"}) is None
    assert (
        xai_realtime_error_log(
            {"type": "error", "error": {"message": "tool schema rejected"}}
        )
        == "xAI realtime error: tool schema rejected"
    )


def test_session_is_full_duplex_server_vad():
    payload = build_session(_settings(), [{"type": "function", "name": "HassTurnOn"}])
    session = payload["session"]
    assert session["turn_detection"] == {
        "type": "server_vad",
        "threshold": 0.35,
        "prefix_padding_ms": 400,
    }
    assert "idle_timeout_ms" not in json.dumps(payload)
    assert "silence_duration_ms" not in json.dumps(payload)
    assert session["audio"]["input"]["format"]["rate"] == 24000
    assert "interruptible" not in json.dumps(payload)
    assert {"type": "web_search"} in session["tools"]


def test_session_instructions_include_client_area():
    payload = build_session(_settings(), [], {"name": "Attic", "id": "attic"})
    text = payload["session"]["instructions"]
    assert "Attic" in text
    assert "attic" in text
    assert "lights" in text
    assert "music" in text
    assert "Music Assistant" in text
