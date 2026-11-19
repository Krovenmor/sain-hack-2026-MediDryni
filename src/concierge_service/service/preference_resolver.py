"""Resolve profile values, AI hints and defaults for a stay."""

import re
from collections.abc import Collection
from datetime import datetime, time
from typing import TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from concierge_service.domain.dataset import User
from concierge_service.domain.sleep import ResolvedPreferences
from concierge_service.ports.ai import Hint, PreferenceHints

_BEDTIMES = {"early": time(22), "intermediate": time(23), "late": time(0)}
_TEMPERATURES = {"cool": 17.5, "normal": 19.5, "warm": 21.5}
_GENTLE_ALARM = re.compile(
    r"(?:резк\w*|громк\w*)\s+(?:зву\w*|будильни\w*)"
    r"|(?:зву\w*|будильни\w*)\s+(?:резк\w*|громк\w*)",
    re.IGNORECASE,
)


def resolve_preferences(
    user: User, track_ids: Collection[str], hints: PreferenceHints | None = None
) -> ResolvedPreferences:
    preferences = user.sleep_preferences
    temperature = preferences.temperature if preferences else None
    tracks = preferences.favorite_track_ids if preferences else None
    dislikes = preferences.dislikes if preferences else None
    light_sensitive = preferences.light_sensitive if preferences else None
    bedtime = user.weekday_bedtime
    home_timezone = user.timezone
    gentle_alarm = any(_GENTLE_ALARM.search(dislike) for dislike in dislikes or ())

    if hints is not None:
        if temperature is None:
            temperature = _bedtime_temperature_category(hints) or _value(hints.temperature)
        if tracks is None:
            tracks = _value(hints.favorite_track_ids)
        if light_sensitive is None:
            light_sensitive = _value(hints.light_sensitive)
        hinted_bedtime = _value(hints.weekday_bedtime)
        if bedtime is None and hinted_bedtime is not None:
            bedtime = time.fromisoformat(hinted_bedtime)
        home_timezone = home_timezone or _timezone(_value(hints.home_timezone))
        gentle_alarm = gentle_alarm or _value(hints.gentle_alarm) is True

    return ResolvedPreferences(
        home_timezone=home_timezone,
        bedtime=bedtime or _BEDTIMES.get(user.chronotype, time(23)),
        night_temperature=_TEMPERATURES.get(temperature, 19.5),
        light_sensitive=light_sensitive is True,
        favorite_track_ids=tuple(track_id for track_id in tracks or () if track_id in track_ids),
        gentle_alarm=gentle_alarm,
    )


T = TypeVar("T")


def _bedtime_temperature_category(hints: PreferenceHints) -> str | None:
    readings = [
        setting for setting in hints.settings
        if setting.context == "bedtime" and setting.device_id == "climate"
        and setting.field_id == "temperature_c" and type(setting.value) in (int, float)
    ]
    if not readings:
        return None

    if all(reading.observed_at is not None for reading in readings):
        dated = [(datetime.fromisoformat(r.observed_at), r) for r in readings]
        latest = max(at for at, _ in dated)
        readings = [reading for at, reading in dated if at == latest]
    for category, midpoint in _TEMPERATURES.items():
        if all(midpoint - 0.5 <= reading.value <= midpoint + 0.5 for reading in readings):
            return category
    return None


def _value(hint: Hint[T] | None) -> T | None:
    return hint.value if hint is not None else None


def _timezone(name: str | None) -> ZoneInfo | None:
    if name is None:
        return None
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        return None
