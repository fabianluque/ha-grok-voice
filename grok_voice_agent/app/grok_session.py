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
from datetime import datetime
from typing import Any

from app.ha_context import HomeContext, with_home_context
from app.memory import Turn, with_history_instructions
from app.tools import ToolGateway

AUDIO_DELTA_TYPES = {"response.output_audio.delta", "response.audio.delta"}
TRANSCRIPT_ROLES = {
    "conversation.item.input_audio_transcription.updated": "user",
    "conversation.item.input_audio_transcription.completed": "user",
    "response.output_audio_transcript.done": "assistant",
    "response.audio_transcript.done": "assistant",
    "response.output_text.done": "assistant",
    "response.text.done": "assistant",
}
FINAL_TRANSCRIPT_TYPES = {
    "conversation.item.input_audio_transcription.completed",
    "response.output_audio_transcript.done",
    "response.audio_transcript.done",
    "response.output_text.done",
    "response.text.done",
}
ASSISTANT_DELTA_TYPES = {
    "response.output_audio_transcript.delta",
    "response.audio_transcript.delta",
    "response.output_text.delta",
    "response.text.delta",
}
USER_DELTA_TYPES = {"conversation.item.input_audio_transcription.delta"}
# xAI name for OpenAI's incremental .delta: cumulative snapshots that may revise.
USER_SNAPSHOT_TYPES = {"conversation.item.input_audio_transcription.updated"}
# Echoes of conversation.item.create. created/added also fire for live audio;
# overlay streaming uses ASR / audio_transcript events instead.
ITEM_LIFECYCLE_TYPES = {
    "conversation.item.created",
    "conversation.item.added",
    "conversation.item.done",
}
# Content types used when reinjecting short per-device history (memory.py).
HISTORY_CONTENT_TYPES = {"input_text", "text"}

_APOS = str.maketrans({"\u2019": "'", "\u2018": "'", "`": "'"})
_FILLERS = frozenset(
    {"ok", "okay", "alright", "please", "hey", "yeah", "yep", "yup", "grok", "uh", "um", "oh"}
)
# Match the whole utterance, or as a suffix ("oh, that's great, thank you").
_SUFFIX_CLOSERS = frozenset(
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
        "thats all for now",
        "goodbye",
        "good bye",
        "bye",
        "bye bye",
        "stop listening",
        "please stop listening",
        "you can go",
        "you can go now",
        "you may go",
        "you may go now",
        "thats enough",
        "that is enough",
        "thanks im done",
        "thank you im done",
        "im done thanks",
        "im all set",
        "were good",
        "we are good",
        "were all set",
        "we are all set",
    }
)
# Short phrases that appear inside real requests ("tell me when I'm done").
_EXACT_CLOSERS = frozenset(
    {
        "im done",
        "i am done",
        "all set",
        "never mind",
        "nevermind",
        "carry on",
        "go now",
    }
)
_CLOSERS = _SUFFIX_CLOSERS | _EXACT_CLOSERS


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
    """True when a completed user turn is a goodbye / that's-all phrase.

    Exact match after normalize, or a closer at the end of the utterance
    (``oh, that's great, thank you``). A closer in the middle of a request
    (``thank you for turning on the lights``) is not a hang-up.
    """
    if not text or not str(text).strip():
        return False
    normalized = strip_fillers(normalize_utterance(text))
    if not normalized:
        return False
    if normalized in _CLOSERS:
        return True
    return any(normalized.endswith(f" {closer}") for closer in _SUFFIX_CLOSERS)


def parse_client_area(value: object) -> dict[str, str] | None:
    """Optional area sent on the voice auth message."""
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    area_id = str(value.get("id") or value.get("area_id") or "").strip()
    if not name and not area_id:
        return None
    if len(name) > 80:
        name = name[:80]
    if len(area_id) > 80:
        area_id = area_id[:80]
    area: dict[str, str] = {"name": name or area_id}
    if area_id:
        area["id"] = area_id
    return area


