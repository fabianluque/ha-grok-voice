"""KS relay tools: retry once without the tablet's room when nothing matched in it."""

import asyncio

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
