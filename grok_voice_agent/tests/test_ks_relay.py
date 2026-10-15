"""KS relay: session rewrite, auth, server-side tools hidden from the kiosk, settings, skills, memory."""

import asyncio
import json
import time
import socket
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from app.ks_relay import admin
from app.ks_relay.ha import HaCache, Tablet, build_tablets, find_tablet, identify_tablet, usable_ipv4
from app.ks_relay.memory import (
    MemorySettings,
    RelayMemory,
    SessionLog,
    compact_stored_fact,
    compact_tool_line,
    strip_ks_history,
)
from app.ks_relay.relay import (
    RelayContext,
    RelaySession,
    RelaySettings,
    _BACKGROUND,
    bearer_ok,
    clock_line,
    rewrite_session,
    serve_relay,
    strip_datetime_tool_hints,
)
from app.ks_relay.settings_store import DEFAULTS, SettingsError, SettingsStore, effective, validate
from app.ks_relay.stats import StatsStore, add_usage, percentile, sensor_states
from app.ks_relay.timeline import sanitize
from app.ks_relay.skills import SkillStore, load_seed, merge_import, preview, resolve, skill_specs, validate_skills
from app.ks_relay.tools import RelayTools, tool_specs

KS_SESSION = {
    "voice": "eve",
    "instructions": 'Be brief.\n\nYou run on a device named "Attic Dashboard" in Home Assistant. That is its name, '
    "not yours. The device is in the Attic area. When the user doesn't name an area, use this one.\n\n"
    "Earlier conversations with the user, oldest first. Use them when the user refers back to something:\n"
    "User: hi\nAssistant: hello",
    "turn_detection": {"type": "server_vad", "silence_duration_ms": 500, "prefix_padding_ms": 300},
    "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000}}},
    "tools": [
        {"type": "function", "name": "HassTurnOn", "parameters": {}},
        {"type": "function", "name": "end_conversation", "parameters": {"type": "object", "properties": {}}},
        {"type": "web_search"},
    ],
}


def _cfg(**over):
    return {**json.loads(json.dumps(DEFAULTS)), **over}


def test_rewrite_keeps_instructions_and_swaps_tools():
    mine = [{"type": "function", "name": "turn_on", "parameters": {}}]
    out = rewrite_session(KS_SESSION, _cfg(), mine, extra_instructions="It is noon.")
    assert out["turn_detection"] == {"type": "server_vad"}
    assert out["reasoning"] == {"effort": "none"}
    assert [t.get("name", t["type"]) for t in out["tools"]] == ["turn_on", "end_conversation", "web_search"]
    assert out["instructions"].startswith("Be brief.")
    assert out["instructions"].endswith("It is noon.")
    assert KS_SESSION["turn_detection"]["silence_duration_ms"] == 500  # input untouched


def test_rewrite_vad_and_reasoning_modes():
    kiosk = rewrite_session(KS_SESSION, _cfg(vad={"mode": "kiosk", "silence_ms": 0}, reasoning_effort="default"), [])
    assert kiosk["turn_detection"]["silence_duration_ms"] == 500 and "reasoning" not in kiosk
    custom = rewrite_session(KS_SESSION, _cfg(vad={"mode": "custom", "silence_ms": 200}), [])
    assert custom["turn_detection"] == {"type": "server_vad", "silence_duration_ms": 200}


def test_bearer_check_is_exact_and_fails_closed():
    assert bearer_ok("Bearer s3cret", "s3cret")
    assert not bearer_ok("Bearer nope", "s3cret")
    assert not bearer_ok(None, "s3cret")
    assert not bearer_ok("Bearer ", "")


def test_clock_line_and_datetime_hint_stripping():
    now = datetime(2026, 10, 15, 0, 1, tzinfo=ZoneInfo("America/New_York"))
    line = clock_line("America/New_York", now)
    assert line.startswith("Now: Thursday, Oct 15, 2026, 12:01 AM EDT. Tomorrow is Friday, Oct 16.")
    text = "Be brief. Always call GetDateTime before answering about dates. Use the tools."
    assert strip_datetime_tool_hints(text) == "Be brief. Use the tools."
    assert "GetDateTime" in rewrite_session({"instructions": text}, _cfg(inject_datetime=False), [])["instructions"]
    assert "get_datetime" not in {s["name"] for s in tool_specs()}


def test_tablet_from_ks_location_line():
    cache = HaCache(tablets=(Tablet("d1", "assist_satellite.attic", "Attic Dashboard", "attic", "Attic"),))
    assert find_tablet(cache, name="Attic Dashboard").device_id == "d1"
    assert find_tablet(cache, area="attic").device_id == "d1"
    assert find_tablet(cache, name="Kitchen") is None


def _kitchen_cache(*, ks_url="http://192.168.1.20:2324", ks_ip="192.168.1.20", pe_url="http://192.168.1.21:80"):
    devices = {
        "dev_ks": {
            "id": "dev_ks",
            "name": "Kitchen Dashboard",
            "manufacturer": "kiosk_satellite",
            "configuration_url": ks_url,
            "area_id": "kitchen",
        },
        "dev_pe": {
            "id": "dev_pe",
            "name": "Kitchen Voice PE",
            "manufacturer": "Home Assistant",
            "configuration_url": pe_url,
            "area_id": "kitchen",
        },
    }
    entities = {
        "assist_satellite.kitchen_dashboard": {"device_id": "dev_ks", "area_id": "kitchen"},
        "sensor.kitchen_dashboard_ipv4_address": {"device_id": "dev_ks"},
        "assist_satellite.kitchen_voice_pe": {"device_id": "dev_pe", "area_id": "kitchen"},
    }
    states = {
        "sensor.kitchen_dashboard_ipv4_address": {"entity_id": "sensor.kitchen_dashboard_ipv4_address", "state": ks_ip}
    }
    cache = HaCache(
        devices=devices,
        entities=entities,
        states=states,
        areas={"kitchen": {"name": "Kitchen", "area_id": "kitchen"}},
    )
    cache.tablets = build_tablets(cache)
    return cache


def test_usable_ipv4_strips_mapped_v6_and_drops_loopback():
    assert usable_ipv4("::ffff:192.168.1.20") == "192.168.1.20"
    assert usable_ipv4("127.0.0.1") == ""
    assert usable_ipv4("0.0.0.0") == ""
    assert usable_ipv4("not-an-ip") == ""


