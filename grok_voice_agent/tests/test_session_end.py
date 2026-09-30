"""Session hang-up uses VAD-quiet idle and goodbye phrases, not mic PCM."""

from __future__ import annotations

import asyncio
import json

from app.config import HOME_ASSISTANT_API_URL, Settings
from app.server import handle_socket


class FakeResponse:
    def __init__(self, status_code: int, text: str = '{"result":{"tools":[]}}') -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {}


class FakeHttp:
    async def get(self, url, headers=None):
        if str(url).endswith("/api/config"):
            return FakeResponse(
                200,
                json.dumps(
                    {
                        "time_zone": "America/New_York",
                        "location_name": "Home",
                        "latitude": 40.7155,
                        "longitude": -74.3646,
                        "country": "US",
                    }
                ),
            )
        if str(url).endswith("/api/states/zone.home"):
            return FakeResponse(
                200,
                json.dumps(
                    {
                        "attributes": {
                            "friendly_name": "Home",
                            "latitude": 40.7155,
                            "longitude": -74.3646,
                        }
                    }
                ),
            )
        return FakeResponse(200)

    async def post(self, url, json=None, headers=None):
        return FakeResponse(200)


class QueueSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list = []
        self.local_address = ("0.0.0.0", 8080)
        self.remote_address = ("192.168.1.20", 44000)
        self.request = None

    async def recv(self):
        return await self.incoming.get()

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        return None


def _settings(idle: float = 0.2, **overrides) -> Settings:
    values = dict(
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
        idle_timeout_seconds=idle,
        ha_api_url=HOME_ASSISTANT_API_URL,
        home_location="Summit, NJ",
        conversation_memory_ttl_seconds=480,
    )
    values.update(overrides)
    return Settings(**values)


def _end_reason(sent: list) -> str | None:
    for item in reversed(sent):
        if not isinstance(item, str):
            continue
        try:
            payload = json.loads(item)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "end":
            return payload.get("reason")
    return None


async def _run_session(
    client: QueueSocket,
    grok: QueueSocket,
    idle: float = 0.2,
    memory=None,
    **settings_overrides,
) -> None:
    async def connect(_settings):
        return grok

    await handle_socket(client, _settings(idle, **settings_overrides), FakeHttp(), connect, memory=memory)


def test_pcm_alone_does_not_reset_idle_and_silence_ends_the_session():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(json.dumps({"type": "auth", "token": "good-token"}))
        task = asyncio.create_task(_run_session(client, grok, idle=0.15))
        await asyncio.sleep(0.05)
        await client.incoming.put(b"\x01\x02\x03\x04")
        await client.incoming.put(b"\x05\x06")
        await asyncio.wait_for(task, timeout=2)
        return client.sent, grok.sent

    sent, grok_sent = asyncio.run(run())
    assert _end_reason(sent) == "idle"
    assert any(b"input_audio_buffer.append" in item.encode() if isinstance(item, str) else False for item in grok_sent) or any(
        "input_audio_buffer.append" in str(item) for item in grok_sent
    )


def test_user_speech_holds_idle_until_assistant_finishes():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(json.dumps({"type": "auth", "token": "good-token"}))
        task = asyncio.create_task(_run_session(client, grok, idle=0.12))
        await asyncio.sleep(0.03)
        await grok.incoming.put(json.dumps({"type": "input_audio_buffer.speech_started"}))
        await asyncio.sleep(0.18)
        assert _end_reason(client.sent) is None
        await grok.incoming.put(json.dumps({"type": "input_audio_buffer.speech_stopped"}))
        await grok.incoming.put(json.dumps({"type": "response.created", "response": {"id": "r1"}}))
        await asyncio.sleep(0.18)
        assert _end_reason(client.sent) is None
        await grok.incoming.put(json.dumps({"type": "response.done"}))
        await asyncio.wait_for(task, timeout=2)
        return client.sent

    sent = asyncio.run(run())
    assert _end_reason(sent) == "idle"
    assert any('"type": "response_done"' in str(item) for item in sent)