def parse_client_device(value: object) -> dict[str, str] | None:
    """Optional satellite/device identity sent on the voice auth message."""
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    device_id = str(
        value.get("id") or value.get("device_id") or value.get("deviceId") or ""
    ).strip()
    if not name and not device_id:
        return None
    if len(name) > 80:
        name = name[:80]
    if len(device_id) > 80:
        device_id = device_id[:80]
    device: dict[str, str] = {"name": name or device_id}
    if device_id:
        device["id"] = device_id
    return device


def slug_area_id(name: str) -> str:
    folded = unicodedata.normalize("NFKC", name).lower()
    folded = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")
    return folded[:80]


def merge_session_area(client: object, settings) -> dict[str, str] | None:
    """Client kiosk area wins; otherwise the add-on default_area / default_area_id."""
    parsed = parse_client_area(client) or {}
    default_name = str(getattr(settings, "default_area", "") or "").strip()
    default_id = str(getattr(settings, "default_area_id", "") or "").strip()
    name = parsed.get("name") or default_name
    area_id = parsed.get("id") or ""
    if not area_id and default_id:
        if not parsed.get("name") or parsed["name"].casefold() == default_name.casefold():
            area_id = default_id
    if not area_id and name:
        area_id = slug_area_id(name)
    return parse_client_area({"name": name, "id": area_id})


def with_area_instructions(base: str, area: dict[str, str] | None) -> str:
    """Tell Grok this satellite's room so bare 'the lights' stays local."""
    if not area:
        return base
    name = area.get("name") or area.get("id") or ""
    if not name:
        return base
    extra = (
        f"You are speaking from the {name} area of this home. "
        "When the user does not name another room, you MUST control lights, music, "
        "and other room-scoped devices in that area. Do not ask which lights or which room. "
        "When they ask to play music, a song, artist, album, playlist, or radio, play it "
        "through Music Assistant on the Music Assistant player in that area "
        "(the HomePod Mini when this area is Attic). Use HassMediaSearchAndPlay or "
        "music_assistant play_media with this area. Do not ask which speaker. "
        "Do not play on this tablet. "
        "Use GetLiveContext or HassGetState for exposed sensors, including Mealie meal "
        "plans, and todo / shopping-list tools for lists."
    )
    area_id = area.get("id")
    if area_id:
        extra += (
            f" Pass Home Assistant area `{name}` and area_id `{area_id}` on those tool calls."
        )
    else:
        extra += f" Pass Home Assistant area `{name}` on those tool calls."
    extra += " If they name a different room or speaker, use that instead."
    root = (base or "").rstrip()
    return f"{root}\n\n{extra}" if root else extra


END_SESSION_TOOL_NAME = "end_session"
END_SESSION_ALIASES = frozenset({END_SESSION_TOOL_NAME, "hang_up"})
END_SESSION_TOOL = {
    "type": "function",
    "name": END_SESSION_TOOL_NAME,
    "description": (
        "End this voice session and return the tablet to wake-word listening. "
        "Call after a brief spoken acknowledgment when the user dismisses you "
        "('you can go', 'you can go now', 'that's all', 'thanks I'm done', "
        "'never mind', 'goodbye') or after a simple one-shot home command "
        "(lights, garage, lock, volume, play/pause) that already succeeded "
        "and needs no follow-up. Do not call during a multi-step task, while "
        "asking a clarifying question, or when the user is listing several requests."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "enum": ["dismiss", "command"],
                "description": (
                    "dismiss = the user told you to go; "
                    "command = a short Home Assistant action finished successfully."
                ),
            }
        },
        "required": ["reason"],
    },
}


def is_end_session_tool(name: object) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    bare = text.rsplit("__", 1)[-1]
    return bare in END_SESSION_ALIASES or text in END_SESSION_ALIASES


def with_session_end_instructions(base: str) -> str:
    extra = (
        "When the user dismisses you, or after you complete one simple home "
        "command that succeeded and you are not asking a question, speak a "
        "very short acknowledgment and then call the end_session tool "
        "(reason=dismiss or reason=command). That hangs up and hands the "
        "microphone back to wake-word listening. Do not call end_session "
        "while a multi-step task is unfinished, while you still need a "
        "clarifying answer, or when they are giving a list of requests."
    )
    root = (base or "").rstrip()
    return f"{root}\n\n{extra}" if root else extra


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


