"""Goodbye phrases and VAD-quiet idle, not raw mic activity."""

from app.grok_session import (
    ConversationWatch,
    END_SESSION_TOOL_NAME,
    is_closing_utterance,
    is_end_session_tool,
    is_home_control_tool,
    merge_session_area,
    parse_client_area,
    parse_client_device,
    slug_area_id,
    with_area_instructions,
    with_session_end_instructions,
)


def test_closing_utterances_match_natural_variants():
    for text in (
        "thank you",
        "Thanks!",
        "thanks grok",
        "OK that's all",
        "that’s it",
        "that's it, thanks",
        "goodbye",
        "Good bye.",
        "stop listening",
        "please stop listening",
        "thank you so much",
        "oh, that's great, thank you",
        "that's great, thanks",
        "alright, goodbye",
        "ok bye",
        "that's all for now, thank you",
        "you can go now",
        "you can go",
        "thanks I'm done",
        "I'm done",
        "never mind",
        "all set",
    ):
        assert is_closing_utterance(text), text


def test_requests_are_not_closing_utterances():
    for text in (
        "thank you for turning on the lights",
        "thanks, now turn off the kitchen",
        "stop listening to the radio",
        "that's all the lights in the attic",
        "could you thank you later for me",
        "don't stop listening until I say so",
        "tell me when I'm done",
        "never mind the kitchen lights",
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


def test_end_session_tool_is_local_and_prompted():
    assert is_end_session_tool("end_session") is True
    assert is_end_session_tool("hang_up") is True
    assert is_end_session_tool("HassTurnOn") is False
    text = with_session_end_instructions("Speak briefly.")
    assert END_SESSION_TOOL_NAME in text
    assert "home device or in-home media" in text
    assert "Never call end_session after sports" in text
    assert "short follow-up" in text
    assert "clarifying" in text


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