def test_trailing_thank_you_ends_the_session():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(json.dumps({"type": "auth", "token": "good-token"}))
        task = asyncio.create_task(_run_session(client, grok, idle=5))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "oh, that's great, thank you",
                }
            )
        )
        await asyncio.wait_for(task, timeout=2)
        return client.sent

    sent = asyncio.run(run())
    assert _end_reason(sent) == "done"


def test_mid_request_thank_you_does_not_end_the_session():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(json.dumps({"type": "auth", "token": "good-token"}))
        task = asyncio.create_task(_run_session(client, grok, idle=0.2))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "thank you for turning on the lights",
                }
            )
        )
        await asyncio.wait_for(task, timeout=2)
        return client.sent

    sent = asyncio.run(run())
    assert _end_reason(sent) == "idle"


def test_thank_you_ends_the_session():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(json.dumps({"type": "auth", "token": "good-token"}))
        task = asyncio.create_task(_run_session(client, grok, idle=5))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Thank you",
                }
            )
        )
        await asyncio.wait_for(task, timeout=2)
        return client.sent

    sent = asyncio.run(run())
    assert _end_reason(sent) == "done"
    assert any("thank you" in str(item).lower() for item in sent)


def test_partial_thank_you_does_not_end_the_session():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(json.dumps({"type": "auth", "token": "good-token"}))
        task = asyncio.create_task(_run_session(client, grok, idle=0.2))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.updated",
                    "transcript": "thank you",
                }
            )
        )
        await asyncio.sleep(0.08)
        assert _end_reason(client.sent) is None
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.updated",
                    "transcript": "thank you for turning on the lights",
                }
            )
        )
        await asyncio.wait_for(task, timeout=2)
        return client.sent

    sent = asyncio.run(run())
    assert _end_reason(sent) == "idle"


def test_auth_area_is_written_into_session_instructions():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "area": {"name": "Attic", "id": "attic"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=0.1))
        await asyncio.wait_for(task, timeout=2)
        return grok.sent

    sent = asyncio.run(run())
    update = next(json.loads(item) for item in sent if isinstance(item, str) and "session.update" in item)
    assert "Attic" in update["session"]["instructions"]
    assert "attic" in update["session"]["instructions"]


def test_missing_client_area_uses_addon_default_attic():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(json.dumps({"type": "auth", "token": "good-token"}))
        task = asyncio.create_task(_run_session(client, grok, idle=0.1))
        await asyncio.wait_for(task, timeout=2)
        return grok.sent

    sent = asyncio.run(run())
    update = next(json.loads(item) for item in sent if isinstance(item, str) and "session.update" in item)
    assert "Attic" in update["session"]["instructions"]
    assert "Do not ask which lights" in update["session"]["instructions"]


def test_ready_includes_idle_timeout_seconds():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(json.dumps({"type": "auth", "token": "good-token"}))
        task = asyncio.create_task(_run_session(client, grok, idle=0.1))
        await asyncio.wait_for(task, timeout=2)
        return client.sent

    sent = asyncio.run(run())
    ready = next(json.loads(item) for item in sent if isinstance(item, str) and '"ready"' in item)
    assert ready["type"] == "ready"
    assert ready["idleTimeoutSeconds"] == 0.1


def test_session_update_injects_live_date_and_home_location():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(json.dumps({"type": "auth", "token": "good-token"}))
        task = asyncio.create_task(_run_session(client, grok, idle=0.1))
        await asyncio.wait_for(task, timeout=2)
        return grok.sent

    sent = asyncio.run(run())
    update = next(json.loads(item) for item in sent if isinstance(item, str) and "session.update" in item)
    text = update["session"]["instructions"]
    assert "Summit, NJ" in text
    assert "Summit, New Jersey" in text
    assert "America/New_York" in text
    assert "current local date and time" in text
    assert update["session"]["turn_detection"]["prefix_padding_ms"] == 800