# Soft server VAD with audio pre-roll so the first syllable after a snappy
# listen-start is not clipped. Do not set idle_timeout_ms (xAI check-in) or
# silence_duration_ms (leave the platform default for turn end).
VAD_THRESHOLD = 0.35
VAD_PREFIX_PADDING_MS = 800
SERVER_VAD = {
    "type": "server_vad",
    "threshold": VAD_THRESHOLD,
    "prefix_padding_ms": VAD_PREFIX_PADDING_MS,
}


def compose_instructions(
    base: str,
    area: dict[str, str] | None = None,
    context: HomeContext | None = None,
    history: list[Turn] | None = None,
    now: datetime | None = None,
) -> str:
    text = with_area_instructions(base, area)
    text = with_home_context(text, context, now=now)
    text = with_session_end_instructions(text)
    return with_history_instructions(text, history)


def build_session(
    settings,
    function_tools: list[dict],
    area: dict[str, str] | None = None,
    context: HomeContext | None = None,
    history: list[Turn] | None = None,
    now: datetime | None = None,
) -> dict:
    tools: list[dict] = list(function_tools)
    if not any(str(tool.get("name") or "") == END_SESSION_TOOL_NAME for tool in tools):
        tools.append(dict(END_SESSION_TOOL))
    if settings.enable_web_search:
        tools.append({"type": "web_search"})
    if settings.enable_x_search:
        tools.append({"type": "x_search"})
    home = context or HomeContext(home_location=str(getattr(settings, "home_location", "") or ""))
    return {
        "type": "session.update",
        "session": {
            "voice": settings.voice,
            "instructions": compose_instructions(
                settings.instructions,
                area,
                home,
                history,
                now=now,
            ),
            "reasoning": {"effort": settings.reasoning_effort},
            # server_vad ends the user turn. Do not set idle_timeout_ms: xAI
            # treats that as a check-in, not a hang-up. Session end uses our
            # idle_timeout_seconds after VAD-quiet (see ConversationWatch).
            "turn_detection": dict(SERVER_VAD),
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    # Live user captions. xAI emits cumulative
                    # conversation.item.input_audio_transcription.updated
                    # snapshots only when this is grok-transcribe (not OpenAI .delta).
                    "transcription": {"model": "grok-transcribe"},
                },
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
        self.end_after_response = False
        self.end_session_forget = False
        self._assistant_partial = ""
        self._user_partial = ""

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
            arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
        name = str(event.get("name") or "")
        if is_end_session_tool(name):
            reason = str(arguments.get("reason") or "dismiss").strip().casefold()
            self.end_after_response = True
            self.end_session_forget = reason == "dismiss"
            self.awaiting_tool_followup = True
            return {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": event.get("call_id"),
                    "output": json.dumps({"ok": True, "ending": True, "reason": reason}),
                },
            }
        output = await self.tools.execute(name, arguments)
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

    def consume_end_session(self) -> bool:
        """True once tools are done and the ack turn (if any) has finished generating.

        The browser then drains queued playback before closing the duplex.
        """
        if not self.end_after_response or self.awaiting_tool_followup:
            return False
        self.end_after_response = False
        return True

    def client_messages(self, event: dict) -> list[dict[str, Any]]:
        event_type = event.get("type")
        if event_type == "input_audio_buffer.speech_started":
            self.playing = False
            self._assistant_partial = ""
            self._user_partial = ""
            return [{"type": "speech_started"}]
        if event_type == "input_audio_buffer.speech_stopped":
            return [{"type": "speech_stopped"}]
        if event_type == "response.created":
            self._assistant_partial = ""
            response = event.get("response") or {}
            return [{"type": "response_started", "responseId": response.get("id")}]
        if event_type in AUDIO_DELTA_TYPES and event.get("delta"):
            self.playing = True
            return [{"type": "binary", "pcm": base64.b64decode(event["delta"])}]
        partial = _stream_transcript(self, event_type, event)
        if partial:
            return [partial]
        role = TRANSCRIPT_ROLES.get(event_type or "")
        if role:
            text = event.get("transcript") or event.get("delta") or ""
            if text:
                return [
                    _transcript_payload(
                        role,
                        str(text),
                        event_type in FINAL_TRANSCRIPT_TYPES,
                        event,
                    )
                ]
        item_transcript = _message_item_transcript(event)
        if item_transcript:
            return [item_transcript]
        return []


