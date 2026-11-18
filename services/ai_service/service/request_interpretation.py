import json
import logging
import re
from datetime import date, datetime, timedelta
from math import isfinite
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from concierge_service.ports.ai import (
    REQUEST_INTERPRETATION_ADAPTER,
    AiAgentError,
    JsonObject,
    RequestContext,
    RequestInterpretation,
)

from ai_service.errors import InvalidModelOutput, validation_reason
from ai_service.prompts import load_prompt
from ai_service.service.context import (
    bounded_history,
    compact_json,
    device_catalog,
    preference_evidence,
    record_text,
    track_catalog,
)
from run_telemetry import log_event


_SYSTEM_PROMPT = load_prompt("interpret_request.md")
logger = logging.getLogger(__name__)

_PAST = re.compile(r"вчера|\bд(?:ень|ня|ней) назад\b|как (?:тогда|в тот раз)|как .*заезд|прошл\w* заезд")
_HOME = re.compile(r"как дома|по-домашнему|как у меня дома")
_FOLLOW_UP = re.compile(
    r"\b(?:его|е[её]|их|это|этот|эту|ещ[её]|снова|обратно|прежн\w*|"
    r"тот же|так же|как договаривал\w*|как раньше|как обычно|перенеси|сдвин\w*|передвин\w*)\b"
)
_ALARM_REFERENCE = re.compile(r"будиль|разбуд|позже|раньше|сдвин|передвин|перенеси")


def request_messages(item: RequestContext) -> list[dict[str, str]]:
    utterances = cast(list[JsonObject], item.request["utterances"])
    query = " ".join(record_text(record) for record in utterances).casefold()
    relevant = _reference_context(item, query)
    payload = {
        "request": item.request,
        "hotel_timezone": item.stay["hotel_timezone"],
        "devices": device_catalog(item.profile.devices, include_limits=True),
        "tracks": track_catalog(item.profile.tracks),
        **relevant,
    }
    if _ALARM_REFERENCE.search(query) and item.stay.get("target_wake_at") is not None:
        payload["target_wake_at"] = item.stay["target_wake_at"]
    if logger.isEnabledFor(logging.DEBUG):
        selected = {key: cast(list[JsonObject], value) for key, value in relevant.items()}
        log_event(
            logger, "llm_request_context", level=logging.DEBUG,
            utterances=len(utterances), conversation_turns=len(selected.get("conversation", ())),
            event_ids=[row["event_id"] for row in selected.get("events", ())],
            message_ids=[row["message_id"] for row in selected.get("chat_history", ())],
            observed_settings=len(selected.get("observed_settings", ())),
            includes_target_wake="target_wake_at" in payload,
        )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": compact_json(payload),
        },
    ]


def _reference_context(item: RequestContext, query: str) -> dict[str, object]:
    past = bool(_PAST.search(query))
    home = bool(_HOME.search(query))
    if not (past or home or _FOLLOW_UP.search(query)):
        recent = bounded_history(
            [message.model_dump(mode="json") for message in item.conversation[-2:]],
            budget=1000, max_items=2,
        )
        return {"conversation": recent} if recent else {}

    user_id = item.profile.user["user_id"]
    if _ALARM_REFERENCE.search(query):
        query += " будильник разбуди будить"
    day = _past_day(item, query) if past else None

    def priority(entry: JsonObject) -> int:
        matched_day = day is not None and _mentions_day(entry, day, item.stay["hotel_timezone"])
        return (10 if matched_day else 0) + (preference_evidence(entry) if past or home else 0)

    conversation = bounded_history(
        [message.model_dump(mode="json") for message in item.conversation],
        budget=1000, max_items=4, query=query, recent=2,
        priority=priority if past or home else None,
    )
    history = [
        row for row in item.profile.chat_history if row["user_id"] == user_id
    ]
    events = [
        row for row in item.profile.events if row["user_id"] == user_id
        and preference_evidence(row) >= 2
    ] if past or home else []
    result: dict[str, object] = {
        "conversation": conversation,
        "chat_history": bounded_history(
            history, budget=650, max_items=3, query=query, priority=priority,
        ),
        "events": bounded_history(
            events, budget=850, max_items=3, query=query, priority=priority,
        ),
    }
    hints = item.preference_hints
    if hints is not None and hints.user_id == user_id and (past or home):
        scope = "past_stay" if past else "home"
        settings = [
            setting.model_dump(mode="json", exclude={"sources"})
            for setting in hints.settings if setting.context == scope
        ]
        result["observed_settings"] = bounded_history(
            settings, budget=650, max_items=5, priority=priority,
        )
    return {key: value for key, value in result.items() if value}


def _past_day(item: RequestContext, query: str) -> date | None:
    match = re.search(r"\b(\d{1,4})\s+(?:дня|дней|день) назад\b", query)
    if not match and "вчера" not in query:
        return None
    at = _local_day(item.request["at"], item.stay["hotel_timezone"])
    return at - timedelta(days=int(match[1]) if match else 1) if at else None


def _local_day(value: object, zone: object) -> date | None:
    if not isinstance(value, str) or not isinstance(zone, str):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(ZoneInfo(zone)).date()
    except (ValueError, ZoneInfoNotFoundError):
        return None


def _mentions_day(entry: JsonObject, day: date, zone: object) -> bool:
    text = record_text(entry)
    if re.search(rf"(?<!\d)0?{day.day}\.0?{day.month}(?![\d.])", text):
        return True
    return any(_local_day(entry.get(key), zone) == day for key in ("observed_at", "started_at", "at"))


def validate_interpretation(text: str, item: RequestContext) -> RequestInterpretation:
    try:
        result = REQUEST_INTERPRETATION_ADAPTER.validate_json(text)
    except ValidationError as invalid:
        try:
            error = json.loads(text)
        except (ValueError, RecursionError):
            error = None
        if error == {
            "record_id": item.request["record_id"], "error": "unresolved_reference"
        }:
            raise AiAgentError("unresolved_reference") from None
        raise InvalidModelOutput(validation_reason(invalid, "interpretation_schema")) from None

    if result.record_id != item.request["record_id"]:
        raise InvalidModelOutput("interpretation_record_id")
    if result.intent == "sound_on" and result.slots.track_id is not None:
        if not any(track["track_id"] == result.slots.track_id for track in item.profile.tracks):
            raise InvalidModelOutput("interpretation_track_catalog")
    if result.intent == "invalid_value":
        value = result.slots.requested_value
        if isinstance(value, float) and not isfinite(value):
            raise InvalidModelOutput("interpretation_nonfinite_value")
    return result
