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


def test_end_session_is_local_and_does_not_call_mcp():
    mcp = FakeMcp()
    bridge = GrokBridge(ToolGateway(mcp, frozenset({"HassTurnOn"})))

    async def run():
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "dismiss"}),
            }
        )

    message = asyncio.run(run())
    assert json.loads(message["item"]["output"]) == {
        "ok": True,
        "ending": False,
        "reason": "dismiss",
        "keep_open": True,
        "error": "closing_phrase_required",
    }
    assert bridge.end_after_response is False
    assert bridge.followup_after_tools() == {"type": "response.create"}
    assert bridge.consume_end_session() is False


def test_end_session_dismiss_after_closing_phrase_hangs_up():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    bridge.note_closing_phrase()

    async def run():
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "dismiss"}),
            }
        )

    message = asyncio.run(run())
    assert json.loads(message["item"]["output"]) == {"ok": True, "ending": True, "reason": "dismiss"}
    assert bridge.end_after_response is True
    assert bridge.end_session_forget is True
    assert bridge.consume_end_session() is False
    assert bridge.followup_after_tools() == {"type": "response.create"}
    assert bridge.consume_end_session() is True


def test_end_session_dismiss_after_followup_question_stays_open():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))

    async def run():
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "dismiss"}),
            }
        )

    asyncio.run(run())
    bridge.followup_after_tools()
    bridge.client_messages(
        {
            "type": "response.output_audio_transcript.done",
            "transcript": "The Mets won 4-2. Want last night's highlights?",
        }
    )
    assert bridge.assistant_asked_followup() is True
    assert bridge.consume_end_session() is False


def test_end_session_command_after_home_control_followup_stays_open():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))

    async def run():
        await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "HassTurnOn",
                "call_id": "call-1",
                "arguments": json.dumps({"name": "attic light"}),
            }
        )
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "command"}),
            }
        )

    asyncio.run(run())
    assert bridge.end_after_response is True
    bridge.followup_after_tools()
    bridge.client_messages(
        {
            "type": "response.output_audio_transcript.done",
            "transcript": "Lights on. Want the kitchen too?",
        }
    )
    assert bridge.consume_end_session() is False


def test_closing_phrase_then_anything_else_still_hangs_up():
    """Regression: thank you + 'Anything else?' used to leave the duplex open."""
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    bridge.note_closing_phrase()
    assert bridge.end_after_response is True
    bridge.client_messages(
        {
            "type": "response.output_audio_transcript.done",
            "transcript": "You're welcome. Anything else?",
        }
    )
    assert bridge.assistant_asked_followup() is True
    assert bridge.consume_end_session() is True


def test_end_session_dismiss_after_closing_phrase_hangs_up_despite_anything_else():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    bridge.note_closing_phrase()

    async def run():
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "dismiss"}),
            }
        )

    message = asyncio.run(run())
    assert json.loads(message["item"]["output"])["ending"] is True
    bridge.followup_after_tools()
    bridge.client_messages(
        {
            "type": "response.output_audio_transcript.done",
            "transcript": "You're welcome. Anything else?",
        }
    )
    assert bridge.assistant_asked_followup() is True
    assert bridge.consume_end_session() is True


def test_idle_timeout_is_longer_after_a_followup():
    from app.grok_session import FOLLOWUP_IDLE_GRACE_SECONDS

    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    assert bridge.idle_timeout_seconds(30) == 30
    bridge.client_messages(
        {
            "type": "response.output_audio_transcript.done",
            "transcript": "A concert Saturday. Want me to check Sunday too?",
        }
    )
    assert bridge.idle_timeout_seconds(30) == 30 + FOLLOWUP_IDLE_GRACE_SECONDS


def test_end_session_command_requires_home_control():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))

    async def hang_up():
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "command"}),
            }
        )

    output = json.loads(asyncio.run(hang_up())["item"]["output"])
    assert output["ending"] is False
    assert output["error"] == "home_control_required"
    assert bridge.end_after_response is False
    assert bridge.followup_after_tools() == {"type": "response.create"}
    assert bridge.consume_end_session() is False


def test_end_session_command_after_home_control_hangs_up():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))

    async def run():
        await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "HassTurnOn",
                "call_id": "call-1",
                "arguments": json.dumps({"name": "attic light"}),
            }
        )
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "command"}),
            }
        )

    message = asyncio.run(run())
    assert json.loads(message["item"]["output"]) == {"ok": True, "ending": True, "reason": "command"}
    assert bridge.home_control_this_turn is True
    assert bridge.end_after_response is True
    assert bridge.end_session_forget is False
    assert bridge.consume_end_session() is False
    assert bridge.followup_after_tools() == {"type": "response.create"}
    assert bridge.consume_end_session() is True