def _event_item_id(event: dict) -> str:
    raw = event.get("item_id") or event.get("itemId")
    if raw:
        return str(raw)
    item = event.get("item")
    if isinstance(item, dict) and item.get("id"):
        return str(item.get("id"))
    return ""


def _transcript_payload(role: str, text: str, final: bool, event: dict) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "transcript", "role": role, "text": text, "final": final}
    item_id = _event_item_id(event)
    if item_id:
        payload["itemId"] = item_id
    return payload


def _extend_partial(current: str, piece: str) -> str:
    """Merge a delta chunk that may be incremental or a cumulative snapshot."""
    chunk = str(piece or "")
    if not chunk:
        return current
    if not current:
        return chunk
    if chunk.startswith(current):
        return chunk
    if current.startswith(chunk):
        return current
    return current + chunk


def _stream_transcript(bridge: GrokBridge, event_type: object, event: dict) -> dict[str, Any] | None:
    """Forward in-progress user/assistant text as overlay upserts, not only *.done."""
    kind = str(event_type or "")
    if kind in ASSISTANT_DELTA_TYPES:
        piece = str(event.get("delta") or event.get("transcript") or event.get("text") or "")
        if not piece:
            return None
        bridge._assistant_partial = _extend_partial(bridge._assistant_partial, piece)
        if not bridge._assistant_partial.strip():
            return None
        return _transcript_payload("assistant", bridge._assistant_partial, False, event)
    if kind in USER_SNAPSHOT_TYPES:
        piece = str(event.get("transcript") or event.get("delta") or event.get("text") or "")
        if not piece.strip():
            return None
        bridge._user_partial = piece
        return _transcript_payload("user", piece, False, event)
    if kind in USER_DELTA_TYPES:
        piece = str(event.get("delta") or event.get("transcript") or event.get("text") or "")
        if not piece:
            return None
        bridge._user_partial = _extend_partial(bridge._user_partial, piece)
        if not bridge._user_partial.strip():
            return None
        return _transcript_payload("user", bridge._user_partial, False, event)
    if kind in FINAL_TRANSCRIPT_TYPES:
        role = TRANSCRIPT_ROLES.get(kind)
        if role == "assistant":
            bridge._assistant_partial = ""
        elif role == "user":
            bridge._user_partial = ""
    return None


def _is_reinjected_history_item(item: dict) -> bool:
    """True for short-memory turns we sent as conversation.item.create.

    Those echoes must stay in the Grok session and off the on-screen overlay.
    Live mic/assistant items carry audio content types (or a transcript).
    """
    content = item.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list) or not content:
        return False
    saw_history_text = False
    for part in content:
        if not isinstance(part, dict):
            return True
        kind = str(part.get("type") or "").casefold()
        if "audio" in kind or part.get("transcript"):
            return False
        if kind in HISTORY_CONTENT_TYPES or kind == "" or part.get("text"):
            saw_history_text = True
            continue
        return False
    return saw_history_text


def _message_item_transcript(event: dict) -> dict[str, Any] | None:
    """Fallback when xAI puts a *live* utterance on conversation.item.done.

    Skip created/added (history echoes and incomplete live items). Skip
    reinjected history even on item.done so a new wake's overlay is this
    session only. ASR updated/completed and audio_transcript deltas remain
    the overlay path.
    """
    if event.get("type") not in ITEM_LIFECYCLE_TYPES:
        return None
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "message":
        return None
    if _is_reinjected_history_item(item):
        return None
    if event.get("type") != "conversation.item.done":
        return None
    role = item.get("role")
    if role not in ("user", "assistant"):
        return None
    text = _item_text(item)
    if not text:
        return None
    return _transcript_payload(str(role), text, True, event)


def _item_text(item: dict) -> str:
    content = item.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("transcript") or part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
    elif item.get("transcript"):
        parts.append(str(item.get("transcript")))
    return " ".join(part for part in parts if part).strip()
