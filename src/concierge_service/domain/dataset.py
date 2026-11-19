"""Case 2 domain records and invariants.

Domain models are plain dataclasses and do not depend on Pydantic or on the
external Hack API JSON shape.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

JSONScalar = str | int | float | bool | None

EXPECTED_INTENT_IDS = frozenset({
    "brighter", "dimmer", "warmer", "cooler", "all_off", "sound_on",
    "quieter", "curtains", "alarm_set", "alarm_shift", "alarm_cancel",
    "going_to_bed", "woke_up", "like_yesterday", "do_not_disturb",
    "like_home", "out_of_scope", "invalid_value", "medical",
})
EXPECTED_SLEEP_RULE_IDS = frozenset({
    "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8",
})


class DatasetError(ValueError):
    """Inconsistent dataset records."""


@dataclass(frozen=True)
class DatasetMeta:
    schema_version: str
    dataset_id: str
    case_id: int
    phase: str
    version: str
    generated_at: datetime
    history_through: date


@dataclass(frozen=True)
class SleepPreferences:
    temperature: Literal["cool", "normal", "warm"] | None
    light_sensitive: bool | None
    favorite_track_ids: tuple[str, ...] | None
    dislikes: tuple[str, ...] | None


@dataclass(frozen=True)
class User:
    user_id: str
    timezone: ZoneInfo | None
    chronotype: Literal["early", "intermediate", "late"] | None
    weekday_bedtime: time | None
    weekday_wake_time: time | None
    sleep_preferences: SleepPreferences | None


@dataclass(frozen=True)
class Event:
    event_id: str
    user_id: str
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    user_id: str
    role: Literal["user", "agent"]
    at: datetime
    text: str


@dataclass(frozen=True)
class Stay:
    record_id: str
    user_id: str
    check_in_at: datetime
    target_wake_at: datetime
    hotel_timezone: ZoneInfo


@dataclass(frozen=True)
class Utterance:
    at: datetime
    text: str


@dataclass(frozen=True)
class Request:
    record_id: str
    user_id: str
    stay_id: str
    at: datetime
    initial_state: Mapping[str, Mapping[str, JSONScalar]]
    utterances: tuple[Utterance, ...]


@dataclass(frozen=True)
class DeviceField:
    field_id: str
    description: str
    value_schema: Mapping[str, object]
    default_value: JSONScalar
    unit: str | None


@dataclass(frozen=True)
class Device:
    device_id: str
    title: str
    fields: tuple[DeviceField, ...]


@dataclass(frozen=True)
class Track:
    track_id: str
    title: str


@dataclass(frozen=True)
class IntentSpec:
    intent_id: str
    description: str


@dataclass(frozen=True)
class SleepRule:
    rule_id: str
    version: str
    description: str


@dataclass(frozen=True)
class Case2Dataset:
    meta: DatasetMeta
    users: tuple[User, ...]
    events: tuple[Event, ...]
    chat_history: tuple[ChatMessage, ...]
    stays: tuple[Stay, ...]
    requests: tuple[Request, ...]
    devices: tuple[Device, ...]
    tracks: tuple[Track, ...]
    intents: tuple[IntentSpec, ...]
    sleep_rules: tuple[SleepRule, ...]


@dataclass(frozen=True)
class DatasetIndex:
    users_by_id: Mapping[str, User]
    stays_by_id: Mapping[str, Stay]
    requests_by_id: Mapping[str, Request]
    devices_by_id: Mapping[str, Device]
    device_fields_by_device: Mapping[str, Mapping[str, DeviceField]]
    tracks_by_id: Mapping[str, Track]
    intents_by_id: Mapping[str, IntentSpec]
    sleep_rules_by_id: Mapping[str, SleepRule]
    events_by_user: Mapping[str, tuple[Event, ...]]
    chat_history_by_user: Mapping[str, tuple[ChatMessage, ...]]
    stays_by_user: Mapping[str, tuple[Stay, ...]]
    requests_by_user: Mapping[str, tuple[Request, ...]]


def build_dataset_index(dataset: Case2Dataset) -> DatasetIndex:
    users_by_id = _unique_by_id(dataset.users, lambda user: user.user_id, "users")
    stays_by_id = _unique_by_id(dataset.stays, lambda stay: stay.record_id, "stays")
    requests_by_id = _unique_by_id(
        dataset.requests, lambda request: request.record_id, "requests"
    )
    devices_by_id = _unique_by_id(
        dataset.devices, lambda device: device.device_id, "devices"
    )
    device_fields = {
        device.device_id: _unique_by_id(
            device.fields, lambda field: field.field_id, "device fields"
        )
        for device in dataset.devices
    }
    tracks_by_id = _unique_by_id(dataset.tracks, lambda track: track.track_id, "tracks")
    intents_by_id = _unique_by_id(dataset.intents, lambda intent: intent.intent_id, "intents")
    rules_by_id = _unique_by_id(dataset.sleep_rules, lambda rule: rule.rule_id, "sleep_rules")
    _unique_by_id(dataset.events, lambda event: (event.user_id, event.event_id), "events")
    _unique_by_id(
        dataset.chat_history, lambda message: (message.user_id, message.message_id), "chat_history"
    )

    if intents_by_id.keys() != EXPECTED_INTENT_IDS:
        raise DatasetError("intents: unexpected set of ids")
    if rules_by_id.keys() != EXPECTED_SLEEP_RULE_IDS:
        raise DatasetError("sleep_rules: unexpected set of ids")

    for stay in dataset.stays:
        if stay.target_wake_at < stay.check_in_at:
            raise DatasetError("stays: target_wake_at precedes check_in_at")

    for request in dataset.requests:
        stay = stays_by_id.get(request.stay_id)
        if stay is None:
            raise DatasetError("requests: unknown stay")
        if stay.user_id != request.user_id:
            raise DatasetError("requests: stay belongs to another user")
        for device_id, fields in request.initial_state.items():
            known_fields = device_fields.get(device_id)
            if known_fields is None:
                raise DatasetError("requests: unknown initial_state device")
            if fields.keys() - known_fields.keys():
                raise DatasetError("requests: unknown initial_state field")

    return DatasetIndex(
        users_by_id=users_by_id,
        stays_by_id=stays_by_id,
        requests_by_id=requests_by_id,
        devices_by_id=devices_by_id,
        device_fields_by_device=device_fields,
        tracks_by_id=tracks_by_id,
        intents_by_id=intents_by_id,
        sleep_rules_by_id=rules_by_id,
        events_by_user=_by_user(
            dataset.events, users_by_id, lambda event: event.started_at, "events"
        ),
        chat_history_by_user=_by_user(
            dataset.chat_history, users_by_id, lambda message: message.at, "chat_history"
        ),
        stays_by_user=_by_user(
            dataset.stays, users_by_id, lambda stay: stay.check_in_at, "stays"
        ),
        requests_by_user=_by_user(
            dataset.requests, users_by_id, lambda request: request.at, "requests"
        ),
    )


T = TypeVar("T")
RecordKey = TypeVar("RecordKey", bound=Hashable)
UserRecord = TypeVar("UserRecord", Event, ChatMessage, Stay, Request)


def _unique_by_id(
    records: Iterable[T], key: Callable[[T], RecordKey], label: str
) -> dict[RecordKey, T]:
    result: dict[RecordKey, T] = {}
    for record in records:
        record_id = key(record)
        if record_id in result:
            raise DatasetError(f"{label}: duplicate id")
        result[record_id] = record
    return result


def _by_user(
    records: Iterable[UserRecord],
    users_by_id: Mapping[str, User],
    at: Callable[[UserRecord], datetime],
    label: str,
) -> dict[str, tuple[UserRecord, ...]]:
    grouped: defaultdict[str, list[UserRecord]] = defaultdict(list)
    for record in records:
        if record.user_id not in users_by_id:
            raise DatasetError(f"{label}: unknown user")
        grouped[record.user_id].append(record)
    return {
        user_id: tuple(sorted(grouped.get(user_id, ()), key=at))
        for user_id in users_by_id
    }