def test_end_session_command_then_home_control_still_hangs_up():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))

    async def run():
        first = await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "command"}),
            }
        )
        await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "HassTurnOn",
                "call_id": "call-1",
                "arguments": json.dumps({"name": "attic light"}),
            }
        )
        return first

    first = asyncio.run(run())
    assert json.loads(first["item"]["output"])["ending"] is False
    assert bridge.home_control_this_turn is True
    assert bridge.end_after_response is True
    assert bridge.end_session_forget is False
    assert bridge.consume_end_session() is False
    assert bridge.followup_after_tools() == {"type": "response.create"}
    assert bridge.consume_end_session() is True


def test_end_session_command_after_query_tool_stays_open():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"GetLiveContext", "HassTurnOn"})))

    async def run():
        await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "homeassistant__GetLiveContext",
                "call_id": "call-1",
                "arguments": "{}",
            }
        )
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "command"}),
            }
        )

    output = json.loads(asyncio.run(run())["item"]["output"])
    assert output["ending"] is False
    assert bridge.home_control_this_turn is False
    assert bridge.followup_after_tools() == {"type": "response.create"}
    assert bridge.consume_end_session() is False


def test_failed_home_control_does_not_allow_command_hangup():
    class FailingMcp:
        async def call_tool(self, name, arguments):
            return json.dumps({"error": "mcp_tool_failed"})

    bridge = GrokBridge(ToolGateway(FailingMcp(), frozenset({"HassTurnOn"})))

    async def run():
        await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "HassTurnOn",
                "call_id": "call-1",
                "arguments": json.dumps({"name": "attic light"}),
            }
        )
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "command"}),
            }
        )

    output = json.loads(asyncio.run(run())["item"]["output"])
    assert output["ending"] is False
    assert bridge.home_control_this_turn is False
    assert bridge.consume_end_session() is False
    assert bridge.followup_after_tools() == {"type": "response.create"}
    assert bridge.consume_end_session() is False


def test_new_user_turn_clears_home_control_so_later_qna_does_not_hang_up():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))

    async def lights():
        await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "HassTurnOn",
                "call_id": "call-1",
                "arguments": json.dumps({"name": "attic light"}),
            }
        )

    asyncio.run(lights())
    assert bridge.home_control_this_turn is True
    bridge.followup_after_tools()
    assert bridge.consume_end_session() is False
    bridge.client_messages({"type": "input_audio_buffer.speech_started"})
    assert bridge.home_control_this_turn is False

    async def sports_hangup():
        return await bridge.handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "name": "end_session",
                "call_id": "call-end",
                "arguments": json.dumps({"reason": "command"}),
            }
        )

    output = json.loads(asyncio.run(sports_hangup())["item"]["output"])
    assert output["ending"] is False
    assert bridge.followup_after_tools() == {"type": "response.create"}
    assert bridge.consume_end_session() is False


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


def test_assistant_transcript_deltas_stream_then_finalize():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    assert bridge.client_messages({"type": "response.audio_transcript.delta", "delta": "The lights"}) == [
        {"type": "transcript", "role": "assistant", "text": "The lights", "final": False}
    ]
    assert bridge.client_messages(
        {"type": "response.output_audio_transcript.delta", "delta": " are on."}
    ) == [{"type": "transcript", "role": "assistant", "text": "The lights are on.", "final": False}]
    assert bridge.client_messages(
        {"type": "response.audio_transcript.done", "transcript": "The lights are on."}
    ) == [{"type": "transcript", "role": "assistant", "text": "The lights are on.", "final": True}]
    assert bridge.client_messages({"type": "response.audio_transcript.delta", "delta": "Okay"}) == [
        {"type": "transcript", "role": "assistant", "text": "Okay", "final": False}
    ]


def test_assistant_cumulative_delta_replaces_partial():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    assert bridge.client_messages(
        {"type": "response.output_audio_transcript.delta", "delta": "Hi"}
    ) == [{"type": "transcript", "role": "assistant", "text": "Hi", "final": False}]
    assert bridge.client_messages(
        {"type": "response.output_audio_transcript.delta", "delta": "Hi there"}
    ) == [{"type": "transcript", "role": "assistant", "text": "Hi there", "final": False}]


def test_user_transcript_delta_streams():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    assert bridge.client_messages(
        {"type": "conversation.item.input_audio_transcription.delta", "delta": "turn on"}
    ) == [{"type": "transcript", "role": "user", "text": "turn on", "final": False}]
    assert bridge.client_messages(
        {"type": "conversation.item.input_audio_transcription.delta", "delta": " the lights"}
    ) == [{"type": "transcript", "role": "user", "text": "turn on the lights", "final": False}]


