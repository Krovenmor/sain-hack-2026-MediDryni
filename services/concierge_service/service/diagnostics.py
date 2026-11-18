"""Allowlisted data for replaying planner decisions from debug logs."""

import re
from collections.abc import Mapping
from math import isfinite

from concierge_service.domain.dataset import JSONScalar
from concierge_service.domain.device_catalog import DeviceValueError
from concierge_service.ports.ai import (
    AlarmSetInterpretation,
    AlarmShiftBy,
    AlarmShiftInterpretation,
    CurtainsInterpretation,
    DoNotDisturbInterpretation,
    InvalidValueInterpretation,
    NumericInterpretation,
    PastSettingsInterpretation,
    PreferenceHints,
    RequestInterpretation,
    SoundInterpretation,
)

_NUMERIC_FIELDS = {
    "brightness", "color_temperature_k", "temperature_c", "volume", "timer_minutes", "position",
    "window_minutes",
}


def setting_values(values: Mapping[str, JSONScalar]) -> dict[str, JSONScalar]:
    allowed: dict[str, JSONScalar] = {}
    for field, value in values.items():
        if field in _NUMERIC_FIELDS and (
            type(value) is int or isinstance(value, float) and isfinite(value)
        ):
            allowed[field] = value
        elif field == "enabled" and isinstance(value, bool):
            allowed[field] = value
        elif field == "mode" and value in ("light", "sound", "both"):
            allowed[field] = value
        elif field == "track_id" and (
            value is None or isinstance(value, str) and re.fullmatch(r"track_[0-9]{1,4}", value)
        ):
            allowed[field] = value
        elif field == "at" and value is None:
            allowed[field] = None
    return allowed


def catalog_failure(error: DeviceValueError) -> dict[str, object]:
    values = setting_values({error.field_id: error.value})
    limit = error.limit
    if type(limit) is not int and not (isinstance(limit, float) and isfinite(limit)):
        limit = None
    return {
        "device": error.device_id, "field": error.field_id, "rule": error.rule,
        "alternative_rules": error.alternatives, "value_type": type(error.value).__name__,
        "values": values, "limit": limit,
    }


def interpretation_slots(interpretation: RequestInterpretation) -> dict[str, object]:
    if isinstance(interpretation, InvalidValueInterpretation):
        return {"reason": interpretation.slots.reason}
    if isinstance(interpretation, SoundInterpretation):
        return setting_values(interpretation.slots.model_dump())
    if isinstance(interpretation, (AlarmSetInterpretation, AlarmShiftInterpretation)):
        slots = interpretation.slots
        if isinstance(slots, AlarmShiftBy):
            return slots.model_dump()
        return slots.model_dump(exclude={"target_at"}) | {"target_at_present": True}
    if isinstance(interpretation, (
        NumericInterpretation, CurtainsInterpretation, DoNotDisturbInterpretation,
        PastSettingsInterpretation,
    )):
        return interpretation.slots.model_dump()
    return {}


def preference_details(hints: PreferenceHints) -> dict[str, object]:
    tracks = hints.favorite_track_ids
    favorite_track_ids = (
        [track for track in tracks.value if "track_id" in setting_values({"track_id": track})]
        if tracks is not None else None
    )
    return {
        "temperature": hints.temperature.value if hints.temperature else None,
        "light_sensitive": hints.light_sensitive.value if hints.light_sensitive else None,
        "gentle_alarm": hints.gentle_alarm.value if hints.gentle_alarm else None,
        "weekday_bedtime": hints.weekday_bedtime.value if hints.weekday_bedtime else None,
        "weekday_wake_time": hints.weekday_wake_time.value if hints.weekday_wake_time else None,
        "has_home_timezone": hints.home_timezone is not None,
        "favorite_track_ids": favorite_track_ids,
        "settings": [
            {
                "scope": setting.context, "device": setting.device_id, "field": setting.field_id,
                "observed_at": setting.observed_at, "value_type": type(setting.value).__name__,
                "values": setting_values({setting.field_id: setting.value}),
                "sources": [
                    {"kind": source.kind, "source_id": source.source_id}
                    if source.kind != "profile" else {"kind": source.kind}
                    for source in setting.sources
                ],
            }
            for setting in hints.settings
        ],
    }
