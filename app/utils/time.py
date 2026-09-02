"""UTC storage with display conversion into the configured workspace timezone."""

from __future__ import annotations

import time as time_mod
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "UTC"

TIMEZONE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("UTC", ("UTC",)),
    (
        "Asia",
        (
            "Asia/Yangon",
            "Asia/Bangkok",
            "Asia/Jakarta",
            "Asia/Singapore",
            "Asia/Kuala_Lumpur",
            "Asia/Ho_Chi_Minh",
            "Asia/Dhaka",
            "Asia/Kolkata",
            "Asia/Kathmandu",
            "Asia/Karachi",
            "Asia/Dubai",
            "Asia/Tehran",
            "Asia/Jerusalem",
            "Asia/Riyadh",
            "Asia/Shanghai",
            "Asia/Hong_Kong",
            "Asia/Taipei",
            "Asia/Seoul",
            "Asia/Tokyo",
            "Asia/Manila",
        ),
    ),
    (
        "Europe",
        (
            "Europe/London",
            "Europe/Dublin",
            "Europe/Lisbon",
            "Europe/Paris",
            "Europe/Berlin",
            "Europe/Amsterdam",
            "Europe/Rome",
            "Europe/Madrid",
            "Europe/Warsaw",
            "Europe/Athens",
            "Europe/Istanbul",
            "Europe/Moscow",
        ),
    ),
    (
        "Americas",
        (
            "America/St_Johns",
            "America/Halifax",
            "America/New_York",
            "America/Toronto",
            "America/Chicago",
            "America/Mexico_City",
            "America/Denver",
            "America/Phoenix",
            "America/Los_Angeles",
            "America/Vancouver",
            "America/Anchorage",
            "America/Sao_Paulo",
            "America/Argentina/Buenos_Aires",
            "America/Santiago",
            "America/Bogota",
            "America/Lima",
        ),
    ),
    (
        "Africa",
        (
            "Africa/Cairo",
            "Africa/Johannesburg",
            "Africa/Lagos",
            "Africa/Nairobi",
            "Africa/Casablanca",
        ),
    ),
    (
        "Australia & Pacific",
        (
            "Australia/Perth",
            "Australia/Adelaide",
            "Australia/Sydney",
            "Australia/Brisbane",
            "Pacific/Auckland",
            "Pacific/Fiji",
            "Pacific/Honolulu",
        ),
    ),
)

_tz_cache: tuple[float, str] = (0.0, DEFAULT_TIMEZONE)
_active_timezone = DEFAULT_TIMEZONE


def utc_now() -> datetime:
    """Naive UTC, matching SQLite DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def set_active_timezone(name: str | None) -> str:
    """Remember the workspace timezone without reading settings (avoids lock cycles)."""
    global _active_timezone
    try:
        key = normalize_timezone(name)
    except ValueError:
        key = DEFAULT_TIMEZONE
    _active_timezone = key
    _set_tz_cache(key)
    return key


def zone_info(name: str | None = None) -> ZoneInfo:
    key = normalize_timezone(name or _active_timezone)
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def normalize_timezone(name: str | None) -> str:
    raw = (name or "").strip() or DEFAULT_TIMEZONE
    if raw in {"Etc/UTC", "GMT", "Z", "utc"}:
        raw = DEFAULT_TIMEZONE
    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {raw}") from exc
    return raw


def configured_timezone() -> str:
    return _active_timezone or DEFAULT_TIMEZONE


def _set_tz_cache(name: str) -> None:
    global _tz_cache
    _tz_cache = (time_mod.monotonic(), name)


def clear_timezone_cache() -> None:
    global _tz_cache
    _tz_cache = (0.0, DEFAULT_TIMEZONE)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_local(value: datetime | None, tz_name: str | None = None) -> datetime | None:
    aware = as_utc(value)
    if aware is None:
        return None
    return aware.astimezone(zone_info(tz_name))


def now_local(tz_name: str | None = None) -> datetime:
    return datetime.now(timezone.utc).astimezone(zone_info(tz_name))


def format_local(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M", *, tz_name: str | None = None) -> str:
    local = to_local(value, tz_name)
    if local is None:
        return ""
    return local.strftime(fmt)


def timezone_abbrev(tz_name: str | None = None) -> str:
    return now_local(tz_name).tzname() or (tz_name or DEFAULT_TIMEZONE)


def timezone_offset_label(tz_name: str | None = None) -> str:
    offset = now_local(tz_name).utcoffset() or timedelta(0)
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def timezone_option_label(tz_name: str) -> str:
    city = tz_name.split("/")[-1].replace("_", " ")
    try:
        return f"{city} ({timezone_offset_label(tz_name)})"
    except Exception:
        return city


def timezone_choices(current: str | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
    seen: set[str] = set()
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for title, names in TIMEZONE_GROUPS:
        options = []
        for name in names:
            try:
                key = normalize_timezone(name)
            except ValueError:
                continue
            seen.add(key)
            options.append((key, timezone_option_label(key)))
        if options:
            groups.append((title, options))
    current_key = DEFAULT_TIMEZONE
    try:
        current_key = normalize_timezone(current)
    except ValueError:
        current_key = DEFAULT_TIMEZONE
    if current_key not in seen:
        groups.append(("Saved", [(current_key, timezone_option_label(current_key))]))
    return groups