def test_idle_keeps_short_history_for_the_same_device():
    from app.memory import ConversationMemory

    memory = ConversationMemory(ttl_seconds=480)

    async def first():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "area": {"name": "Attic", "id": "attic"},
                    "device": {"name": "Attic Dashboard", "id": "attic-tablet"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=5, memory=memory))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "what's happening this weekend",
                }
            )
        )
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "response.output_audio_transcript.done",
                    "transcript": "A concert in Summit on Saturday.",
                }
            )
        )
        await grok.incoming.put(json.dumps({"type": "response.done"}))
        await asyncio.sleep(0.03)
        await client.incoming.put(json.dumps({"type": "stop", "reason": "idle"}))
        await asyncio.wait_for(task, timeout=2)

    async def second():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "area": {"name": "Attic", "id": "attic"},
                    "device": {"name": "Attic Dashboard", "id": "attic-tablet"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=0.1, memory=memory))
        await asyncio.wait_for(task, timeout=2)
        return grok.sent

    asyncio.run(first())
    sent = asyncio.run(second())
    update = next(json.loads(item) for item in sent if isinstance(item, str) and "session.update" in item)
    text = update["session"]["instructions"]
    assert "what's happening this weekend" in text
    assert "concert in Summit" in text
    items = [json.loads(item) for item in sent if isinstance(item, str) and "conversation.item.create" in item]
    user_items = [item for item in items if item.get("item", {}).get("role") == "user"]
    assert any("what's happening this weekend" in str(item) for item in user_items)


def test_history_follows_device_not_area_fallback():
    from app.memory import ConversationMemory

    memory = ConversationMemory(ttl_seconds=480)
    device = {"name": "Dining Dashboard", "id": "dining-tablet"}

    async def first():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "area": {"name": "Attic", "id": "attic"},
                    "device": device,
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=5, memory=memory))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "I have a meeting today at 7pm",
                }
            )
        )
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "response.output_audio_transcript.done",
                    "transcript": "Okay, I'll remember that.",
                }
            )
        )
        await grok.incoming.put(json.dumps({"type": "response.done"}))
        await asyncio.sleep(0.03)
        await client.incoming.put(json.dumps({"type": "stop", "reason": "idle"}))
        await asyncio.wait_for(task, timeout=2)

    async def second():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "area": {"name": "Dining Room", "id": "dining_room"},
                    "device": device,
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=0.1, memory=memory))
        await asyncio.wait_for(task, timeout=2)
        return grok.sent

    asyncio.run(first())
    sent = asyncio.run(second())
    update = next(json.loads(item) for item in sent if isinstance(item, str) and "session.update" in item)
    assert "I have a meeting today at 7pm" in update["session"]["instructions"]
    assert "calendar" in update["session"]["instructions"].lower()
    assert any(
        "I have a meeting today at 7pm" in item
        for item in sent
        if isinstance(item, str) and "conversation.item.create" in item
    )


def test_attic_and_dining_devices_do_not_share_history():
    from app.memory import ConversationMemory

    memory = ConversationMemory(ttl_seconds=480)

    async def attic():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "area": {"name": "Attic", "id": "attic"},
                    "device": {"name": "Attic Dashboard", "id": "attic-tablet"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=5, memory=memory))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "attic only secret",
                }
            )
        )
        await grok.incoming.put(json.dumps({"type": "response.done"}))
        await asyncio.sleep(0.03)
        await client.incoming.put(json.dumps({"type": "stop", "reason": "idle"}))
        await asyncio.wait_for(task, timeout=2)

    async def dining():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "area": {"name": "Dining Room", "id": "dining_room"},
                    "device": {"name": "Dining Dashboard", "id": "dining-tablet"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=0.1, memory=memory))
        await asyncio.wait_for(task, timeout=2)
        return grok.sent

    asyncio.run(attic())
    sent = asyncio.run(dining())
    update = next(json.loads(item) for item in sent if isinstance(item, str) and "session.update" in item)
    assert "attic only secret" not in update["session"]["instructions"]


