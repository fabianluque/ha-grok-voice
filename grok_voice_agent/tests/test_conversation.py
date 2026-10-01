"""Goodbye phrases and VAD-quiet idle, not raw mic activity."""

from pathlib import Path

from app.grok_session import (
    ConversationWatch,
    END_SESSION_TOOL,
    END_SESSION_TOOL_NAME,
    compose_instructions,
    is_closing_utterance,
    is_end_session_tool,
    is_home_control_tool,
    is_open_followup,
    merge_session_area,
    parse_client_area,
    parse_client_device,
    slug_area_id,
    with_area_instructions,
    with_qa_turn_instructions,
    with_session_end_instructions,
)
from app.memory import Turn


def test_closing_utterances_match_natural_variants():
    for text in (
        "thank you",
        "Thanks!",
        "thanks grok",
        "thanks a lot",
        "thank you so much",
        "OK that's all",
        "that’s it",
        "that's it, thanks",
        "thanks that's all",
        "that'll be all",
        "that'll do",
        "goodbye",
        "Good bye.",
        "good night",
        "goodnight",
        "oh, that's great, thank you",
        "that's great, thanks",
        "alright, goodbye",
        "ok bye",
        "that's all for now, thank you",
        "thanks I'm done",
        "I'm done",
        "we're done",
        "we're good",
        "we're all set",
        "all set",
    ):
        assert is_closing_utterance(text), text


def test_requests_are_not_closing_utterances():
    for text in (
        "thank you for turning on the lights",
        "thanks, now turn off the kitchen",
        "stop listening",
        "please stop listening",
        "stop listening to the radio",
        "that's all the lights in the attic",
        "could you thank you later for me",
        "don't stop listening until I say so",
        "you can go now",
        "you can go",
        "never mind",
        "never mind the kitchen lights",
        "that's enough",
        "that's everything",
        "carry on",
        "go now",
        "tell me when I'm done",
        "tell me when we're done",
        "",
        None,
    ):
        assert is_closing_utterance(text) is False


def test_area_instructions_scope_bare_room_commands():
    area = parse_client_area({"name": "Dining Room", "id": "dining_room"})
    text = with_area_instructions("Speak briefly.", area)
    assert "Dining Room" in text
    assert "dining_room" in text
    assert "lights" in text
    assert "music" in text
    assert "Music Assistant" in text
    assert "Do not ask which lights" in text
    assert "Do not ask which speaker" in text
    assert with_area_instructions("Speak briefly.", None) == "Speak briefly."
    assert parse_client_area({}) is None
    assert parse_client_area("attic") is None


def test_merge_session_area_prefers_kiosk_then_addon_default():
    settings = type("Settings", (), {"default_area": "Attic", "default_area_id": "attic"})()
    dining = merge_session_area({"name": "Dining Room", "id": "dining_room"}, settings)
    assert dining == {"name": "Dining Room", "id": "dining_room"}
    fallback = merge_session_area(None, settings)
    assert fallback == {"name": "Attic", "id": "attic"}
    named_only = merge_session_area({"name": "Attic"}, settings)
    assert named_only == {"name": "Attic", "id": "attic"}
    other_room = merge_session_area({"name": "Kitchen"}, settings)
    assert other_room == {"name": "Kitchen", "id": "kitchen"}
    blank = type("Settings", (), {"default_area": "", "default_area_id": ""})()
    assert merge_session_area(None, blank) is None
    assert slug_area_id("Dining Room") == "dining_room"
    assert parse_client_device({"name": "Attic Dashboard", "id": "attic-tablet"}) == {
        "name": "Attic Dashboard",
        "id": "attic-tablet",
    }
    assert parse_client_device({}) is None
    assert parse_client_device("attic") is None


