"""Grok Voice session configuration and event mapping.

Uplink audio is forwarded for the whole session, including while a reply is
playing. Barge-in is Grok server VAD plus an immediate speech_started event
so the browser can flush its own playback queue.
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from typing import Any

from app.tools import ToolGateway

AUDIO_DELTA_TYPES = {"response.output_audio.delta", "response.audio.delta"}
TRANSCRIPT_ROLES = {
    "conversation.item.input_audio_transcription.updated": "user",
    "conversation.item.input_audio_transcription.completed": "user",
    "response.output_audio_transcript.done": "assistant",
    "response.audio_transcript.done": "assistant",
}
FINAL_TRANSCRIPT_TYPES = {
    "conversation.item.input_audio_transcription.completed",
    "response.output_audio_transcript.done",
    "response.audio_transcript.done",
}

_APOS = str.maketrans({"\u2019": "'", "\u2018": "'", "`": "'"})
_FILLERS = frozenset(
    {"ok", "okay", "alright", "please", "hey", "yeah", "yep", "yup", "grok", "uh", "um", "oh"}
)
_CLOSERS = frozenset(
    {
        "thank you",
        "thanks",
        "thank you so much",
        "thanks a lot",
        "thanks so much",
        "thank you very much",
        "thanks very much",
        "thats all",
        "thats it",
        "that is all",
        "that is it",
        "thatll be all",
        "that will be all",
        "thats everything",
        "that is everything",
        "thats all thanks",
        "thats it thanks",
        "thanks thats all",
        "thanks thats it",
        "thank you thats all",
        "thank you thats it",
        "goodbye",
        "good bye",
        "bye",
        "bye bye",
        "stop listening",
        "please stop listening",
    }
)


def normalize_utterance(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_APOS).lower().replace("'", "")
    folded = re.sub(r"[^a-z0-9\s]", " ", folded)
    return " ".join(folded.split())


def strip_fillers(text: str) -> str:
    words = text.split()
    while words and words[0] in _FILLERS:
        words.pop(0)
    while words and words[-1] in _FILLERS:
        words.pop()
    return " ".join(word for word in words if word != "grok")


def is_closing_utterance(text: str | None) -> bool:
    """True when a completed user turn is a goodbye / that's-all phrase."""
    if not text or not str(text).strip():
        return False
    return strip_fillers(normalize_utterance(text)) in _CLOSERS


class ConversationWatch:
    """Idle only after the assistant is done and the user is not mid-utterance.

    xAI ``turn_detection.idle_timeout_ms`` does not close the session; it
    commits a silent user turn and generates a proactive check-in
    (``input_audio_buffer.timeout_triggered``). Session hang-up is ours.
    """

    def __init__(self) -> None:
        self.user_speaking = False
        self.assistant_busy = False

    def on_speech_started(self) -> None:
        self.user_speaking = True

    def on_speech_stopped(self) -> None:
        self.user_speaking = False

    def on_response_started(self) -> None:
        self.assistant_busy = True

    def on_response_done(self, awaiting_tools: bool = False) -> None:
        self.assistant_busy = awaiting_tools

    def is_quiet(self) -> bool:
        return not self.user_speaking and not self.assistant_busy


def xai_session_tools_log(event: dict) -> str | None:
    """Log line when xAI echoes the tools registered on the voice session."""
    if event.get("type") != "session.updated":
        return None
    session = event.get("session")
    if not isinstance(session, dict) or "tools" not in session:
        return None
    tools = session.get("tools")
    if not isinstance(tools, list):
        tools = []
    names = []
    for tool in tools:
        if isinstance(tool, dict):
            names.append(str(tool.get("name") or tool.get("type") or "unknown"))
    return f"xAI session tools count={len(names)} names={','.join(names)}"


def xai_realtime_error_log(event: dict) -> str | None:
    """Log line when xAI rejects session.update, including a bad tool schema."""
    if event.get("type") != "error":
        return None
    error = event.get("error")
    if isinstance(error, dict):
        detail = error.get("message") or error.get("type") or error
    else:
        detail = error or event.get("message") or event
    return f"xAI realtime error: {detail}"


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
            # server_vad ends the user turn. Do not set idle_timeout_ms: xAI
            # treats that as a check-in, not a hang-up. Session end uses our
            # idle_timeout_seconds after VAD-quiet (see ConversationWatch).
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
        if event_type == "input_audio_buffer.speech_stopped":
            return [{"type": "speech_stopped"}]
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
                return [
                    {
                        "type": "transcript",
                        "role": role,
                        "text": text,
                        "final": event_type in FINAL_TRANSCRIPT_TYPES,
                    }
                ]
        return []