def test_updated_user_transcript_is_remembered_after_speech_stopped():
    from app.memory import ConversationMemory

    memory = ConversationMemory(ttl_seconds=480)

    async def first():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "device": {"name": "Attic Dashboard", "id": "attic-tablet"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=5, memory=memory))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.updated",
                    "transcript": "I have a meeting today at 7pm",
                }
            )
        )
        await grok.incoming.put(json.dumps({"type": "input_audio_buffer.speech_stopped"}))
        await grok.incoming.put(json.dumps({"type": "response.done"}))
        await asyncio.sleep(0.03)
        await client.incoming.put(json.dumps({"type": "stop", "reason": "idle"}))
        await asyncio.wait_for(task, timeout=2)

    async def second():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "device": {"name": "Attic Dashboard", "id": "attic-tablet"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=0.1, memory=memory))
        await asyncio.wait_for(task, timeout=2)
        return grok.sent

    asyncio.run(first())
    sent = asyncio.run(second())
    update = next(json.loads(item) for item in sent if isinstance(item, str) and "session.update" in item)
    assert "I have a meeting today at 7pm" in update["session"]["instructions"]


def test_goodbye_clears_history_for_that_device():
    from app.memory import ConversationMemory

    memory = ConversationMemory(ttl_seconds=480)

    async def first():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "device": {"name": "Attic Dashboard", "id": "attic-tablet"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=5, memory=memory))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "what's this weekend",
                }
            )
        )
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "thank you",
                }
            )
        )
        await asyncio.wait_for(task, timeout=2)

    async def second():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "device": {"name": "Attic Dashboard", "id": "attic-tablet"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=0.1, memory=memory))
        await asyncio.wait_for(task, timeout=2)
        return grok.sent

    asyncio.run(first())
    sent = asyncio.run(second())
    update = next(json.loads(item) for item in sent if isinstance(item, str) and "session.update" in item)
    assert "what's this weekend" not in update["session"]["instructions"]


def test_end_session_tool_hangs_up_after_the_ack_turn():
    async def run():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(json.dumps({"type": "auth", "token": "good-token"}))
        task = asyncio.create_task(_run_session(client, grok, idle=5))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "response.function_call_arguments.done",
                    "name": "end_session",
                    "call_id": "e1",
                    "arguments": json.dumps({"reason": "command"}),
                }
            )
        )
        await grok.incoming.put(json.dumps({"type": "response.done"}))
        await asyncio.sleep(0.03)
        assert _end_reason(client.sent) is None
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "response.output_audio_transcript.done",
                    "transcript": "Lights on.",
                }
            )
        )
        await grok.incoming.put(json.dumps({"type": "response.done"}))
        await asyncio.wait_for(task, timeout=2)
        return client.sent, grok.sent

    sent, grok_sent = asyncio.run(run())
    assert _end_reason(sent) == "done"
    assert any(item.get("type") == "response.create" for item in (json.loads(x) for x in grok_sent if isinstance(x, str) and x.startswith("{")))


def test_end_session_command_keeps_short_history():
    from app.memory import ConversationMemory

    memory = ConversationMemory(ttl_seconds=480)

    async def first():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "device": {"name": "Attic Dashboard", "id": "attic-tablet"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=5, memory=memory))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "turn on the lights",
                }
            )
        )
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "response.function_call_arguments.done",
                    "name": "end_session",
                    "call_id": "e1",
                    "arguments": json.dumps({"reason": "command"}),
                }
            )
        )
        await grok.incoming.put(json.dumps({"type": "response.done"}))
        await asyncio.sleep(0.03)
        await grok.incoming.put(
            json.dumps(
                {
                    "type": "response.output_audio_transcript.done",
                    "transcript": "On.",
                }
            )
        )
        await grok.incoming.put(json.dumps({"type": "response.done"}))
        await asyncio.wait_for(task, timeout=2)

    async def second():
        client = QueueSocket()
        grok = QueueSocket()
        await client.incoming.put(
            json.dumps(
                {
                    "type": "auth",
                    "token": "good-token",
                    "device": {"name": "Attic Dashboard", "id": "attic-tablet"},
                }
            )
        )
        task = asyncio.create_task(_run_session(client, grok, idle=0.1, memory=memory))
        await asyncio.wait_for(task, timeout=2)
        return grok.sent

    asyncio.run(first())
    sent = asyncio.run(second())
    update = next(json.loads(item) for item in sent if isinstance(item, str) and "session.update" in item)
    assert "turn on the lights" in update["session"]["instructions"]
