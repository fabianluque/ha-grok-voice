"""Date, timezone, and home location injected at session open."""

import asyncio
import json
from datetime import datetime, timezone

from app.config import Settings, load_settings
from app.grok_session import build_session
from app.ha_context import (
    HomeContext,
    fallback_home_context,
    fetch_home_context,
    format_home_location,
    format_local_now,
    parse_ha_config,
    with_home_context,
    with_zone_home,
)

NOW = datetime(2026, 9, 30, 15, 43, tzinfo=timezone.utc)


def _settings(**overrides) -> Settings:
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
        idle_timeout_seconds=20,
        ha_api_url="http://127.0.0.1:8123",
        home_location="Summit, NJ",
    )
    values.update(overrides)
    return Settings(**values)


def test_format_local_now_uses_the_clock_not_a_stale_constant():
    stamp = format_local_now("America/New_York", NOW)
    assert "Wednesday, September 30, 2026, 11:43 AM" in stamp
    assert "2026-09-30" in stamp
    assert "America/New_York" in stamp
    later = format_local_now("America/New_York", datetime(2027, 7, 4, 16, 0, tzinfo=timezone.utc))
    assert "Sunday, July 4, 2027" in later
    assert "2026-09-30" not in later


def test_location_prefers_addon_label_then_ha_coords():
    context = HomeContext(
        time_zone="America/New_York",
        location_name="Home",
        home_location="Summit, NJ",
        country="US",
        latitude=40.7155,
        longitude=-74.3646,
        zone_name="Home",
    )
    text = format_home_location(context)
    assert "Summit, NJ" in text
    assert "40.7155°N" in text
    assert "74.3646°W" in text
    assert "US" in text


def test_instructions_include_fresh_date_and_location():
    context = HomeContext(home_location="Summit, NJ", time_zone="America/New_York")
    text = with_home_context("Speak briefly.", context, now=NOW)
    assert "Speak briefly." in text
    assert "September 30, 2026" in text
    assert "Summit, NJ" in text
    assert "training data" in text


def test_session_update_gets_context_at_open():
    payload = build_session(
        _settings(),
        [],
        {"name": "Attic", "id": "attic"},
        context=HomeContext(home_location="Summit, NJ"),
        now=NOW,
    )
    text = payload["session"]["instructions"]
    assert "Attic" in text
    assert "September 30, 2026" in text
    assert "Summit, NJ" in text
    assert payload["session"]["turn_detection"]["prefix_padding_ms"] == 400
    assert payload["session"]["turn_detection"]["threshold"] == 0.4
    assert "idle_timeout_ms" not in str(payload)
    assert "silence_duration_ms" not in str(payload)


def test_parse_ha_config_and_zone_home():
    settings = _settings()
    context = parse_ha_config(
        {
            "time_zone": "America/New_York",
            "location_name": "Home",
            "latitude": 40.7155,
            "longitude": -74.3646,
            "country": "US",
        },
        settings,
    )
    assert context.time_zone == "America/New_York"
    assert context.home_location == "Summit, NJ"
    merged = with_zone_home(context, {"attributes": {"friendly_name": "Home", "latitude": 40.7}})
    assert merged.latitude == 40.7155
    assert fallback_home_context(settings).home_location == "Summit, NJ"


def test_fetch_home_context_reads_ha_config():
    class Http:
        async def get(self, url, headers=None):
            assert headers["Authorization"] == "Bearer supervisor-token"
            if url.endswith("/api/config"):
                return type(
                    "Response",
                    (),
                    {
                        "status_code": 200,
                        "text": json.dumps(
                            {
                                "time_zone": "America/New_York",
                                "location_name": "Home",
                                "latitude": 40.7155,
                                "longitude": -74.3646,
                                "country": "US",
                            }
                        ),
                    },
                )()
            return type(
                "Response",
                (),
                {
                    "status_code": 200,
                    "text": json.dumps(
                        {
                            "attributes": {
                                "friendly_name": "Home",
                                "latitude": 40.7155,
                                "longitude": -74.3646,
                            }
                        }
                    ),
                },
            )()

    context = asyncio.run(fetch_home_context(Http(), _settings()))
    assert context.time_zone == "America/New_York"
    assert context.home_location == "Summit, NJ"
    assert context.latitude == 40.7155


def test_load_settings_reads_home_location_and_memory_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "addon-token")
    path = tmp_path / "options.json"
    path.write_text(
        json.dumps({"home_location": "Summit, NJ", "conversation_memory_ttl_seconds": 600}),
        encoding="utf-8",
    )
    settings = load_settings(path, env_dirs=())
    assert settings.home_location == "Summit, NJ"
    assert settings.conversation_memory_ttl_seconds == 600
