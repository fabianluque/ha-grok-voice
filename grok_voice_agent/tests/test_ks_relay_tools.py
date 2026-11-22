"""KS relay tools: retry once without the tablet's room when nothing matched in it."""

import asyncio
import json

from app.ks_relay.ha import HaCache, Tablet
from app.ks_relay.tools import AREA_FALLBACK_TOOLS, HOME_WRITE_TOOLS, RelayTools


class AreaHa:
    """Home Assistant intents that only match when the slots fit what is really in each room."""

    def __init__(self, matches):
        self.matches = matches
        self.cache = HaCache(
            tablets=(Tablet("d1", "assist_satellite.kitchen", "Kitchen Dashboard", "kitchen", "Kitchen"),),
            exposed=frozenset({"todo.grocery", "climate.hallway"}),
            states={
                "todo.grocery": {"attributes": {"friendly_name": "Grocery"}},
                "climate.hallway": {"attributes": {"friendly_name": "Hallway thermostat"}},
            },
            entities={"climate.hallway": {"area_id": "hallway"}},
            areas={"hallway": {"name": "Hallway"}, "kitchen": {"name": "Kitchen"}},
        )
        self.intents = []
        self.services = []

    async def intent(self, name, data, *, device_id="", satellite_id=""):
        self.intents.append((name, dict(data)))
        if self.matches(name, data):
            return {"response_type": "action_done", "data": {"success": [{"type": "entity", "name": "matched"}]}}
        return {"response_type": "error", "data": {"code": "no_valid_targets"}}

    async def call_service(self, domain, service, data, **kwargs):
        self.services.append((domain, service, data, kwargs.get("target")))
        return None


def _tools(matches):
    ha = AreaHa(matches)
    return RelayTools(ha, ha.cache.tablets[0]), ha


def _no_area(name, data):
    return "area" not in data


def test_left_garage_in_the_kitchen_retries_without_the_area():
    tools, ha = _tools(_no_area)
    result = asyncio.run(tools.execute("open_close", {"action": "open", "name": "left garage", "area": "Kitchen"}))
    assert result["ok"] is True and result["area_dropped"] == "Kitchen"
    assert [data for _, data in ha.intents] == [
        {"name": "left garage", "area": "Kitchen", "domain": ["cover"]},
        {"name": "left garage", "domain": ["cover"]},
    ]


def test_each_home_write_tool_falls_back():
    calls = {
        "turn_on": {"name": "porch light", "area": "kitchen"},
        "turn_off": {"name": "porch light", "area": "Kitchen"},
        "set_light": {"name": "porch light", "area": "Kitchen", "brightness": 40},
        "lock": {"action": "lock", "name": "front door", "area": "Kitchen"},
        "fan_speed": {"name": "attic fan", "area": "Kitchen", "percentage": 30},
        "climate": {"name": "hallway", "area": "Kitchen", "temperature": 21},
    }
    for name, args in calls.items():
        tools, ha = _tools(_no_area)
        result = asyncio.run(tools.execute(name, args))
        assert result["ok"] is True and result.get("area_dropped") == "Kitchen", name
        assert len(ha.intents) == 2 and "area" not in ha.intents[1][1], name
    assert set(calls) | {"open_close", "todo"} == set(AREA_FALLBACK_TOOLS) == set(HOME_WRITE_TOOLS)


def test_climate_mode_matches_the_named_thermostat_in_another_room():
    tools, ha = _tools(_no_area)
    result = asyncio.run(tools.execute("climate", {"name": "hallway", "area": "Kitchen", "mode": "heat"}))
    assert result["ok"] is True and result["done"] == ["Hallway thermostat"]
    assert ha.services == [("climate", "set_hvac_mode", {"hvac_mode": "heat"}, {"entity_id": ["climate.hallway"]})]


def test_grocery_list_named_after_the_room_retries_with_the_plain_list():
    tools, ha = _tools(lambda name, data: data.get("name") == "grocery list")
    result = asyncio.run(tools.execute("todo", {"action": "add", "item": "milk", "list": "Kitchen grocery list"}))
    assert result["ok"] is True
    assert [data["name"] for _, data in ha.intents] == ["Kitchen grocery list", "grocery list"]

    tools, ha = _tools(lambda name, data: data.get("name") == "Grocery")
    result = asyncio.run(tools.execute("todo", {"action": "add", "item": "milk", "list": "kitchen list", "area": "Kitchen"}))
    assert result["ok"] is True
    assert [data["name"] for _, data in ha.intents] == ["kitchen list", "Grocery"]  # nothing left: default list


