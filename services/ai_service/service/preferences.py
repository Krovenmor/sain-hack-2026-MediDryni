import json
import logging
import re
from math import isfinite
from collections import Counter
from collections.abc import Sequence
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import JsonValue, ValidationError

from concierge_service.ports.ai import (
    JsonObject,
    PreferenceHints,
    ProfileContext,
    SourceRef,
)

from ai_service.errors import InvalidModelOutput, validation_reason
from ai_service.prompts import load_prompt
from ai_service.service.context import (
    bounded_history,
    compact_json,
    device_catalog,
    preference_evidence,
    track_catalog,
)
from run_telemetry import log_event


_INSTRUCTIONS = load_prompt("preferences.md")
_SINGLE_FORMAT = load_prompt("preferences_single.md")
_BATCH_FORMAT = load_prompt("preferences_batch.md")
logger = logging.getLogger(__name__)

_PROFILE_FIELDS = (
    "user_id", "timezone", "chronotype", "weekday_bedtime", "weekday_wake_time",
    "sleep_preferences",
)


def _select_messages(messages: Sequence[JsonObject], user_id: str) -> list[JsonObject]:
    history = [message for message in messages if message["user_id"] == user_id]
    budget, max_items = 2100, 12
    if len(history) <= max_items and len(compact_json(history)) <= budget:
        return history

    relevant_turns = {
        neighbor
        for index, message in enumerate(history)
        if preference_evidence(message) >= 2
        for neighbor in range(max(0, index - 1), min(len(history), index + 2))
    }
    return bounded_history(
        [message for index, message in enumerate(history)
         if index in relevant_turns or preference_evidence(message) > 0],
        budget=budget, max_items=max_items, priority=preference_evidence,
    )


class PreferenceInput:
    def __init__(
        self, item: ProfileContext, user_id: str, *, profile_number: int, include_history: bool = True
    ) -> None:
        self._item = item
        self._user_id = user_id
        self._profile_number = profile_number
        self._profile = {key: item.user[key] for key in _PROFILE_FIELDS if key in item.user}
        self._events = bounded_history(
            [event for event in item.events if event["user_id"] == user_id
             and preference_evidence(event) > 0],
            budget=2600, max_items=12, priority=preference_evidence,
        ) if include_history else []
        self._messages = _select_messages(item.chat_history, user_id) if include_history else []

    @property
    def user_id(self) -> str:
        return self._user_id

    def payload(self) -> dict[str, object]:
        return {
            "user": self._profile,
            "events": self._events,
            "chat_history": self._messages,
            "devices": device_catalog(self._item.devices, include_limits=False),
            "tracks": track_catalog(self._item.tracks),
        }

    def log_selected_sources(self) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            log_event(
                logger, "llm_preference_context", level=logging.DEBUG,
                profile_number=self._profile_number, profile_fields=sorted(self._profile),
                event_ids=[row["event_id"] for row in self._events],
                message_ids=[row["message_id"] for row in self._messages],
                events_available=len(self._item.events),
                messages_available=len(self._item.chat_history),
            )

    def parse(self, text: str) -> PreferenceHints:
        try:
            hints = PreferenceHints.model_validate_json(text)
        except ValidationError as error:
            raise InvalidModelOutput(validation_reason(error, "preference_schema")) from None
        self.validate(hints)
        return hints

    def validate(self, hints: PreferenceHints) -> None:
        if hints.user_id != self._user_id:
            raise InvalidModelOutput("preference_user_id")

        records = {
            ("profile", self._user_id): self._profile,
            **{("event", event["event_id"]): event for event in self._events},
            **{("message", message["message_id"]): message for message in self._messages},
        }
        for hint in (
            hints.temperature, hints.light_sensitive, hints.favorite_track_ids,
            hints.gentle_alarm, hints.weekday_bedtime, hints.weekday_wake_time,
            hints.home_timezone, *hints.settings,
        ):
            if hint is not None:
                for source in hint.sources:
                    _validate_source(records.get((source.kind, source.source_id)), source)

        if hints.favorite_track_ids is not None:
            ids = hints.favorite_track_ids.value
            catalog_ids = {track["track_id"] for track in self._item.tracks}
            if len(set(ids)) != len(ids) or not set(ids) <= catalog_ids:
                raise InvalidModelOutput("preference_track_catalog")

        if hints.home_timezone is not None:
            try:
                ZoneInfo(hints.home_timezone.value)
            except (ValueError, ZoneInfoNotFoundError):
                raise InvalidModelOutput("preference_timezone") from None

        fields = {
            (device["device_id"], field["field_id"])
            for device in self._item.devices
            for field in cast(list[JsonObject], device["fields"])
        }
        for setting in hints.settings:
            if (setting.device_id, setting.field_id) not in fields:
                raise InvalidModelOutput("preference_device_field")
            if isinstance(setting.value, float) and not isfinite(setting.value):
                raise InvalidModelOutput("preference_nonfinite_value")


def _validate_source(record: JsonObject | None, source: SourceRef) -> None:
    if record is None:
        raise InvalidModelOutput("preference_source_id")
    pointer = source.pointer
    if pointer == "":
        return
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise InvalidModelOutput("preference_source_pointer")

    value: JsonValue = record
    for segment in pointer[1:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif (
            isinstance(value, list)
            and len(key) <= len(str(len(value)))
            and re.fullmatch(r"0|[1-9][0-9]*", key)
            and int(key) < len(value)
        ):
            value = value[int(key)]
        else:
            raise InvalidModelOutput("preference_source_pointer")


def preference_messages(items: Sequence[PreferenceInput]) -> list[dict[str, str]]:
    for item in items:
        item.log_selected_sources()
    if len(items) == 1:
        prompt = f"{_INSTRUCTIONS}\n{_SINGLE_FORMAT}"
        payload = items[0].payload()
    else:
        prompt = f"{_INSTRUCTIONS}\n{_BATCH_FORMAT}"
        payload = [item.payload() for item in items]
    return [{"role": "system", "content": prompt}, {"role": "user", "content": compact_json(payload)}]


def parse_preference_batch(
    text: str, items: Sequence[PreferenceInput]
) -> dict[str, PreferenceHints]:
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError):
        raise InvalidModelOutput("model_json") from None
    if not isinstance(payload, list):
        raise InvalidModelOutput("preference_batch_schema")

    inputs = {item.user_id: item for item in items}
    ids = Counter(
        row["user_id"] for row in payload
        if isinstance(row, dict) and isinstance(row.get("user_id"), str)
    )
    results = {}
    rejected: Counter[str] = Counter()
    for row in payload:
        if not isinstance(row, dict) or not isinstance(row.get("user_id"), str):
            rejected["preference_schema"] += 1
            continue
        user_id = row["user_id"]
        if user_id not in inputs or ids[user_id] != 1:
            rejected["preference_user_id"] += 1
            continue
        try:
            results[user_id] = inputs[user_id].parse(compact_json(row))
        except InvalidModelOutput as error:
            rejected[error.reason] += 1
    if len(results) != len(items) or rejected:
        log_event(
            logger, "llm_preference_batch_incomplete", accepted=len(results), expected=len(items),
            rejected=dict(rejected),
        )
    if not results:
        raise InvalidModelOutput("preference_batch_no_valid_profiles")
    return results
