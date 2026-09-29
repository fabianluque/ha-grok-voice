"""Grok Voice session configuration and event mapping.

Uplink audio is forwarded for the whole session, including while a reply is
playing. Barge-in is Grok server VAD plus an immediate speech_started event
so the browser can flush its own playback queue.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from app.tools import ToolGateway

AUDIO_DELTA_TYPES = {"response.output_audio.delta", "response.audio.delta"}
TRANSCRIPT_ROLES = {
    "conversation.item.input_audio_transcription.updated": "user",
    "conversation.item.input_audio_transcription.completed": "user",
    "response.output_audio_transcript.done": "assistant",
    "response.audio_transcript.done": "assistant",
}


def build_session(settings, function_tools: list[dict]) -> dict:
    tools: list[dict] = list(function_tools)
    if settings.enable_web_search:
        tools.append({"type": "web_search"})
    if settings.enable_x_search:
        tools.append({"type": "x_search"})
    return {
        "type": "session.update",
        "session": {
            "voice": settings.voice,
            "instructions": settings.instructions,
            "reasoning": {"effort": settings.reasoning_effort},
            "turn_detection": {"type": "server_vad"},
            "audio": {
                "input": {"format": {"type": "audio/pcm", "rate": 24000}},
                "output": {"format": {"type": "audio/pcm", "rate": 24000}},
            },
            "tools": tools,
        },
    }


class GrokBridge:
    def __init__(self, tools: ToolGateway) -> None:
        self.tools = tools
        self.playing = False
        self.awaiting_tool_followup = False

    def client_audio(self, pcm: bytes) -> dict:
        """Always append mic audio, including while the assistant is speaking."""
        return {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm).decode("ascii"),
        }

    async def handle_function_call(self, event: dict) -> dict:
        raw_arguments = event.get("arguments") or "{}"
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {}
        else:
            arguments = raw_arguments
        output = await self.tools.execute(str(event.get("name") or ""), arguments)
        self.awaiting_tool_followup = True
        return {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": event.get("call_id"),
                "output": output,
            },
        }

    def followup_after_tools(self) -> dict | None:
        if not self.awaiting_tool_followup:
            return None
        self.awaiting_tool_followup = False
        return {"type": "response.create"}

    def client_messages(self, event: dict) -> list[dict[str, Any]]:
        event_type = event.get("type")
        if event_type == "input_audio_buffer.speech_started":
            self.playing = False
            return [{"type": "speech_started"}]
        if event_type == "response.created":
            response = event.get("response") or {}
            return [{"type": "response_started", "responseId": response.get("id")}]
        if event_type in AUDIO_DELTA_TYPES and event.get("delta"):
            self.playing = True
            return [{"type": "binary", "pcm": base64.b64decode(event["delta"])}]
        role = TRANSCRIPT_ROLES.get(event_type or "")
        if role:
            text = event.get("transcript") or event.get("delta") or ""
            if text:
                return [{"type": "transcript", "role": role, "text": text}]
        return []