def test_no_retry_for_another_named_room_an_area_only_target_or_a_success():
    tools, ha = _tools(_no_area)
    result = asyncio.run(tools.execute("turn_on", {"name": "lamp", "area": "Bedroom"}))
    assert result["ok"] is False and len(ha.intents) == 1  # the user named that room

    tools, ha = _tools(_no_area)
    result = asyncio.run(tools.execute("turn_off", {"area": "Kitchen", "domain": "light"}))
    assert result["ok"] is False and len(ha.intents) == 1  # never widen to the whole house

    tools, ha = _tools(lambda name, data: True)
    result = asyncio.run(tools.execute("turn_on", {"name": "lamp", "area": "Kitchen"}))
    assert result["ok"] is True and "area_dropped" not in result and len(ha.intents) == 1

    tools, ha = _tools(lambda name, data: False)
    result = asyncio.run(tools.execute("turn_on", {"name": "lamp", "area": "Kitchen"}))
    assert result["ok"] is False and "area_dropped" not in result and len(ha.intents) == 2  # retried once only


def test_get_state_and_timers_do_not_use_the_write_fallback():
    tools, ha = _tools(lambda name, data: False)
    asyncio.run(tools.execute("timer", {"action": "start", "minutes": 5}))
    assert len(ha.intents) == 1


# calendar -----------------------------------------------------------------
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from app.ks_relay.dates import annotate_dates  # noqa: E402
from app.ks_relay.settings_store import DEFAULTS, TOOL_FAMILIES, validate  # noqa: E402
from app.ks_relay.skills import RESERVED  # noqa: E402
from app.ks_relay.tools import FAMILY_OF, calendar_window, run_tool, tool_specs  # noqa: E402

ZONE = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 21, 15, 0, tzinfo=ZONE)


class CalendarHa:
    """calendar.get_events with a canned response per entity."""

    def __init__(self, events=None, exposed=None):
        friendly = {
            "calendar.family": "Family",
            "calendar.weekly_menu": "Weekly Menu",
            "calendar.school": "School",
            "calendar.private": "Private",
        }
        self.cache = HaCache(
            exposed=frozenset(exposed if exposed is not None else {"calendar.family", "calendar.weekly_menu", "calendar.school", "light.porch"}),
            states={e: {"attributes": {"friendly_name": n}} for e, n in friendly.items()},
            time_zone="America/New_York",
        )
        self.events = events or {}
        self.services = []

    async def call_service(self, domain, service, data, **kwargs):
        self.services.append((domain, service, dict(data), kwargs.get("target"), kwargs.get("return_response")))
        return {e: {"events": self.events.get(e, [])} for e in (kwargs.get("target") or {}).get("entity_id", [])}


def _calendar(args, **ha_kwargs):
    ha = CalendarHa(**ha_kwargs)
    result = asyncio.run(RelayTools(ha).execute("calendar", args))
    return result, ha


def test_calendar_family_is_on_by_default_and_can_be_turned_off():
    assert FAMILY_OF["calendar"] == "calendar" and "calendar" in TOOL_FAMILIES
    assert DEFAULTS["tools"]["calendar"] == "direct"
    assert "calendar" in {s["name"] for s in tool_specs()}
    assert "calendar" in {s["name"] for s in tool_specs(validate({})["tools"])}
    off = validate({"tools": {"calendar": "off"}})["tools"]
    assert off["calendar"] == "off" and off["state"] == "direct"
    assert "calendar" not in {s["name"] for s in tool_specs(off)}
    assert "calendar" in RESERVED
    spec = next(s for s in tool_specs() if s["name"] == "calendar")
    assert "what's for dinner" in spec["description"] and "what's on the calendar" in spec["description"]


def test_calendar_uses_only_exposed_calendars():
    result, ha = _calendar({})
    assert result["ok"] is True
    domain, service, data, target, wants = ha.services[0]
    assert (domain, service, wants) == ("calendar", "get_events", True)
    assert target == {"entity_id": ["calendar.family", "calendar.school", "calendar.weekly_menu"]}
    assert set(data) == {"start_date_time", "end_date_time"}
    result, ha = _calendar({"name": "private"})
    assert result == {"ok": False, "error": "no exposed calendar matches"} and ha.services == []
    result, ha = _calendar({}, exposed={"light.porch"})
    assert result["ok"] is False and ha.services == []