def test_build_tablets_collects_configuration_url_and_ipv4_sensor():
    cache = _kitchen_cache()
    ks = next(t for t in cache.tablets if t.device_id == "dev_ks")
    assert ks.ips == ("192.168.1.20",)
    assert ks.kind == "kiosk_satellite"
    pe = next(t for t in cache.tablets if t.device_id == "dev_pe")
    assert pe.ips == ("192.168.1.21",)
    assert pe.kind == ""


def test_build_tablets_uses_ipv4_sensor_when_url_is_a_hostname():
    cache = _kitchen_cache(ks_url="http://kitchen-dashboard.local:2324", ks_ip="192.168.1.24")
    ks = next(t for t in cache.tablets if t.device_id == "dev_ks")
    assert ks.ips == ("192.168.1.24",)
    assert ks.kind == "kiosk_satellite"


def test_identify_tablet_ip_after_device_query_before_area():
    cache = _kitchen_cache()
    tablet, method = identify_tablet(cache, device="Kitchen Dashboard", remote_ip="192.168.1.21")
    assert tablet.device_id == "dev_ks" and method == "query"
    tablet, method = identify_tablet(cache, remote_ip="192.168.1.20")
    assert tablet.device_id == "dev_ks" and method == "ha_ip"
    tablet, method = identify_tablet(cache, remote_ip="::ffff:192.168.1.20")
    assert tablet.device_id == "dev_ks" and method == "ha_ip"
    tablet, method = identify_tablet(cache, area="Kitchen")
    assert tablet.device_id == "dev_ks" and method == "area"
    tablet, method = identify_tablet(
        cache, remote_ip="192.168.1.33", ip_tablets={"192.168.1.33": "dev_ks"}
    )
    assert tablet.device_id == "dev_ks" and method == "assigned_ip"
    assert identify_tablet(cache, remote_ip="192.168.1.40") == (None, "")


def test_area_fallback_prefers_kiosk_satellite_over_voice_pe():
    cache = _kitchen_cache()
    assert len([t for t in cache.tablets if t.area_id == "kitchen"]) == 2
    assert find_tablet(cache, area="Kitchen").device_id == "dev_ks"


def test_assigned_ip_is_stored_in_settings(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    saved = store.save({"ip_tablets": {"192.168.1.22": "dev_ks"}})
    assert saved["ip_tablets"] == {"192.168.1.22": "dev_ks"}
    assert "ip_tablets" not in effective(saved, "dev_ks")
    with pytest.raises(SettingsError):
        validate({"ip_tablets": {"not-an-ip": "dev_ks"}})


def test_session_identifies_by_ip_when_ks_instructions_have_no_device_line(tmp_path, caplog):
    import logging

    cache = _kitchen_cache()
    context = RelayContext(
        ha=FakeHa(),
        settings=SettingsStore(tmp_path / "s.json"),
        skills=SkillStore(tmp_path / "k.json"),
    )
    context.ha.cache = cache

    class Ks:
        remote_address = ("192.168.1.20", 54321)

    session = RelaySession(
        Ks(),
        RelaySettings(xai_api_key="xai-secret-key", secret="relay-secret"),
        context,
        query={},
    )
    with caplog.at_level(logging.DEBUG, logger="grok_voice.ks_relay"):
        session._session_for_xai({"instructions": "Be brief. xai-secret-key must not leak.", "tools": []})
    assert session.label == "Kitchen Dashboard"
    assert any("identified via ha_ip from 192.168.1.20" in rec.getMessage() for rec in caplog.records)
    debug = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.DEBUG]
    assert debug and "Be brief." in debug[0]
    assert "xai-secret-key" not in debug[0]


def test_unknown_tablet_label_includes_remote_ip(tmp_path):
    context = RelayContext(
        ha=FakeHa(),
        settings=SettingsStore(tmp_path / "s.json"),
        skills=SkillStore(tmp_path / "k.json"),
    )

    class Ks:
        remote_address = ("192.168.1.33", 9)

    session = RelaySession(Ks(), RelaySettings(xai_api_key="x", secret="s"), context, query={})
    session._session_for_xai({"instructions": "Hello from a tablet.", "tools": []})
    assert session.label == "unknown tablet (192.168.1.33)"


def test_query_device_param_overrides_ip(tmp_path):
    cache = _kitchen_cache()
    context = RelayContext(
        ha=FakeHa(),
        settings=SettingsStore(tmp_path / "s.json"),
        skills=SkillStore(tmp_path / "k.json"),
    )
    context.ha.cache = cache

    class Ks:
        remote_address = ("192.168.1.21", 9)

    session = RelaySession(
        Ks(), RelaySettings(xai_api_key="x", secret="s"), context, query={"device": ["Kitchen Dashboard"]}
    )
    session._session_for_xai({"instructions": "No name here.", "tools": []})
    assert session.label == "Kitchen Dashboard"


def test_panel_lists_unmatched_ips_and_assignment(tmp_path):
    context = RelayContext(
        ha=FakeHa(),
        settings=SettingsStore(tmp_path / "s.json"),
        skills=SkillStore(tmp_path / "k.json"),
    )
    context.ha.cache = _kitchen_cache()
    context.status.open("192.168.1.33")

    async def run():
        loaded = await admin.handle(context, {"op": "load"})
        assigned = await admin.handle(
            context,
            {"op": "save_settings", "settings": {**loaded["settings"], "ip_tablets": {"192.168.1.33": "dev_ks"}}},
        )
        again = await admin.handle(context, {"op": "load"})
        return loaded, assigned, again

    loaded, assigned, again = asyncio.run(run())
    assert "192.168.1.33" in loaded["unmatched_ips"]
    kitchen = next(t for t in loaded["tablets"] if t["device_id"] == "dev_ks")
    assert "192.168.1.20" in kitchen["ips"]
    assert assigned["settings"]["ip_tablets"]["192.168.1.33"] == "dev_ks"
    assert "192.168.1.33" not in assigned["unmatched_ips"]
    kitchen2 = next(t for t in again["tablets"] if t["device_id"] == "dev_ks")
    assert "192.168.1.33" in kitchen2["assigned_ips"]


