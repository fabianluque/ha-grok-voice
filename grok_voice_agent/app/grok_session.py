"""Grok Voice session configuration and event mapping.

Uplink audio is forwarded for the whole session, including while a reply is
playing. Barge-in is Grok server VAD plus an immediate speech_started event
so the browser can flush its own playback queue.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

from app.ha_context import HomeContext, merge_home_location, with_home_context
from app.mcp_client import bare_tool_name, tool_domain
from app.memory import Turn, with_history_instructions
from app.tools import ToolGateway

log = logging.getLogger("grok_voice")

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
# Extra quiet time after Grok asks a follow-up so idle does not fire while
# TTS is still draining or the user is thinking of an answer.
FOLLOWUP_IDLE_GRACE_SECONDS = 15
_FOLLOWUP_PHRASES = (
    "do you want",
    "would you like",
    "want me to",
    "anything else",
    "need anything",
    "shall i",
    "should i",
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


def is_open_followup(text: str | None) -> bool:
    """True when the assistant turn is still waiting for an answer.

    A question mark, a sentence-final ``Want …`` (ASR often drops ``?``),
    or a short ``want …?`` / ``anything else`` pattern. Home-command acks
    like ``Lights on.`` do not match.
    """
    if not text or not str(text).strip():
        return False
    raw = str(text).strip()
    if "?" in raw:
        return True
    if re.search(r"(?:^|[.!]+\s+)want\b", raw, flags=re.IGNORECASE):
        return True
    normalized = normalize_utterance(raw)
    return any(phrase in normalized for phrase in _FOLLOWUP_PHRASES)


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
        "through Music Assistant on the Music Assistant player in that area. "
        "Use HassMediaSearchAndPlay or "
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
        "('thank you', 'thanks', 'that's all', 'you can go', 'you can go now', "
        "'thanks I'm done', 'never mind', 'goodbye') or after a successful home "
        "device or in-home media action (lights, garage, lock, climate, cover, "
        "play/pause/volume) that already succeeded and needs no follow-up. Do "
        "not call after sports, news, events, history, or general Q&A — keep "
        "listening. After a short first answer you may ask one brief offer of "
        "more, then STOP; do not continue and answer that offer yourself. After "
        "a goodbye or thank you, do not ask a follow-up. Never call this in the "
        "same turn as a follow-up question. Do not call during a multi-step "
        "task, while asking a clarifying question, or when the user is listing "
        "several requests."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "enum": ["dismiss", "command"],
                "description": (
                    "dismiss = the user told you to go; "
                    "command = a Home Assistant device or in-home media action "
                    "finished successfully. Do not use command after answering "
                    "a question."
                ),
            }
        },
        "required": ["reason"],
    },
}
# Assist intents / service tools that change a device or play media in the home.
HOME_CONTROL_TOOLS = frozenset(
    {
        "HassTurnOn",
        "HassTurnOff",
        "HassToggle",
        "HassLightSet",
        "HassMediaPause",
        "HassMediaUnpause",
        "HassSetVolume",
        "HassSetVolumeRelative",
        "HassVolumeSet",
        "HassMediaNext",
        "HassMediaPrevious",
        "HassMediaPlayerMute",
        "HassMediaPlayerUnmute",
        "HassMediaSearchAndPlay",
        "play_media",
        "play_announcement",
        "HassOpenCover",
        "HassCloseCover",
        "HassSetCoverPosition",
        "HassSetPosition",
        "HassLock",
        "HassUnlock",
        "HassLockLock",
        "HassLockUnlock",
        "HassClimateSetTemperature",
        "HassSetTemperature",
        "HassSetHvacMode",
        "HassClimateSetHvacMode",
        "HassFanSetSpeed",
        "HassFanSetPresetMode",
        "HassSetValue",
    }
)
HOME_CONTROL_DOMAINS = frozenset(
    {
        "light",
        "switch",
        "fan",
        "cover",
        "lock",
        "climate",
        "media_player",
        "music_assistant",
        "scene",
        "script",
        "input_boolean",
        "humidifier",
        "vacuum",
        "button",
        "valve",
        "remote",
        "alarm_control_panel",
        "siren",
        "water_heater",
    }
)
HOME_QUERY_TOOLS = frozenset({"GetLiveContext", "GetDateTime", "HassGetState"})


def is_end_session_tool(name: object) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    bare = text.rsplit("__", 1)[-1]
    return bare in END_SESSION_ALIASES or text in END_SESSION_ALIASES


def is_home_control_tool(name: object) -> bool:
    """True for device / in-home media actions, not queries or conversation tools."""
    text = str(name or "").strip()
    if not text or is_end_session_tool(text):
        return False
    bare = bare_tool_name(text)
    if bare in HOME_QUERY_TOOLS:
        return False
    if bare in HOME_CONTROL_TOOLS:
        return True
    return tool_domain(text) in HOME_CONTROL_DOMAINS


def tool_output_failed(output: str) -> bool:
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(data, dict) and bool(data.get("error"))


def with_session_end_instructions(base: str) -> str:
    extra = (
        "Call end_session only in these cases: (1) the user dismissed you "
        "(goodbye, thank you, thanks, that's all, you can go), reason=dismiss; "
        "or (2) you just successfully ran a home device or in-home media action "
        "(lights, garage, locks, climate, covers, play/pause/volume on a house "
        "speaker) and you are not asking a question, reason=command. Speak a "
        "very short ack first. After thank you or goodbye, do not ask "
        "'anything else' — just ack and hang up. Never call end_session after "
        "sports, news, events, history, calendars, lists, trivia, or other "
        "conversation — not even with reason=dismiss. For those Q&A turns: "
        "short first answer, optional ONE brief offer of more, then STOP and "
        "wait. Never continue and answer that follow-up yourself. Never call "
        "end_session in the same turn as a follow-up question. Never hang up "
        "until the user answers, says goodbye, or goes silent. Do not hang up "
        "mid multi-step task or while waiting for a clarifying answer."
    )
    root = (base or "").rstrip()
    return f"{root}\n\n{extra}" if root else extra


def with_qa_turn_instructions(base: str) -> str:
    """Last-injected Q&A turn shape: short answer, optional one offer, stop.

    Hang-up policy stays in ``with_session_end_instructions``. This block is
    appended after history so the model does not monologue past the offer.
    """
    extra = (
        "Q&A policy (sports, events, history, news, trivia, facts): Give a "
        "short first answer to the question (one sentence, two max). You may "
        "then ask ONE brief offer of a follow-up (for example 'Want his term?' "
        "or 'Anything else?'). Then STOP and wait for the user. Never continue "
        "and answer that follow-up yourself in the same turn. Never monologue: "
        "long answer + 'want more?' + then keeping talking. "
        "Bad: 'George Washington. Want more? He served 1789 to 1797.' "
        "Good: 'George Washington was the first U.S. president. Want his term "
        "dates?' then silence. Do not hang up after Q&A."
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
    # Location is appended after customized Instructions, never replaced by them.
    text = with_home_context(base, context, now=now)
    text = with_area_instructions(text, area)
    text = with_session_end_instructions(text)
    text = with_history_instructions(text, history)
    # Q&A stop-after-offer last so history does not bury it.
    return with_qa_turn_instructions(text)


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
    home = merge_home_location(context, settings)
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
        self.home_control_this_turn = False
        self.closing_phrase_this_turn = False
        self._pending_end_reason: str | None = None
        self._assistant_partial = ""
        self._assistant_turn = ""
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
            if reason not in ("dismiss", "command"):
                reason = "dismiss"
            self._pending_end_reason = reason
            self.end_session_forget = reason == "dismiss"
            self.awaiting_tool_followup = True
            self._commit_end_session_if_allowed()
            ending = self.end_after_response
            output = {"ok": True, "ending": ending, "reason": reason}
            if not ending:
                output["keep_open"] = True
                if self.assistant_asked_followup():
                    output["error"] = "open_followup"
                elif reason == "command":
                    output["error"] = "home_control_required"
                else:
                    output["error"] = "closing_phrase_required"
            return {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": event.get("call_id"),
                    "output": json.dumps(output),
                },
            }
        output = await self.tools.execute(name, arguments)
        self.awaiting_tool_followup = True
        if is_home_control_tool(name) and not tool_output_failed(output):
            self.home_control_this_turn = True
            self._commit_end_session_if_allowed()
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
        self._commit_end_session_if_allowed()
        return {"type": "response.create"}

    def note_closing_phrase(self) -> None:
        """User said goodbye / thank you; hang up after the ack.

        A soft question in Grok's ack ('Anything else?') does not keep the
        session open. Overlay tap-dismiss is a separate client hang-up path.
        """
        self.closing_phrase_this_turn = True
        self._pending_end_reason = "dismiss"
        self.end_session_forget = True
        self._commit_end_session_if_allowed()

    def assistant_asked_followup(self) -> bool:
        return is_open_followup(self._assistant_turn or self._assistant_partial)

    def has_assistant_turn(self) -> bool:
        """True when this turn already has spoken/streamed assistant text."""
        return bool((self._assistant_turn or self._assistant_partial).strip())

    def idle_timeout_seconds(self, base: float) -> float:
        """Quiet-time hang-up. Follow-up questions get extra time to hear and answer."""
        timeout = float(base)
        if self.assistant_asked_followup():
            return timeout + FOLLOWUP_IDLE_GRACE_SECONDS
        return timeout

    def _record_assistant_text(self, text: str) -> None:
        piece = str(text or "").strip()
        if not piece:
            return
        current = (self._assistant_turn or "").strip()
        if not current:
            self._assistant_turn = piece
            return
        if piece.startswith(current) or current.startswith(piece):
            self._assistant_turn = piece if len(piece) >= len(current) else current
            return
        if piece in current:
            return
        self._assistant_turn = f"{current} {piece}"

    def _remember_client_transcript(self, payload: dict[str, Any]) -> None:
        if payload.get("role") == "assistant" and payload.get("text"):
            self._record_assistant_text(str(payload["text"]))

    def _abort_end_session(self) -> None:
        self.end_after_response = False
        self._pending_end_reason = None

    def _commit_end_session_if_allowed(self) -> None:
        reason = self._pending_end_reason
        if not reason:
            return
        # A detected goodbye / thank-you always arms hang-up, even when the
        # ack contains a soft "Anything else?". Q&A follow-ups without a
        # closer still refuse dismiss below.
        if reason == "dismiss" and self.closing_phrase_this_turn:
            self.end_after_response = True
            self.end_session_forget = True
            return
        if self.assistant_asked_followup():
            return
        if reason == "command" and self.home_control_this_turn:
            self.end_after_response = True
            self.end_session_forget = False

    def consume_end_session(self) -> bool:
        """True once tools are done and the ack turn (if any) has finished generating.

        Command hang-up is allowed only after a successful home-control tool this
        turn and only when the ack is not a follow-up question. Dismiss hangs up
        after a detected goodbye / thank-you even if Grok's ack is a soft
        question. ``reason=dismiss`` without a closer is ignored so Q&A
        follow-ups stay open. The browser then drains queued playback before
        closing the duplex.
        """
        self._commit_end_session_if_allowed()
        if self.assistant_asked_followup() and not self.closing_phrase_this_turn:
            if self.end_after_response or self._pending_end_reason:
                log.info("voice end_session skipped open_followup")
            self._abort_end_session()
            return False
        if not self.end_after_response or self.awaiting_tool_followup:
            if self._pending_end_reason and not self.awaiting_tool_followup:
                if self._pending_end_reason == "command":
                    log.info("voice end_session skipped no_home_control")
                else:
                    log.info("voice end_session skipped dismiss_without_closer")
                self._pending_end_reason = None
            return False
        if not self.end_session_forget and not self.home_control_this_turn:
            log.info("voice end_session skipped no_home_control")
            self._abort_end_session()
            return False
        if self.end_session_forget and not self.closing_phrase_this_turn:
            log.info("voice end_session skipped dismiss_without_closer")
            self._abort_end_session()
            return False
        self.end_after_response = False
        self._pending_end_reason = None
        self.home_control_this_turn = False
        return True

    def client_messages(self, event: dict) -> list[dict[str, Any]]:
        event_type = event.get("type")
        if event_type == "input_audio_buffer.speech_started":
            self.playing = False
            self._assistant_partial = ""
            self._user_partial = ""
            if not self.end_after_response:
                self.home_control_this_turn = False
                self._pending_end_reason = None
                self.closing_phrase_this_turn = False
                self._assistant_turn = ""
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
            self._remember_client_transcript(partial)
            return [partial]
        role = TRANSCRIPT_ROLES.get(event_type or "")
        if role:
            text = event.get("transcript") or event.get("delta") or ""
            if text:
                payload = _transcript_payload(
                    role,
                    str(text),
                    event_type in FINAL_TRANSCRIPT_TYPES,
                    event,
                )
                self._remember_client_transcript(payload)
                return [payload]
        item_transcript = _message_item_transcript(event)
        if item_transcript:
            self._remember_client_transcript(item_transcript)
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