def test_calendar_matches_a_named_calendar():
    _, ha = _calendar({"name": "school"})
    assert ha.services[0][3] == {"entity_id": ["calendar.school"]}
    result, ha = _calendar({"name": "soccer"})
    assert result["ok"] is False and ha.services == []


def test_calendar_meal_questions_prefer_meal_calendars():
    for args in ({"meal": True}, {"name": "dinner"}, {"name": "supper", "day": "tonight"}):
        _, ha = _calendar(args)
        assert ha.services[0][3] == {"entity_id": ["calendar.weekly_menu"]}, args
    _, ha = _calendar({"meal": True}, exposed={"calendar.family", "calendar.school"})
    assert ha.services[0][3] == {"entity_id": ["calendar.family", "calendar.school"]}
    _, ha = _calendar({"name": "menu"})
    assert ha.services[0][3] == {"entity_id": ["calendar.weekly_menu"]}


def test_calendar_windows():
    def window(args):
        start, end = calendar_window(args, ZONE, NOW)
        return start.isoformat(), end.isoformat()

    today = ("2026-11-21T00:00:00-05:00", "2026-11-22T00:00:00-05:00")
    assert window({}) == today
    assert window({"day": "today"}) == window({"day": "tonight"}) == window({"range": "tonight"}) == today
    assert window({"day": "tomorrow"}) == ("2026-11-22T00:00:00-05:00", "2026-11-23T00:00:00-05:00")
    assert window({"range": "yesterday"}) == ("2026-11-20T00:00:00-05:00", "2026-11-21T00:00:00-05:00")
    assert window({"range": "week"}) == ("2026-11-21T00:00:00-05:00", "2026-11-28T00:00:00-05:00")
    assert window({"day": "2026-11-25"}) == ("2026-11-25T00:00:00-05:00", "2026-11-26T00:00:00-05:00")
    assert window({"start": "2026-11-21", "end": "2026-11-23"}) == ("2026-11-21T00:00:00-05:00", "2026-11-24T00:00:00-05:00")
    assert window({"start": "2026-11-21T18:00:00", "end": "2026-11-21T21:00:00-05:00"}) == (
        "2026-11-21T18:00:00-05:00",
        "2026-11-21T21:00:00-05:00",
    )
    assert window({"start": "2026-11-21T18:00:00Z"}) == ("2026-11-21T18:00:00+00:00", "2026-11-22T00:00:00-05:00")
    for bad in ({"start": "2026-11-22", "end": "2026-11-20"}, {"day": "someday"}):
        try:
            calendar_window(bad, ZONE, NOW)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_calendar_returns_concise_events_with_day_labels():
    events = {
        "calendar.weekly_menu": [
            {"start": "2026-11-22", "end": "2026-11-23", "summary": "Soup", "uid": "x", "rrule": ""},
            {"start": "2026-11-21T18:00:00-05:00", "end": "2026-11-21T19:00:00-05:00", "summary": "Tacos",
             "description": "Make extra", "location": "Kitchen"},
        ]
    }
    ha = CalendarHa(events=events)
    output, ok, _ = asyncio.run(run_tool(RelayTools(ha), "calendar", json.dumps({"meal": True}), 5))
    result = json.loads(output)
    assert ok and result["calendars"] == ["Weekly Menu"]
    assert result["events"] == [
        {"calendar": "Weekly Menu", "summary": "Tacos", "start": "2026-11-21T18:00:00-05:00",
         "end": "2026-11-21T19:00:00-05:00", "location": "Kitchen", "description": "Make extra"},
        {"calendar": "Weekly Menu", "summary": "Soup", "start": "2026-11-22", "end": "2026-11-23"},
    ]
    labeled = annotate_dates(output, "America/New_York", now=NOW)
    assert "2026-11-21T18:00:00-05:00 (local Saturday 2026-11-21 6:00 PM, today)" in labeled
    assert '"2026-11-22 (Sunday, tomorrow)"' in labeled
    empty, _ = _calendar({"day": "tomorrow"})
    assert empty["ok"] is True and empty["events"] == [] and empty["say"]
