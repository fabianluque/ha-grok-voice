"""Home timezone and location for the voice session prompt.

Computed at session open so event questions use *this* local date, not a
stale training-data calendar. Location comes from add-on ``home_location``
(Summit, NJ / zip) plus Home Assistant ``/api/config`` and ``zone.home``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("grok_voice")

FALLBACK_TIME_ZONE = "America/New_York"
CONFIG_FETCH_TIMEOUT_SECONDS = 0.8


@dataclass(frozen=True)
class HomeContext:
    time_zone: str = FALLBACK_TIME_ZONE
    location_name: str = ""
    home_location: str = ""
    country: str = ""
    latitude: float | None = None
    longitude: float | None = None
    zone_name: str = ""


def fallback_home_context(settings) -> HomeContext:
    return HomeContext(home_location=str(getattr(settings, "home_location", "") or "").strip())


def _json_body(response: object) -> dict[str, Any]:
    if response is None:
        return {}
    status = getattr(response, "status_code", None)
    if status not in (200, 201):
        return {}
    json_fn = getattr(response, "json", None)
    if callable(json_fn):
        try:
            data = json_fn()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    text = getattr(response, "text", "") or ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _text(value: object) -> str:
    return str(value or "").strip()


def parse_ha_config(payload: dict[str, Any], settings) -> HomeContext:
    time_zone = _text(payload.get("time_zone")) or FALLBACK_TIME_ZONE
    return HomeContext(
        time_zone=time_zone,
        location_name=_text(payload.get("location_name")),
        home_location=str(getattr(settings, "home_location", "") or "").strip(),
        country=_text(payload.get("country")),
        latitude=_float(payload.get("latitude")),
        longitude=_float(payload.get("longitude")),
        zone_name="",
    )


def with_zone_home(context: HomeContext, payload: dict[str, Any]) -> HomeContext:
    attributes = payload.get("attributes")
    if not isinstance(attributes, dict):
        attributes = payload
    zone_name = _text(attributes.get("friendly_name") or attributes.get("name"))
    latitude = _float(attributes.get("latitude"))
    longitude = _float(attributes.get("longitude"))
    return HomeContext(
        time_zone=context.time_zone,
        location_name=context.location_name,
        home_location=context.home_location,
        country=context.country,
        latitude=context.latitude if context.latitude is not None else latitude,
        longitude=context.longitude if context.longitude is not None else longitude,
        zone_name=zone_name,
    )


def resolve_time_zone(name: str) -> tuple[str, ZoneInfo | timezone]:
    wanted = (name or "").strip() or FALLBACK_TIME_ZONE
    for candidate in (wanted, FALLBACK_TIME_ZONE):
        try:
            return candidate, ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, Exception):
            continue
    return "UTC", timezone.utc


def format_local_now(time_zone: str, now: datetime | None = None) -> str:
    """Human local stamp plus ISO date. Never a baked-in calendar day."""
    zone_name, tz = resolve_time_zone(time_zone)
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    hour = current.strftime("%I").lstrip("0") or "12"
    tz_label = current.tzname() or zone_name
    clock = (
        f"{current.strftime('%A')}, {current.strftime('%B')} {current.day}, {current.year}, "
        f"{hour}:{current.strftime('%M')} {current.strftime('%p')} {tz_label}"
    )
    iso = current.strftime("%Y-%m-%d")
    return f"{clock} (ISO date {iso}, time zone {zone_name})"


# Two-letter USPS abbreviations so "Summit, NJ" is a named city, not a vague label.
_US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}
_GENERIC_PLACE_NAMES = frozenset({"home", "house", "residence", "zone.home"})


def expand_city_name(value: str) -> tuple[str, str]:
    """Turn ``Summit, NJ`` into a city the model can say out loud.

    Returns ``(display, city_name)`` where display is ``Summit, NJ / Summit, New Jersey``
    and city_name is ``Summit, New Jersey``. Unrecognized labels are returned as-is.
    """
    raw = " ".join((value or "").split())
    if not raw:
        return "", ""
    compact = raw.replace(".", "")
    city = ""
    region = ""
    if "," in compact:
        left, right = compact.rsplit(",", 1)
        city, region = left.strip(), right.strip().upper()
    else:
        parts = compact.split()
        if len(parts) >= 2:
            city, region = " ".join(parts[:-1]), parts[-1].upper()
    if city and region in _US_STATES:
        full = f"{city}, {_US_STATES[region]}"
        short = f"{city}, {region}"
        if short.casefold() == raw.casefold():
            return f"{short} / {full}", full
        return f"{raw} / {full}", full
    return raw, raw


def named_city(context: HomeContext) -> tuple[str, str]:
    """Prefer add-on ``home_location``; skip generic HA names like ``Home``."""
    for candidate in (context.home_location, context.zone_name, context.location_name):
        text = (candidate or "").strip()
        if not text or text.casefold() in _GENERIC_PLACE_NAMES:
            continue
        return expand_city_name(text)
    return "", ""


def format_home_location(context: HomeContext) -> str:
    """Prefer the add-on city label, then HA zone / config name, then coordinates."""
    parts: list[str] = []
    display, city_name = named_city(context)
    if display:
        parts.append(display)
    elif city_name:
        parts.append(city_name)
    place = context.zone_name or context.location_name
    if place and place.casefold() not in {p.casefold() for p in parts} | _GENERIC_PLACE_NAMES:
        if place.casefold() not in display.casefold() and place.casefold() not in city_name.casefold():
            parts.append(place)
    if context.country and context.country.casefold() not in " ".join(parts).casefold():
        parts.append(context.country)
    if context.latitude is not None and context.longitude is not None:
        parts.append(_format_coords(context.latitude, context.longitude))
    return "; ".join(parts)


def _format_coords(latitude: float, longitude: float) -> str:
    ns = "N" if latitude >= 0 else "S"
    ew = "E" if longitude >= 0 else "W"
    return f"{abs(latitude):.4f}°{ns}, {abs(longitude):.4f}°{ew}"


def with_home_context(base: str, context: HomeContext | None, now: datetime | None = None) -> str:
    ctx = context or HomeContext()
    when = format_local_now(ctx.time_zone, now)
    where = format_home_location(ctx)
    extra = (
        f"The current local date and time is {when}. "
        "Use this clock for today, tonight, this week, this weekend, this season, "
        "and this year. Do not guess a date from training data."
    )
    display, city_name = named_city(ctx)
    if where:
        extra += f" This home is in {where}."
        if city_name:
            extra += (
                f" The city name is {city_name}. Use that city name for local events, "
                "weather, sports, and 'near me' questions. Do not call this place only "
                "'home' or an unnamed location."
            )
        else:
            extra += " Use that place for local events, weather, and 'near me' questions."
    elif display or city_name:
        extra += (
            f" This home's city is {city_name or display}. Use that city name for local "
            "events, weather, and 'near me' questions."
        )
    root = (base or "").rstrip()
    return f"{root}\n\n{extra}" if root else extra


async def fetch_home_context(http, settings) -> HomeContext:
    """Read HA timezone / GPS. Failures fall back to America/New_York + home_location."""
    context = fallback_home_context(settings)
    token = str(getattr(settings, "mcp_token", "") or "").strip()
    api_url = str(getattr(settings, "ha_api_url", "") or "").rstrip("/")
    if not token or not api_url:
        return context
    headers = {"Authorization": f"Bearer {token}"}
    try:
        config_response = await http.get(f"{api_url}/api/config", headers=headers)
        parsed = parse_ha_config(_json_body(config_response), settings)
        context = parsed
    except Exception as exc:
        log.info("HA /api/config unavailable (%s); using fallback timezone", type(exc).__name__)
        return context
    try:
        zone_response = await http.get(f"{api_url}/api/states/zone.home", headers=headers)
        zone = _json_body(zone_response)
        if zone:
            context = with_zone_home(context, zone)
    except Exception as exc:
        log.info("HA zone.home unavailable (%s)", type(exc).__name__)
    return context