def test_speech_started_resets_assistant_partial():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    bridge.client_messages({"type": "response.audio_transcript.delta", "delta": "Hello"})
    assert bridge.client_messages({"type": "input_audio_buffer.speech_started"}) == [{"type": "speech_started"}]
    assert bridge.client_messages({"type": "response.audio_transcript.delta", "delta": "Yes"}) == [
        {"type": "transcript", "role": "assistant", "text": "Yes", "final": False}
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


def _history_item(role: str, text: str) -> dict:
    content_type = "input_text" if role == "user" else "text"
    return {"type": "message", "role": role, "content": [{"type": content_type, "text": text}]}


def test_reinjected_history_item_echoes_are_not_client_transcripts():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    meeting = "I have a meeting today at 7pm"
    for kind in ("conversation.item.created", "conversation.item.added", "conversation.item.done"):
        assert bridge.client_messages({"type": kind, "item": _history_item("user", meeting)}) == []
        assert bridge.client_messages({"type": kind, "item": _history_item("assistant", "Got it")}) == []
    assert bridge.client_messages(
        {"type": "conversation.item.input_audio_transcription.updated", "transcript": "and tomorrow?"}
    ) == [{"type": "transcript", "role": "user", "text": "and tomorrow?", "final": False}]


def test_live_audio_item_done_is_still_a_fallback_transcript():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    assert (
        bridge.client_messages(
            {
                "type": "conversation.item.created",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_audio", "transcript": "turn on the lights"}],
                },
            }
        )
        == []
    )
    assert bridge.client_messages(
        {
            "type": "conversation.item.done",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_audio", "transcript": "turn on the lights"}],
            },
        }
    ) == [{"type": "transcript", "role": "user", "text": "turn on the lights", "final": True}]


def test_user_updated_snapshot_streams_and_can_revise():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    assert bridge.client_messages(
        {"type": "conversation.item.input_audio_transcription.updated", "transcript": "Hello?"}
    ) == [{"type": "transcript", "role": "user", "text": "Hello?", "final": False}]
    assert bridge.client_messages(
        {
            "type": "conversation.item.input_audio_transcription.updated",
            "transcript": "Hello, my name is",
        }
    ) == [{"type": "transcript", "role": "user", "text": "Hello, my name is", "final": False}]


def test_user_snapshot_forwards_item_id():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    assert bridge.client_messages(
        {
            "type": "conversation.item.input_audio_transcription.updated",
            "item_id": "item_9",
            "transcript": "turn on the light",
        }
    ) == [
        {
            "type": "transcript",
            "role": "user",
            "text": "turn on the light",
            "final": False,
            "itemId": "item_9",
        }
    ]
    assert bridge.client_messages(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item_9",
            "transcript": "turn off the attic fan",
        }
    ) == [
        {
            "type": "transcript",
            "role": "user",
            "text": "turn off the attic fan",
            "final": True,
            "itemId": "item_9",
        }
    ]


def test_empty_transcript_delta_is_not_forwarded():
    bridge = GrokBridge(ToolGateway(FakeMcp(), frozenset({"HassTurnOn"})))
    assert bridge.client_messages({"type": "response.audio_transcript.delta", "delta": ""}) == []
    assert bridge.client_messages({"type": "response.audio_transcript.delta", "delta": "Hi"}) == [
        {"type": "transcript", "role": "assistant", "text": "Hi", "final": False}
    ]


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
    assert session["turn_detection"]["type"] == "server_vad"
    assert session["turn_detection"]["prefix_padding_ms"] == 800
    assert session["turn_detection"]["threshold"] == 0.35
    assert "idle_timeout_ms" not in json.dumps(payload)
    assert "silence_duration_ms" not in json.dumps(payload)
    assert session["audio"]["input"]["format"]["rate"] == 24000
    assert session["audio"]["input"]["transcription"]["model"] == "grok-transcribe"
    assert "interruptible" not in json.dumps(payload)
    assert {"type": "web_search"} in session["tools"]
    assert any(tool.get("name") == "end_session" for tool in session["tools"])
    assert "end_session" in session["instructions"]


def test_session_instructions_include_client_area():
    payload = build_session(_settings(), [], {"name": "Attic", "id": "attic"})
    text = payload["session"]["instructions"]
    assert "Attic" in text
    assert "attic" in text
    assert "lights" in text
    assert "music" in text
    assert "Music Assistant" in text
    assert "end_session" in text
    assert "Never call end_session after sports" in text
    assert "same turn as a follow-up" in text
    assert "short follow-up" in text
    assert "thank you" in text
