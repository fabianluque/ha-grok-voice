"""MCP auth uses the Supervisor add-on token, not an empty Bearer value."""

import json

import pytest

from app.config import load_settings
from app.mcp_client import McpError, McpHttpClient, bearer_header


def _options(tmp_path, payload: dict):
    path = tmp_path / "options.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_blank_long_lived_token_uses_supervisor_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "addon-token")
    monkeypatch.delenv("HASSIO_TOKEN", raising=False)
    settings = load_settings(_options(tmp_path, {"longlived_token": ""}), env_dirs=())
    assert settings.mcp_token == "addon-token"
    assert settings.mcp_token_source == "supervisor"
    assert bearer_header(settings.mcp_token) == "Bearer addon-token"


def test_whitespace_long_lived_token_does_not_replace_the_addon_token(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "addon-token")
    settings = load_settings(_options(tmp_path, {"longlived_token": "  "}), env_dirs=())
    assert settings.mcp_token == "addon-token"
    assert settings.mcp_token_source == "supervisor"


def test_long_lived_token_is_the_documented_401_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "addon-token")
    settings = load_settings(
        _options(tmp_path, {"longlived_token": "user-token"}),
        env_dirs=(),
    )
    assert settings.mcp_token == "user-token"
    assert settings.mcp_token_source == "long-lived"


def test_s6_container_environment_file_supplies_the_addon_token(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASSIO_TOKEN", raising=False)
    env_dir = tmp_path / "container_environment"
    env_dir.mkdir()
    (env_dir / "SUPERVISOR_TOKEN").write_text("addon-token\n", encoding="utf-8")
    settings = load_settings(_options(tmp_path, {}), env_dirs=(env_dir,))
    assert settings.mcp_token == "addon-token"
    assert settings.mcp_token_source == "supervisor"


def test_legacy_hassio_token_file_is_used_when_supervisor_token_is_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASSIO_TOKEN", raising=False)
    env_dir = tmp_path / "container_environment"
    env_dir.mkdir()
    (env_dir / "HASSIO_TOKEN").write_text("legacy-token", encoding="utf-8")
    settings = load_settings(_options(tmp_path, {"longlived_token": ""}), env_dirs=(env_dir,))
    assert settings.mcp_token == "legacy-token"


def test_default_area_is_blank_and_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "addon-token")
    settings = load_settings(_options(tmp_path, {}), env_dirs=())
    assert settings.default_area == ""
    assert settings.default_area_id == ""
    custom = load_settings(
        _options(tmp_path, {"default_area": "Kitchen", "default_area_id": "kitchen"}),
        env_dirs=(),
    )
    assert custom.default_area == "Kitchen"
    assert custom.default_area_id == "kitchen"


def test_home_location_and_memory_ttl_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "addon-token")
    settings = load_settings(_options(tmp_path, {}), env_dirs=())
    assert settings.home_location == ""
    assert settings.duplex_lan_host == ""
    assert settings.conversation_memory_ttl_seconds == 480
    assert settings.idle_timeout_seconds == 30


def test_duplex_lan_host_is_blank_and_accepts_a_lan_ip(tmp_path, monkeypatch):
    from app.config import clean_duplex_lan_host

    monkeypatch.setenv("SUPERVISOR_TOKEN", "addon-token")
    settings = load_settings(_options(tmp_path, {}), env_dirs=())
    assert settings.duplex_lan_host == ""
    custom = load_settings(
        _options(tmp_path, {"duplex_lan_host": " 192.168.86.38 "}),
        env_dirs=(),
    )
    assert custom.duplex_lan_host == "192.168.86.38"
    from_url = load_settings(
        _options(tmp_path, {"duplex_lan_host": "http://192.168.86.38:8123/"}),
        env_dirs=(),
    )
    assert from_url.duplex_lan_host == "192.168.86.38"
    assert clean_duplex_lan_host("") == ""
    assert clean_duplex_lan_host("homeassistant.local") == "homeassistant.local"


def test_missing_addon_token_stays_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASSIO_TOKEN", raising=False)
    settings = load_settings(_options(tmp_path, {}), env_dirs=())
    assert settings.mcp_token == ""
    assert settings.mcp_token_source == "missing"


def test_empty_token_is_not_sent_as_bearer_space():
    with pytest.raises(McpError, match="empty"):
        bearer_header("")
    with pytest.raises(McpError, match="empty"):
        bearer_header("  ")


class _Http:
    def __init__(self) -> None:
        self.headers = None

    async def post(self, url, json=None, headers=None):
        self.headers = headers
        return type("Response", (), {"status_code": 200, "text": '{"result":{"tools":[]}}', "headers": {}})()


def test_mcp_client_sends_the_addon_token():
    http = _Http()
    client = McpHttpClient("http://supervisor/core/api/mcp", "addon-token", http)

    import asyncio

    asyncio.run(client.list_tools())
    assert http.headers["Authorization"] == "Bearer addon-token"


def test_service_script_imports_the_container_environment():
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "rootfs" / "etc" / "services.d" / "grok" / "run"
    text = script.read_text(encoding="utf-8")
    assert "with-contenv" in text
    assert "python3 -m app.main" in text