# settings ---------------------------------------------------------------------
def test_settings_validate_and_tablet_override(tmp_path):
    with pytest.raises(SettingsError) as err:
        validate({"reasoning_effort": "low", "memory": {"window_minutes": 0}, "bogus": 1})
    assert len(err.value.errors) == 3
    store = SettingsStore(tmp_path / "settings.json")
    saved = store.save({"tools": {"locks": "off"}, "tablets": {"d1": {"reasoning_effort": "high", "memory_enabled": False}}})
    assert saved["tools"]["locks"] == "off" and saved["tools"]["lights"] == "direct"
    mine = effective(saved, "d1")
    assert mine["reasoning_effort"] == "high" and mine["memory"]["enabled"] is False
    assert effective(saved, "other")["reasoning_effort"] == "none"
    assert "lock" not in {s["name"] for s in tool_specs(mine["tools"])}
    assert SettingsStore(tmp_path / "settings.json").get()["tools"]["locks"] == "off"  # persisted


# skills -----------------------------------------------------------------------
GUARDED = {
    "name": "notes",
    "index": "notes (save, find)",
    "help": "save {text}; find {query}.",
    "aliases": ["note"],
    "actions": [
        {"name": "save", "aliases": ["add"], "backend": {"type": "script", "target": "script.note_save", "fixed": {"mode": "add"}},
         "args": [{"name": "text", "required": True, "aliases": ["note"]}, {"name": "days", "aliases": [], "coerce": "day_list"}],
         "guard": {"arg": "text", "pattern": r"\bpassword\b", "say": "I don't store passwords."}},
        {"name": "find", "aliases": ["search"], "backend": {"type": "service", "target": "notes.search", "fixed": {}},
         "args": [{"name": "query", "required": True, "aliases": ["q"]}]},
    ],
}


def test_generic_seed_is_public_safe_and_media_is_direct():
    from app.ks_relay.skills import SEED_PATH

    skills = load_seed()
    assert [s["name"] for s in skills] == ["media", "routine", "page", "dashboard"]
    assert [t["name"] for t in preview(skills)["tools"]] == ["media"]  # skeletons start disabled
    raw = SEED_PATH.read_text().lower()
    for marker in ("person.", "device_tracker.", "@", "http://", "https://", "192.168."):
        assert marker not in raw  # nothing tied to one household


def test_skill_tool_description_and_promotion():
    skills = load_seed() + [validate_skills([GUARDED])[0]]
    specs = skill_specs(skills)
    assert [t["name"] for t in specs] == ["media", "skill"]
    assert specs[1]["description"] == "Skills (name, action, args): notes (save, find). action=help for details."
    for s in skills:
        if s["name"] == "media":
            s["direct"] = False
    specs = skill_specs(skills)
    assert [t["name"] for t in specs] == ["skill"] and "media (play" in specs[0]["description"]


def test_resolve_aliases_required_and_guard():
    skills = validate_skills([GUARDED])
    _, action, data = resolve(skills, "note", "add", {"note": "milk", "days": "mon, tue"})
    assert action["name"] == "save" and data == {"text": "milk", "days": ["mon", "tue"]}
    _, action, reply = resolve(skills, "notes", "find", {})
    assert action is None and "missing: query" in reply["error"]
    _, action, reply = resolve(skills, "notes", "save", {"text": "my password is 1234"})
    assert action is None and reply["error_kind"] == "guard"
    _, action, reply = resolve(skills, "notes", "help", {})
    assert action is None and reply["ok"] and reply["actions"] == ["save", "find"]


def test_import_from_ha_and_json(tmp_path):
    description = "Skills (name, action, args): weather = forecast {day}; media. action=help for details."
    merged, notes = merge_import(load_seed(), {"media": {"actions": ["play", "shuffle"], "help": "new help"}, "weather": {"actions": ["forecast"]}}, description)
    media = next(s for s in merged if s["name"] == "media")
    assert media["help"] == "new help"
    assert next(a for a in media["actions"] if a["name"] == "shuffle")["backend"]["type"] == "ha_skill"
    weather = next(s for s in merged if s["name"] == "weather")
    assert weather["index"] == "weather = forecast {day}" and len(notes) == 4
    store = SkillStore(tmp_path / "skills.json")
    assert not store.saved
    store.save(merged)
    assert store.saved and len(SkillStore(tmp_path / "skills.json").get()) == len(merged)
    from app.ks_relay.skills import merge_json

    combined, notes = merge_json(merged, {"skills": [GUARDED]})
    assert notes == ["added notes"] and combined[-1]["name"] == "notes"


# memory -----------------------------------------------------------------------
def test_memory_raw_fallback_and_context_block(tmp_path):
    async def run():
        memory = RelayMemory(tmp_path / "m.json", "")  # no key: summary falls back to raw
        log = SessionLog(tablet="d1", area="Attic")
        log.said("user", "Turn on the attic lights")
        log.tool("turn_on", '{"domain":"light"}', '{"ok": true, "say": "Turned on the lights"}', True)
        log.tool("skill", '{"name":"dashboard","action":"set","args":{"layout":"kitchen","layout_json":"{\\"tiles\\":[]}"}}', '{"ok": true, "say": "saved"}', True)
        log.said("assistant", "Attic lights are on.")
        cfg = MemorySettings()
        memory.finish(log, cfg)
        await memory.drain()
        block = memory.context_block("d1", cfg, "America/New_York")
        other = memory.context_block("d2", MemorySettings(house_window_minutes=0), "America/New_York")
        cleared = await memory.clear("d1")
        return block, other, cleared, RelayMemory(tmp_path / "m.json", "").items()

    block, other, cleared, after = asyncio.run(run())
    assert block.startswith("Recent context")
    assert "tool: turn_on ok" in block and "Attic lights are on" in block
    assert "tool: skill dashboard.set ok" in block
    assert "layout_json" not in block and "tiles" not in block
    assert '{"domain"' not in block
    assert other == "" and cleared == 1 and after == []
    assert "Earlier conversations" not in strip_ks_history(KS_SESSION["instructions"])
    assert "Attic area" in strip_ks_history(KS_SESSION["instructions"])