def test_watch_is_quiet_only_after_assistant_done_and_user_not_speaking():
    watch = ConversationWatch()
    assert watch.is_quiet() is True
    watch.on_speech_started()
    assert watch.is_quiet() is False
    watch.on_speech_stopped()
    assert watch.is_quiet() is True
    watch.on_response_started()
    assert watch.is_quiet() is False
    watch.on_speech_started()
    watch.on_response_done()
    assert watch.is_quiet() is False
    watch.on_speech_stopped()
    assert watch.is_quiet() is True
    watch.on_response_done(awaiting_tools=True)
    assert watch.is_quiet() is False


def test_open_followups_are_questions_and_want_clauses():
    for text in (
        "The Mets won 4-2. Want last night's highlights?",
        "Want last night's highlights",
        "A concert Saturday. Want me to check Sunday too?",
        "Lights on. Anything else?",
        "Would you like the kitchen too?",
        "Should I dim them as well?",
    ):
        assert is_open_followup(text), text
    for text in (
        "Lights on.",
        "You're welcome.",
        "Anytime.",
        "The Mets won 4-2 last night.",
        "I want to be careful with that lock.",
        "",
        None,
    ):
        assert is_open_followup(text) is False, text


def test_end_session_tool_is_local_and_prompted():
    assert is_end_session_tool("end_session") is True
    assert is_end_session_tool("hang_up") is True
    assert is_end_session_tool("HassTurnOn") is False
    text = with_session_end_instructions("Speak briefly.")
    assert END_SESSION_TOOL_NAME in text
    assert "home device or in-home media" in text
    assert "Never call end_session after sports" in text
    assert "same turn as a question" in text
    assert "short first answer" in text
    assert "Do not ask a follow-up" in text
    assert "ONE brief offer" not in text
    assert "clarifying" in text
    assert "thank you" in text
    assert "Anything else" not in text
    description = str(END_SESSION_TOOL["description"])
    assert "keep listening" in description
    assert "do not ask a follow-up" in description
    assert "ONE brief offer" not in description
    assert "ask a brief follow-up instead" not in description


def test_qa_turn_policy_is_short_answer_then_stop():
    text = with_qa_turn_instructions("Speak briefly.")
    assert "Speak briefly." in text
    assert "short first answer" in text
    assert "Then STOP" in text
    assert "Do not ask a follow-up" in text
    assert "Do not offer more" in text
    assert "ONE brief offer" not in text
    assert "Want his term" not in text
    assert "Anything else" not in text
    assert "Do not hang up after Q&A" in text


def test_composed_instructions_put_qa_policy_after_history():
    text = compose_instructions(
        "Speak briefly.",
        history=[Turn("user", "what's this weekend"), Turn("assistant", "a concert Saturday")],
    )
    assert "what's this weekend" in text
    assert text.index("what's this weekend") < text.index("Q&A policy")
    assert text.rstrip().endswith("Do not hang up after Q&A.")
    assert "Never call end_session after sports" in text
    assert "Do not ask a follow-up" in text
    assert "ONE brief offer" not in text


def test_default_addon_instructions_match_qa_stop_policy():
    text = (Path(__file__).resolve().parents[1] / "config.yaml").read_text(encoding="utf-8")
    assert "short first answer" in text
    assert "Then STOP" in text
    assert "Do not ask a follow-up" in text
    assert "ONE brief offer" not in text
    assert "Anything else" not in text
    assert "Want his term" not in text


def test_home_control_tools_are_device_and_media_actions():
    for name in (
        "HassTurnOn",
        "intent__HassTurnOff",
        "light__HassLightSet",
        "HassOpenCover",
        "cover__HassCloseCover",
        "lock__HassLockLock",
        "climate__HassSetTemperature",
        "HassMediaSearchAndPlay",
        "music_assistant__play_media",
        "media_player__HassMediaPause",
    ):
        assert is_home_control_tool(name), name
    for name in (
        "GetLiveContext",
        "homeassistant__GetLiveContext",
        "HassGetState",
        "GetDateTime",
        "todo__HassListAddItem",
        "mealie__get_mealplan",
        "calendar__GetEvents",
        "web_search",
        "end_session",
        "",
        None,
    ):
        assert is_home_control_tool(name) is False, name
