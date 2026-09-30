"""Goodbye phrases and VAD-quiet idle, not raw mic activity."""

from app.grok_session import ConversationWatch, is_closing_utterance


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
    ):
        assert is_closing_utterance(text), text


def test_requests_are_not_closing_utterances():
    for text in (
        "thank you for turning on the lights",
        "thanks, now turn off the kitchen",
        "stop listening to the radio",
        "that's all the lights in the attic",
        "",
        None,
    ):
        assert is_closing_utterance(text) is False


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
