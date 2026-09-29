"""Token checks must fail closed and must not open Grok."""

import asyncio

from app.auth import HaAuth, redact
from app.config import Settings
from app.server import VoiceConnection


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
        ha_api_url="http://supervisor/core",
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
    assert http.calls[0][0] == "http://supervisor/core/api/"


def test_logs_redact_the_token_and_key():
    secret = "sk-live-do-not-print"
    assert secret not in redact(f"authorization {secret}", [secret])