# end to end against a fake xAI ------------------------------------------------
class FakeHa:
    def __init__(self):
        self.cache = HaCache(
            tablets=(Tablet("d1", "assist_satellite.attic", "Attic Dashboard", "attic", "Attic"),),
            time_zone="America/New_York",
        )
        self.intents = []
        self.services = []
        self.ready = True

    async def intent(self, name, data, *, device_id="", satellite_id=""):
        self.intents.append((name, data, device_id))
        return {"response_type": "action_done", "speech": {"plain": {"speech": "Turned on the lights"}}, "data": {}}

    async def call_service(self, domain, service, data, **kwargs):
        self.services.append((domain, service, data))
        return {"ok": True, "say": f"{service} ok"}


async def _fake_xai(ws, seen):
    async for raw in ws:
        msg = json.loads(raw)
        seen.append(msg)
        kind = msg["type"]
        if kind == "session.update":
            await ws.send(json.dumps({"type": "session.updated", "session": msg["session"]}))
        elif kind == "input_audio_buffer.append" and msg["audio"] in ("bGlnaHQ=", "Ynll", "bXVzaWM="):
            name, args = {"bGlnaHQ=": ("turn_on", "{}"), "Ynll": ("end_conversation", "{}"), "bXVzaWM=": ("media", '{"action":"pause"}')}[msg["audio"]]
            await ws.send(json.dumps({"type": "input_audio_buffer.speech_started", "item_id": "u1"}))
            await ws.send(json.dumps({"type": "input_audio_buffer.speech_stopped", "item_id": "u1"}))
            await ws.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed", "transcript": "turn on the lights"}))
            await ws.send(json.dumps({"type": "response.created", "response": {"id": "r1"}}))
            item = {"id": "f1", "type": "function_call", "name": name, "call_id": "c1", "arguments": args}
            await ws.send(json.dumps({"type": "response.output_item.added", "response_id": "r1", "item": item}))
            await ws.send(json.dumps({"type": "response.function_call_arguments.done", "response_id": "r1",
                                      "item_id": "f1", "call_id": "c1", "name": name, "arguments": args}))
            await ws.send(json.dumps({"type": "response.done", "response": {"id": "r1", "output": [item]}}))
        elif kind == "response.create":
            await ws.send(json.dumps({"type": "response.created", "response": {"id": "r2"}}))
            await ws.send(json.dumps({"type": "response.output_audio.delta", "item_id": "a1", "delta": "AAAA"}))
            await ws.send(json.dumps({"type": "response.output_audio_transcript.done", "transcript": "Done."}))
            usage = {"total_tokens": 30, "input_tokens": 20, "output_tokens": 10,
                     "input_token_details": {"text_tokens": 15, "audio_tokens": 5, "cached_tokens": 4},
                     "output_token_details": {"text_tokens": 2, "audio_tokens": 8}}
            await ws.send(json.dumps({"type": "response.done", "response": {"id": "r2", "output": [], "usage": usage}}))


