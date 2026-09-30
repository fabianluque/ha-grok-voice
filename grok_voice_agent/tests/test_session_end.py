"""Session hang-up uses VAD-quiet idle and goodbye phrases, not mic PCM."""

from __future__ import annotations

import asyncio
import json

from app.config import HOME_ASSISTANT_API_URL, Settings
from app.server import handle_socket


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = '{"result":{"tools":[]}}'
        self.headers = {}


class FakeHttp:
    async def get(self, url, headers=None):
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


def _settings(idle: float = 0.2) -> Settings:
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
        idle_timeout_seconds=idle,
        ha_api_url=HOME_ASSISTANT_API_URL,
    )


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


async def _run_session(client: QueueSocket, grok: QueueSocket, idle: float = 0.2) -> None:
    async def connect(_settings):
        return grok

    await handle_socket(client, _settings(idle), FakeHttp(), connect)


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
