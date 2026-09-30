"""Short per-device conversation memory with a sliding TTL."""

from app.grok_session import parse_client_device
from app.memory import ConversationMemory, SessionTranscript, Turn, conversation_key, with_history_instructions


def test_memory_key_prefers_device_then_area():
    device = parse_client_device({"name": "Attic Dashboard", "id": "attic-tablet"})
    area = {"name": "Attic", "id": "attic"}
    assert conversation_key(device, area) == "device:attic_tablet|area:attic"
    assert conversation_key(None, area) == "area:attic"
    assert conversation_key(device, None) == "device:attic_tablet"
    assert conversation_key(None, None) == "default"
    dining = parse_client_device({"name": "Dining Room Dashboard"})
    assert conversation_key(dining, {"name": "Dining Room", "id": "dining_room"}) != conversation_key(
        device, area
    )


def test_ttl_expires_and_goodbye_forgets():
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    memory = ConversationMemory(ttl_seconds=300, clock=now)
    key = "device:attic_tablet|area:attic"
    memory.remember(key, [Turn("user", "what's this weekend"), Turn("assistant", "a concert in Summit")])
    assert memory.get(key)[0].text == "what's this weekend"
    clock["t"] = 120
    assert len(memory.get(key)) == 2
    clock["t"] = 301
    assert memory.get(key) == []
    memory.remember(key, [Turn("user", "and tomorrow")])
    memory.forget(key)
    assert memory.get(key) == []


def test_idle_session_merges_new_turns_without_duplicating():
    memory = ConversationMemory(ttl_seconds=480)
    key = "device:attic"
    first = [Turn("user", "dim the lights"), Turn("assistant", "done")]
    memory.remember(key, first)
    memory.remember(key, first + [Turn("user", "warmer"), Turn("assistant", "warmer now")])
    texts = [turn.text for turn in memory.get(key)]
    assert texts == ["dim the lights", "done", "warmer", "warmer now"]


def test_history_is_injected_as_instructions_not_forever():
    text = with_history_instructions(
        "Speak briefly.",
        [Turn("user", "what's on this weekend"), Turn("assistant", "a concert Saturday")],
    )
    assert "Speak briefly." in text
    assert "what's on this weekend" in text
    assert "do not recap" in text.lower()


def test_session_transcript_keeps_final_turns_only():
    transcript = SessionTranscript()
    transcript.add("user", "turn on", final=False)
    transcript.add("user", "turn on the lights", final=True)
    transcript.add("assistant", "on", final=True)
    transcript.add("tool", "HassTurnOn", final=True)
    assert [(turn.role, turn.text) for turn in transcript.turns] == [
        ("user", "turn on the lights"),
        ("assistant", "on"),
    ]