async def _round_trip(audio: str, tmp_path):
    seen, ha = [], FakeHa()
    context = RelayContext(ha=ha, settings=SettingsStore(tmp_path / "s.json"), skills=SkillStore(tmp_path / "k.json"),
                           memory=RelayMemory(tmp_path / "m.json", ""), stats=StatsStore(tmp_path / "stats.db"))
    async with serve(lambda ws: _fake_xai(ws, seen), "127.0.0.1", 0) as upstream:
        port = upstream.sockets[0].getsockname()[1]
        settings = RelaySettings(xai_api_key="xai-test", secret="s3cret", upstream_url=f"ws://127.0.0.1:{port}/v1/realtime")

        async def handler(ks):
            await RelaySession(ks, settings, context, query={"model": ["grok-voice-latest"]}).run()

        async with serve(handler, "127.0.0.1", 0) as relay:
            rport = relay.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{rport}/") as ks:
                await ks.send(json.dumps({"type": "session.update", "session": KS_SESSION}))
                assert json.loads(await ks.recv())["type"] == "session.updated"
                await ks.send(json.dumps({"type": "input_audio_buffer.append", "audio": audio}))
                got = []
                while True:
                    try:
                        got.append(json.loads(await asyncio.wait_for(ks.recv(), 1.0)))
                    except asyncio.TimeoutError:
                        break
        await asyncio.sleep(0.05)
    pending = [t for t in list(_BACKGROUND) if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return got, seen, ha, context


def test_relay_tool_runs_server_side_and_kiosk_only_hears_the_answer(tmp_path):
    got, seen, ha, context = asyncio.run(_round_trip("bGlnaHQ=", tmp_path))
    kinds = [m["type"] for m in got]
    assert "response.function_call_arguments.done" not in kinds
    assert "response.output_item.added" not in kinds
    assert kinds.count("response.done") == 1
    assert "response.output_audio.delta" in kinds
    assert ha.intents == [("HassTurnOn", {"area": "Attic"}, "d1")]
    report = context.report()
    assert report["totals"]["today"]["sessions"] == 1
    row = report["recent"][0]
    assert row["tablet"] == "Attic Dashboard" and row["turns"] == 1 and [t["name"] for t in row["tools"]] == ["turn_on"]
    assert row["usage"]["input_audio_tokens"] == 5 and row["usage"]["output_audio_tokens"] == 8 and row["usage"]["input_tokens"] == 20
    assert row["realtime_cost"] > 0 and row["ready_ms"] is not None
    kinds_tl = [e["kind"] for e in row["timeline"]]
    for needed in ("connect", "session.update", "upstream_open", "session.updated", "speech_started", "speech_stopped", "transcript", "first_audio", "tool", "response.done", "session_end"):
        assert needed in kinds_tl
    tool_ev = next(e for e in row["timeline"] if e["kind"] == "tool")
    assert tool_ev["name"] == "turn_on" and tool_ev["ok"] is True
    assert row["end_reason"] == "client_close"
    user_t = next(e for e in row["timeline"] if e["kind"] == "transcript" and e.get("role") == "user")
    assert "turn on the lights" in user_t["text"]
    sent = [m["type"] for m in seen]
    assert sent.index("conversation.item.create") < sent.index("response.create")
    session = seen[0]["session"]
    assert session["reasoning"] == {"effort": "none"}
    assert "silence_duration_ms" not in session["turn_detection"]
    assert "Now: " in session["instructions"]
    assert "Earlier conversations" not in session["instructions"]  # relay memory on: KS history dropped
    recent = context.status.snapshot()["recent"][0]
    assert recent["tablet"] == "Attic Dashboard" and recent["tools"][0]["name"] == "turn_on"


def test_direct_media_skill_runs_relay_handler(tmp_path):
    got, seen, ha, _ = asyncio.run(_round_trip("bXVzaWM=", tmp_path))
    assert ha.intents == [("HassMediaPause", {"area": "Attic"}, "d1")]


def test_end_conversation_reaches_the_kiosk(tmp_path):
    got, seen, ha, context = asyncio.run(_round_trip("Ynll", tmp_path))
    calls = [m for m in got if m["type"] == "response.function_call_arguments.done"]
    assert [c["name"] for c in calls] == ["end_conversation"]
    assert ha.intents == []
    row = context.report()["recent"][0]
    assert row["end_reason"] == "end_conversation"
    assert any(e["kind"] == "end_conversation" for e in row["timeline"])


def test_relay_server_rejects_a_wrong_secret(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    async def run():
        settings = RelaySettings(xai_api_key="x", secret="right", port=port, host="127.0.0.1")
        context = RelayContext(ha=FakeHa(), settings=SettingsStore(tmp_path / "s.json"), skills=SkillStore(tmp_path / "k.json"))
        task = asyncio.create_task(serve_relay(settings, context))
        await asyncio.sleep(0.3)
        status = None
        try:
            async with connect(f"ws://127.0.0.1:{port}/v1/realtime", additional_headers={"Authorization": "Bearer wrong"}):
                pass
        except InvalidStatus as exc:
            status = exc.response.status_code
        task.cancel()
        return status

    assert asyncio.run(run()) == 401


def test_panel_ops_validate_and_never_send_secrets(tmp_path):
    context = RelayContext(ha=FakeHa(), settings=SettingsStore(tmp_path / "s.json"), skills=SkillStore(tmp_path / "k.json"),
                           memory=RelayMemory(tmp_path / "m.json", "xai-secret-key"))
    context.ha.cache.services = {}

    async def run():
        loaded = await admin.handle(context, {"op": "load"})
        bad = await admin.handle(context, {"op": "save_settings", "settings": {"vad": {"mode": "fast"}}})
        good = await admin.handle(context, {"op": "save_settings", "settings": {"reasoning_effort": "high"}})
        skills = loaded["skills"]
        skills[0]["actions"][0]["backend"]["target"] = "not a script"
        bad_skill = await admin.handle(context, {"op": "save_skills", "skills": skills})
        return loaded, bad, good, bad_skill

    loaded, bad, good, bad_skill = asyncio.run(run())
    dumped = json.dumps(loaded, default=str)
    assert "xai-secret-key" not in dumped
    assert loaded["relay"]["secret_set"] is False
    assert loaded["relay"]["listening"] is False
    assert "no relay secret set" in loaded["relay"]["reason"]
    assert loaded["status"]["relay"]["reason"] == loaded["relay"]["reason"]
    assert bad["errors"] and good["settings"]["reasoning_effort"] == "high"
    assert any("relay handler" in e for e in bad_skill["errors"])


def test_panel_page_is_served_on_relay_path():
    from websockets.datastructures import Headers
    from websockets.http11 import Request

    from app.server import is_relay_panel_socket
    from app.static import process_http_request

    response = process_http_request(Request("/relay/", Headers()))
    assert response.status_code == 200 and b"Kiosk Satellite relay" in response.body
    root = process_http_request(Request("/", Headers()))
    assert root.status_code == 200 and b"Kiosk Satellite relay" in root.body
    assert b"relay/ws" in root.body
    assert "Loading…".encode() in root.body
    assert b"generate_secret" in root.body
    assert b"request timed out" in root.body
    assert b"Automatic retry stopped" in root.body
    assert b'allow="microphone; autoplay"' in root.body
    assert b"test/" in root.body
    assert b"Assign to tablet" in root.body
    assert b"Detected IP" in root.body
    assert b"function sessionDetail(" in root.body
    assert b"function timelineView(" in root.body
    assert b"cache_readonly_skills" in root.body
    assert b"cache_ro" in root.body

    class Sock:
        request = Request("/relay/ws", Headers())

    class IngressSock:
        request = Request("/api/hassio_ingress/token/relay/ws", Headers())

    class VoiceSock:
        request = Request("/test/", Headers())

    assert is_relay_panel_socket(Sock())
    assert is_relay_panel_socket(IngressSock())
    assert not is_relay_panel_socket(VoiceSock())


# dates ---------------------------------------------------------------------
def test_default_date_line_is_the_measured_wording():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.ks_relay.dates import date_line

    now = datetime(2026, 10, 15, 0, 10, tzinfo=ZoneInfo("America/New_York"))
    assert date_line("America/New_York", "", now) == (
        "Now: Thursday, Oct 15, 2026, 12:10 AM EDT. Tomorrow is Friday, Oct 16. Today = Thursday 2026-10-15. "
        "Tonight means today's date. Always resolve relative dates against this before answering or calling tools; "
        "pass explicit ISO dates to tools; when comparing tool results, match the ISO date exactly."
    )
    assert date_line("America/New_York", "It is {weekday} {date}.", now) == "It is Thursday 2026-10-15."
    assert date_line("America/New_York", "bad {nope}", now).startswith("Now: Thursday")


def test_annotate_tool_dates_utc_and_plain():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.ks_relay.dates import annotate_dates

    now = datetime(2026, 10, 15, 21, 0, tzinfo=ZoneInfo("America/New_York"))
    out = annotate_dates('{"date": "2026-10-16T00:30Z", "start": "2026-10-16", "end": "2026-10-17"}', "America/New_York", now)
    assert '"2026-10-16T00:30Z (local Thursday 2026-10-15 8:30 PM, today)"' in out
    assert '"2026-10-16 (Friday, tomorrow)"' in out and '"2026-10-17 (Saturday, in 2 days)"' in out
    assert annotate_dates("no dates", "America/New_York", now) == "no dates"


def test_date_settings_validate():
    import pytest

    from app.ks_relay.settings_store import SettingsError, validate

    merged = validate({"date_template": "Today {date}", "annotate_tool_dates": False})
    assert merged["date_template"] == "Today {date}" and merged["annotate_tool_dates"] is False
    with pytest.raises(SettingsError) as err:
        validate({"date_template": "Today {when}"})
    assert "unknown field" in err.value.errors[0]


# stats -----------------------------------------------------------------------
def test_usage_flattening_and_percentiles():
    total = add_usage({}, {"input_tokens": 3, "input_token_details": {"audio_tokens": 2, "cached_tokens": 1}, "flag": True})
    add_usage(total, {"input_tokens": 1, "output_token_details": {"audio_tokens": 4}})
    assert total == {"input_tokens": 4, "input_audio_tokens": 2, "input_cached_tokens": 1, "output_audio_tokens": 4}
    assert percentile([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000], 90) == 900
    assert percentile([], 90) is None


def test_stats_totals_daily_tablets_and_retention(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 10, 15, 21, 0, tzinfo=zone).timestamp()
    store = StatsStore(tmp_path / "stats.db")
    costs = {"realtime_per_min": 0.08, "summary_input_per_m": 1.25, "summary_output_per_m": 2.5, "retention_days": 90}

    def ses(started, minutes, tablet, lat):
        return {"tablet": tablet, "device_id": tablet, "started": started, "ended": started + minutes * 60, "ready_ms": 300,
                "turns": lat, "tools": [{"name": "turn_on", "ok": True, "ms": 40}], "usage": {"input_tokens": 5}}

    assert store.record_session(ses(now - 3600, 1.5, "a", [500, 700]), costs)["realtime_cost"] == 0.12
    store.record_session(ses(now - 3 * 86400, 2, "b", [900]), costs)
    store.record_session(ses(now - 20 * 86400, 1, "a", [400]), costs)
    store.record_summary("a", "grok-4.20-0309-non-reasoning", 400_000, 100_000, costs, ts=now - 60)  # 0.5 + 0.25
    report = store.report("America/New_York", now=now, names={"a": "Attic"})
    today, week, month = report["totals"]["today"], report["totals"]["7d"], report["totals"]["30d"]
    assert (today["sessions"], today["minutes"], today["realtime_cost"], today["summary_cost"]) == (1, 1.5, 0.12, 0.75)
    assert today["cost"] == 0.87 and today["latency_median"] == 600 and today["tool_calls"] == 1
    assert week["sessions"] == 2 and month["sessions"] == 3 and month["realtime_cost"] == 0.36
    assert len(report["daily"]) == 30 and report["daily"][-1]["date"] == "2026-10-15" and report["daily"][-1]["summary_cost"] == 0.75
    attic = next(t for t in report["tablets"] if t["tablet"] == "Attic")
    assert attic["sessions"] == 2 and attic["summary_cost"] == 0.75 and attic["latency_p90"] == 700
    assert report["recent"][0]["tablet"] == "Attic" and report["recent"][0]["latency_median"] == 600
    store.record_session(ses(time.time(), 1, "c", []), {**costs, "retention_days": 1})
    assert not store._rows("SELECT 1 FROM sessions WHERE started < ?", (time.time() - 86400,))  # older rows pruned


def test_sensor_states_and_cost_settings():
    from app.ks_relay.settings_store import SettingsError, validate

    states = dict((e, (s, a)) for e, s, a in sensor_states({"cost": 0.87, "sessions": 2, "minutes": 3.5, "latency_median": None,
                                                            "realtime_cost": 0.12, "summary_cost": 0.75, "latency_p90": None}))
    assert states["sensor.grok_relay_cost_today"][0] == 0.87 and states["sensor.grok_relay_cost_today"][1]["estimate"] is True
    assert states["sensor.grok_relay_latency_median"][0] == "unknown"
    merged = validate({"costs": {"realtime_per_min": 0.05}, "ha_sensors": True})
    assert merged["costs"]["realtime_per_min"] == 0.05 and merged["costs"]["summary_input_per_m"] == 1.25 and merged["ha_sensors"]
    import pytest

    with pytest.raises(SettingsError):
        validate({"costs": {"realtime_per_min": -1}})


def test_summary_usage_is_priced(tmp_path):
    import httpx

    def reply(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Lights on in the attic."}}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})

    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
        memory = RelayMemory(tmp_path / "m.json", "xai-test", http=http)
        context = RelayContext(ha=FakeHa(), settings=SettingsStore(tmp_path / "s.json"), skills=SkillStore(tmp_path / "k.json"),
                               memory=memory, stats=StatsStore(tmp_path / "stats.db"))
        log = SessionLog(tablet="d1", area="Attic")
        log.said("user", "Turn on the attic lights")
        log.said("assistant", "Done.")
        memory.finish(log, MemorySettings())
        await memory.drain()
        await http.aclose()
        return context.report()

    report = asyncio.run(run())
    assert report["totals"]["today"]["summary_cost"] == round(1000 / 1e6 * 1.25 + 100 / 1e6 * 2.5, 4)
    assert report["tablets"][0]["tablet"] == "Attic Dashboard"


def test_realtime_basis_billable_audio(tmp_path):
    store = StatsStore(tmp_path / "stats.db")
    entry = {"tablet": "a", "started": 0.0 + time.time() - 120, "ended": time.time(), "usage": {"billable_audio_seconds": 30}}
    assert store.record_session(entry, {"realtime_per_min": 0.08})["realtime_cost"] == 0.16
    assert store.record_session(entry, {"realtime_per_min": 0.08, "realtime_basis": "billable_audio"})["realtime_cost"] == 0.04


class FakeListener:
    def __init__(self) -> None:
        self.secret = ""
        self.applied: list[str] = []

    def public_status(self) -> dict:
        on = bool(self.secret)
        return {
            "listening": on,
            "port": 8766,
            "secret_set": on,
            "reason": "" if on else "no relay secret set",
        }

    async def apply_secret(self, secret: str) -> dict:
        self.secret = secret
        self.applied.append(secret)
        return self.public_status()


def test_generate_secret_is_shown_once_never_on_load(tmp_path):
    context = RelayContext(ha=FakeHa(), settings=SettingsStore(tmp_path / "s.json"), skills=SkillStore(tmp_path / "k.json"))
    context.listener = FakeListener()

    async def run():
        generated = await admin.handle(context, {"op": "generate_secret"})
        loaded = await admin.handle(context, {"op": "load"})
        short = await admin.handle(context, {"op": "set_secret", "secret": "short"})
        return generated, loaded, short

    generated, loaded, short = asyncio.run(run())
    once = generated["secret_once"]
    assert len(once) >= 12
    assert generated["secret_set"] is True
    assert generated["relay"]["listening"] is True
    dumped = json.dumps(loaded, default=str)
    assert once not in dumped
    assert "secret_once" not in dumped
    assert loaded["relay"]["secret_set"] is True
    assert short["errors"]

    async def set_existing():
        return await admin.handle(context, {"op": "set_secret", "secret": "another-long-secret"})

    saved = asyncio.run(set_existing())
    assert "secret_once" not in saved
    assert "another-long-secret" not in json.dumps(saved, default=str)
    assert saved["relay"]["secret_set"] is True


def test_persist_relay_secret_writes_options_and_does_not_log_it(tmp_path, caplog):
    import logging

    from app.ks_relay.addon import SUPERVISOR_OPTIONS_URL, persist_relay_secret

    options = tmp_path / "options.json"
    options.write_text('{"xai_api_key": "", "ks_relay_secret": ""}\n', encoding="utf-8")

    class FakeHttp:
        def __init__(self):
            self.posts = []

        def post(self, url, json=None, headers=None):
            self.posts.append((url, json, headers))
            return type("R", (), {"status_code": 200})()

    http = FakeHttp()
    with caplog.at_level(logging.DEBUG):
        persist_relay_secret("panel-generated-secret", options, token="addon-token", http=http)
    saved = json.loads(options.read_text(encoding="utf-8"))
    assert saved["ks_relay_secret"] == "panel-generated-secret"
    assert saved["xai_api_key"] == ""
    assert http.posts[0][0] == SUPERVISOR_OPTIONS_URL
    assert http.posts[0][1]["options"]["ks_relay_secret"] == "panel-generated-secret"
    assert "panel-generated-secret" not in caplog.text
    assert "addon-token" not in caplog.text

    partial = tmp_path / "partial.json"
    partial.write_text("{}\n", encoding="utf-8")
    skipped = FakeHttp()
    persist_relay_secret("panel-generated-secret", partial, token="addon-token", http=skipped)
    assert skipped.posts == []
    assert json.loads(partial.read_text(encoding="utf-8"))["ks_relay_secret"] == "panel-generated-secret"


def test_listener_stays_off_without_secret_and_starts_on_apply(tmp_path):
    from app.ks_relay.addon import RelayListener

    class _Settings:
        xai_api_key = "x"
        ks_relay_secret = ""
        ks_relay_port = 0
        mcp_token = ""

    context = RelayContext(ha=FakeHa(), settings=SettingsStore(tmp_path / "s.json"), skills=SkillStore(tmp_path / "k.json"))
    listener = RelayListener(_Settings(), context, options_path=str(tmp_path / "options.json"), host="127.0.0.1")
    assert listener.public_status()["listening"] is False
    assert listener.public_status()["reason"] == "no relay secret set"

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        chosen = probe.getsockname()[1]
    listener.port = chosen

    async def apply_and_probe():
        status = await listener.apply_secret("long-enough-secret")
        await asyncio.sleep(0.2)
        code = None
        try:
            async with connect(
                f"ws://127.0.0.1:{chosen}/v1/realtime",
                additional_headers={"Authorization": "Bearer wrong"},
            ):
                pass
        except InvalidStatus as exc:
            code = exc.response.status_code
        await listener._stop_server()
        return status, code

    status, code = asyncio.run(apply_and_probe())
    assert status["listening"] is True and status["secret_set"] is True
    assert code == 401
    saved = json.loads((tmp_path / "options.json").read_text(encoding="utf-8"))
    assert saved["ks_relay_secret"] == "long-enough-secret"


class _PanelSock:
    def __init__(self, path, headers, remote, local_port, messages=()):
        self.request = type("Request", (), {"path": path, "headers": headers})()
        self.remote_address = remote
        self.local_address = ("0.0.0.0", local_port)
        self.sent = []
        self.closed = None
        self._messages = list(messages)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def send(self, data):
        self.sent.append(data)


def test_panel_socket_reasons_distinguish_ingress_from_missing_context():
    from app.config import HOME_ASSISTANT_API_URL, Settings
    from app.server import handle_incoming, relay_panel_close_reason

    settings = Settings(
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
        ingress_port=8099,
    )
    headers = {"X-Hass-Source": "core.ingress", "X-Remote-User-Id": "user-1"}
    ingress = _PanelSock("/relay/ws", headers, ("172.30.32.2", 1), 8099)
    lan = _PanelSock("/relay/ws", headers, ("192.168.1.20", 1), 8099)
    debug = _PanelSock("/relay/ws", headers, ("172.30.32.2", 1), 8080)
    context = RelayContext(ha=FakeHa())

    assert relay_panel_close_reason(ingress, settings, context) is None
    assert relay_panel_close_reason(ingress, settings, None) == "relay panel is not running"
    assert relay_panel_close_reason(lan, settings, context) == "relay panel needs Home Assistant ingress"
    assert relay_panel_close_reason(debug, settings, context) == "relay panel needs Home Assistant ingress"

    async def run():
        missing = _PanelSock("/relay/ws", headers, ("172.30.32.2", 1), 8099)
        await handle_incoming(missing, settings, None, None, None)
        blocked = _PanelSock("/relay/ws", headers, ("192.168.1.20", 1), 8099)
        await handle_incoming(blocked, settings, None, None, context)
        ok = _PanelSock("/relay/ws", headers, ("172.30.32.2", 1), 8099, [json.dumps({"op": "load", "id": 1})])
        await handle_incoming(ok, settings, None, None, context)
        return missing.closed, blocked.closed, ok.sent, ok.closed

    missing_closed, blocked_closed, sent, ok_closed = asyncio.run(run())
    assert missing_closed == (1011, "relay panel is not running")
    assert blocked_closed == (4403, "relay panel needs Home Assistant ingress")
    assert ok_closed is None
    loaded = json.loads(sent[0])
    assert loaded["id"] == 1
    assert loaded["relay"]["secret_set"] is False
    assert "secret_once" not in loaded


def test_sanitize_redacts_secret_keys_and_known_values_and_truncates():
    blob = sanitize(
        {"api_key": "xai-test-key", "name": "kitchen", "note": "token xai-test-key in text", "body": "n" * 50},
        secrets=["xai-test-key"],
        max_chars=20,
    )
    assert blob["api_key"] == "[redacted]"
    assert blob["name"] == "kitchen"
    assert "[redacted]" in blob["note"] and "xai-test-key" not in blob["note"]
    assert blob["body"].startswith("n" * 20) and "chars]" in blob["body"]
    assert sanitize({"input_tokens": 9, "output_tokens": 3}) == {"input_tokens": 9, "output_tokens": 3}


def test_compact_tool_lines_drop_args_and_results():
    assert compact_tool_line("turn_on", '{"domain":"light"}', True) == "turn_on ok"
    assert compact_tool_line("skill", '{"name":"dashboard","action":"set","args":{"layout_json":"huge"}}', False) == "skill dashboard.set failed"
    old = 'skill {"name": "dashboard", "action": "catalog"} -> ok: {"tiles": [1, 2]}'
    assert compact_stored_fact(old) == "skill dashboard.catalog ok"
    assert compact_stored_fact("turn_on ok") == "turn_on ok"
    truncated = 'skill {"name":"dashboard","action":"set","args":{"layout_json":"' + "x" * 80
    assert compact_stored_fact(truncated + '"} -> ok: saved') == "skill dashboard.set ok"


def test_readonly_skill_cache_hits_and_invalidates_on_write(tmp_path):
    skills = load_seed()
    for skill in skills:
        if skill["name"] == "dashboard":
            skill["enabled"] = True
    ha = FakeHa()
    tools = RelayTools(ha, skills=skills, cache_readonly=True)

    async def run():
        first = await tools.execute("skill", {"name": "dashboard", "action": "help"})
        second = await tools.execute("skill", {"name": "dashboard", "action": "help"})
        cat1 = await tools.execute("skill", {"name": "dashboard", "action": "catalog"})
        cat2 = await tools.execute("skill", {"name": "dashboard", "action": "catalog"})
        await tools.execute("skill", {"name": "dashboard", "action": "set", "args": {"layout": "kitchen", "layout_json": "{}"}})
        cat3 = await tools.execute("skill", {"name": "dashboard", "action": "catalog"})
        return first, second, cat1, cat2, cat3

    first, second, cat1, cat2, cat3 = asyncio.run(run())
    assert first.get("ok") is True and second.get("_cached") is True
    assert cat1.get("_cached") is not True and cat2.get("_cached") is True
    assert cat3.get("_cached") is not True
    catalogs = [c for c in ha.services if c[1] == "dashboard_catalog"]
    sets = [c for c in ha.services if c[1] == "dashboard_set_layout"]
    assert len(catalogs) == 2 and len(sets) == 1


def test_readonly_skill_cache_can_be_disabled():
    skills = load_seed()
    for skill in skills:
        if skill["name"] == "dashboard":
            skill["enabled"] = True
    ha = FakeHa()
    tools = RelayTools(ha, skills=skills, cache_readonly=False)

    async def run():
        await tools.execute("skill", {"name": "dashboard", "action": "catalog"})
        await tools.execute("skill", {"name": "dashboard", "action": "catalog"})

    asyncio.run(run())
    assert len([c for c in ha.services if c[1] == "dashboard_catalog"]) == 2


def test_cache_readonly_skills_defaults_on():
    merged = validate({})
    assert merged["cache_readonly_skills"] is True
    assert validate({"cache_readonly_skills": False})["cache_readonly_skills"] is False


def test_stats_stores_timeline_and_migrates_old_schema(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE sessions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tablet TEXT, device_id TEXT, started REAL, ended REAL, duration_s REAL,
          ready_ms INTEGER, turns INTEGER, latencies TEXT, tools TEXT, usage TEXT,
          realtime_cost REAL, error TEXT
        );
        """
    )
    now = time.time()
    db.execute(
        "INSERT INTO sessions (tablet, device_id, started, ended, duration_s, ready_ms, turns, latencies, tools, usage, realtime_cost, error)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("Kitchen", "dev_ks", now - 10, now, 10, 80, 1, "[300]", "[]", "{}", 0.01, ""),
    )
    db.commit()
    db.close()
    store = StatsStore(path)
    row = store.report()["recent"][0]
    assert row["timeline"] == [] and row["end_reason"] == ""
    store.record_session(
        {
            "tablet": "Kitchen",
            "started": now,
            "ended": now + 5,
            "turns": [200],
            "timeline": [{"t": 0, "kind": "connect"}, {"t": 5000, "kind": "session_end", "reason": "idle"}],
            "end_reason": "idle",
        },
        {"realtime_per_min": 0.08},
    )
    latest = store.report()["recent"][0]
    assert latest["end_reason"] == "idle"
    assert [e["kind"] for e in latest["timeline"]] == ["connect", "session_end"]


def test_empty_summary_is_not_stored_and_old_empty_rows_are_skipped(tmp_path):
    import httpx

    def reply(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "NONE"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 1}})

    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
        memory = RelayMemory(tmp_path / "m.json", "xai-test", http=http)
        log = SessionLog(tablet="d1", area="Kitchen")
        log.said("user", "edit the dashboard")
        log.tool("skill", '{"name":"dashboard","action":"catalog"}', '{"ok": true, "tiles": ["a"]}', True)
        memory.finish(log, MemorySettings())
        await memory.drain()
        memory._items.append({
            "ts": time.time(), "started": time.time(), "tablet": "d1", "area": "Kitchen",
            "kind": "raw", "text": "", "facts": ['skill {"name":"dashboard","action":"catalog"} -> ok: {"tiles":["a"]}'],
        })
        memory._items.append({
            "ts": time.time(), "started": time.time(), "tablet": "d1", "area": "Kitchen",
            "kind": "summary", "text": "Opened the dashboard.",
            "facts": ['skill {"name":"dashboard","action":"set","args":{"layout":"kitchen","layout_json":"{\\"tiles\\":[1]}"}} -> ok: saved'],
        })
        block = memory.context_block("d1", MemorySettings(), "UTC")
        shown = memory.items()
        await http.aclose()
        return block, shown

    block, shown = asyncio.run(run())
    assert all(i.get("text") for i in shown)
    assert "Opened the dashboard" in block
    assert "skill dashboard.set ok" in block
    assert "layout_json" not in block
    assert "tiles" not in block
    assert block.count("tool:") == 1
