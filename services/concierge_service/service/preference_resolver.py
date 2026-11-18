"""Resolve structured guest preferences and defaults for a stay."""

import re
from collections.abc import Collection
from datetime import time

from concierge_service.domain.dataset import User
from concierge_service.domain.sleep import ResolvedPreferences

_BEDTIMES = {"early": time(22), "intermediate": time(23), "late": time(0)}
_TEMPERATURES = {"cool": 17.5, "normal": 19.5, "warm": 21.5}
_GENTLE_ALARM = re.compile(
    r"(?:резк\w*|громк\w*)\s+(?:зву\w*|будильни\w*)"
    r"|(?:зву\w*|будильни\w*)\s+(?:резк\w*|громк\w*)",
    re.IGNORECASE,
)


def resolve_preferences(user: User, track_ids: Collection[str]) -> ResolvedPreferences:
    preferences = user.sleep_preferences
    temperature = preferences.temperature if preferences else None
    tracks = preferences.favorite_track_ids if preferences else None
    dislikes = preferences.dislikes if preferences else None
    return ResolvedPreferences(
        home_timezone=user.timezone,
        bedtime=user.weekday_bedtime or _BEDTIMES.get(user.chronotype, time(23)),
        night_temperature=_TEMPERATURES.get(temperature, 19.5),
        light_sensitive=bool(preferences and preferences.light_sensitive),
        favorite_track_ids=tuple(track_id for track_id in tracks or () if track_id in track_ids),
        gentle_alarm=any(_GENTLE_ALARM.search(dislike) for dislike in dislikes or ()),
    )
