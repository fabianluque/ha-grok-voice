"""Date and time for Grok: the instruction line and day labels on tool outputs.

Measured (text and spoken runs against grok-voice-latest): a clock line plus explicit
rules gets "tonight / tomorrow" right when tool data carries local dates, but Home
Assistant often returns UTC timestamps (a game at 8:30 PM EDT is "...T00:30Z" the next
day). Labeling dates in tool outputs with the local day ("today", "tomorrow") fixed
every such case, so both are on by default.
"""

from __future__ import annotations

import re
import string
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_DATE_TEMPLATE = (
    "Now: {weekday}, {month} {day}, {year}, {time} {tz}. Tomorrow is {tomorrow_weekday}, {tomorrow_month} {tomorrow_day}. "
    "Today = {weekday} {date}. Tonight means today's date. Always resolve relative dates against this before "
    "answering or calling tools; pass explicit ISO dates to tools; when comparing tool results, match the ISO date exactly."
)
FIELDS = (
    "weekday", "month", "day", "year", "time", "tz", "date",
    "tomorrow_weekday", "tomorrow_month", "tomorrow_day", "tomorrow_date", "yesterday_date",
)
_DATETIME_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})")
_DATE_RE = re.compile(r"(?<![\d:T-])(\d{4}-\d{2}-\d{2})(?![\d:T])")


def clock_time(moment: datetime) -> str:
    """12:01 AM without strftime's %-I (not in musl)."""
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def template_errors(template: str) -> list[str]:
    try:
        names = {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
    except ValueError as exc:
        return [f"date template: {exc}"]
    bad = sorted(n for n in names if n not in FIELDS)
    return [f"date template: unknown field {{{n}}} (use {', '.join('{' + f + '}' for f in FIELDS)})" for n in bad[:1]]


def date_line(time_zone: str, template: str = "", now: datetime | None = None) -> str:
    zone = ZoneInfo(time_zone or "UTC")
    now = (now or datetime.now(zone)).astimezone(zone)
    tomorrow, yesterday = now + timedelta(days=1), now - timedelta(days=1)
    values = {
        "weekday": now.strftime("%A"), "month": now.strftime("%b"), "day": now.day, "year": now.year,
        "time": clock_time(now), "tz": now.strftime("%Z"), "date": now.date().isoformat(),
        "tomorrow_weekday": tomorrow.strftime("%A"), "tomorrow_month": tomorrow.strftime("%b"),
        "tomorrow_day": tomorrow.day, "tomorrow_date": tomorrow.date().isoformat(), "yesterday_date": yesterday.date().isoformat(),
    }
    template = template or DEFAULT_DATE_TEMPLATE
    if template_errors(template):
        template = DEFAULT_DATE_TEMPLATE
    return template.format(**values).strip()


def _label(day: date, today: date) -> str:
    delta = (day - today).days
    return {0: "today", 1: "tomorrow", -1: "yesterday"}.get(delta) or (f"in {delta} days" if delta > 0 else f"{-delta} days ago")


def annotate_dates(text: str, time_zone: str, now: datetime | None = None) -> str:
    """'2026-10-16T00:30Z' -> '2026-10-16T00:30Z (local Thursday 2026-10-15 8:30 PM, today)';
    '2026-10-16' -> '2026-10-16 (Friday, tomorrow)'."""
    if not text or not re.search(r"\d{4}-\d{2}-\d{2}", text):
        return text
    zone = ZoneInfo(time_zone or "UTC")
    today = (now or datetime.now(zone)).astimezone(zone).date()

    def stamp(match: re.Match) -> str:
        raw = match.group(0)
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1)).astimezone(zone)
        except ValueError:
            return raw
        return f"{raw} (local {when.strftime('%A')} {when.date().isoformat()} {clock_time(when)}, {_label(when.date(), today)})"

    def plain(match: re.Match) -> str:
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError:
            return match.group(0)
        return f"{match.group(1)} ({day.strftime('%A')}, {_label(day, today)})"

    parts = []
    last = 0
    for match in _DATETIME_RE.finditer(text):
        parts.append(_DATE_RE.sub(plain, text[last : match.start()]))
        parts.append(stamp(match))
        last = match.end()
    parts.append(_DATE_RE.sub(plain, text[last:]))
    return "".join(parts)
