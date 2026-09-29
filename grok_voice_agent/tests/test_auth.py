"""Token checks must fail closed and must not open Grok."""

import asyncio
import json

from app.auth import HaAuth, redact, trusted_ingress_user
from app.config import HOME_ASSISTANT_API_URL, Settings
from app.server import VoiceConnection, handle_socket, ingress_user_from_socket


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeHttp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = []

    async def get(self, url, headers=None):
        self.calls.append((url, headers))
        return FakeResponse(self.status_code)


def _settings() -> Settings:
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
        idle_timeout_seconds=20,
        ha_api_url=HOME_ASSISTANT_API_URL,
    )


def test_missing_token_is_rejected_without_a_request():
    http = FakeHttp(200)

    async def check():
        return await HaAuth("http://supervisor/core", http).validate("  ")

    assert asyncio.run(check()) is False
    assert http.calls == []


def test_invalid_token_does_not_open_grok():
    http = FakeHttp(401)
    opened = []

    async def connect(_settings):
        opened.append(True)
        return object()

    async def check():
        connection = VoiceConnection(_settings(), http, connect)
        missing = await connection.authenticate("")
        rejected = await connection.authenticate("not-a-real-token")
        return missing, rejected

    missing, rejected = asyncio.run(check())
    assert missing is False
    assert rejected is False
    assert opened == []


def test_valid_token_opens_grok_once():
    http = FakeHttp(200)
    opened = []

    async def connect(_settings):
        opened.append(True)
        return object()

    async def check():
        connection = VoiceConnection(_settings(), http, connect)
        return await connection.authenticate("good-token")

    assert asyncio.run(check()) is True
    assert opened == [True]
    assert http.calls[0][0] == "http://127.0.0.1:8123/api/"
    assert "supervisor" not in http.calls[0][0]
    assert http.calls[0][1]["Authorization"] == "Bearer good-token"


def test_logs_redact_the_token_and_key():
    secret = "sk-live-do-not-print"
    assert secret not in redact(f"authorization {secret}", [secret])


def test_ingress_user_is_trusted_only_from_the_supervisor_peer():
    headers = {
        "X-Hass-Source": "core.ingress",
        "X-Remote-User-Id": "user-1",
    }
    assert trusted_ingress_user(headers, ("172.30.32.2", 44000)) == "user-1"
    assert trusted_ingress_user(headers, ("::ffff:172.30.32.2", 44000)) == "user-1"
    assert trusted_ingress_user(headers, ("10.1.1.5", 44000)) is None
    assert trusted_ingress_user(headers, ("127.0.0.1", 44000)) is None
    assert trusted_ingress_user({"X-Remote-User-Id": "user-1"}, ("172.30.32.2", 1)) is None
    assert trusted_ingress_user({"X-Hass-Source": "core.ingress"}, ("172.30.32.2", 1)) == "ingress"
    assert trusted_ingress_user(headers, None) is None


class _Socket:
    def __init__(self, raw, headers, remote, local_port):
        self._raw = raw
        self.sent = []
        self.request = type("Request", (), {"headers": headers})()
        self.remote_address = remote
        self.local_address = ("0.0.0.0", local_port)

    async def recv(self):
        if self._raw is None:
            await asyncio.sleep(30)
        raw = self._raw
        self._raw = None
        return raw

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        return None


class _Grok:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        return None


def _ingress_headers():
    return {
        "X-Hass-Source": "core.ingress",
        "X-Remote-User-Id": "user-1",
        "X-Ingress-Path": "/api/hassio_ingress/token",
    }


def test_ingress_socket_opens_grok_without_a_user_token():
    http = FakeHttp(401)
    grok = _Grok()

    async def connect(_settings):
        return grok

    socket = _Socket(
        json.dumps({"type": "auth", "via": "ingress"}),
        _ingress_headers(),
        ("172.30.32.2", 44000),
        8099,
    )

    async def run():
        await handle_socket(socket, _settings(), http, connect)

    asyncio.run(run())
    assert http.calls == []
    assert grok.sent
    assert any('"type": "ready"' in str(item) for item in socket.sent)


def test_spoofed_ingress_headers_on_the_debug_port_still_need_a_token():
    http = FakeHttp(401)
    opened = []

    async def connect(_settings):
        opened.append(True)
        return _Grok()

    socket = _Socket(
        json.dumps({"type": "auth", "via": "ingress"}),
        _ingress_headers(),
        ("172.30.32.2", 44000),
        8080,
    )

    async def run():
        await handle_socket(socket, _settings(), http, connect)

    asyncio.run(run())
    assert opened == []
    assert any('"reason": "unauthorized"' in str(item) for item in socket.sent)


def test_lan_client_cannot_spoof_ingress_headers():
    socket = _Socket("", _ingress_headers(), ("192.168.1.20", 44000), 8099)
    assert ingress_user_from_socket(socket, 8099) is None
