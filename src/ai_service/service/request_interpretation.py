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
    PastSettingsInterpretation,
    RequestContext,
    RequestInterpretation,
)

from ai_service.prompts import load_prompt
from ai_service.service.context import (
    bounded_history,
    compact_json,
    device_catalog,
    preference_evidence,
    record_text,
    track_catalog,
)
from ai_service.service.history_evidence import ConfirmationHistory
from ai_service.service.output_validation import InvalidModelOutput, validation_reason
from shared.telemetry import log_event


_SYSTEM_PROMPT = load_prompt("interpret_request.md")
logger = logging.getLogger(__name__)

_PAST = re.compile(r"вчера|\bд(?:ень|ня|ней) назад\b|как (?:тогда|в тот раз)|как .*заезд|прошл\w* заезд")
_HOME = re.compile(r"как дома|по-домашнему|как у меня дома")
_FOLLOW_UP = re.compile(
    r"\b(?:его|е[её]|их|это|этот|эту|ещ[её]|снова|обратно|прежн\w*|"
    r"тот же|так же|как договаривал\w*|как раньше|как обычно|перенеси|сдвин\w*|передвин\w*)\b"
)
_ALARM_REFERENCE = re.compile(r"будиль|разбуд|позже|раньше|сдвин|передвин|перенеси")


def request_payload(item: RequestContext) -> dict[str, object]:
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
    return payload


def request_messages(payload: dict[str, object]) -> list[dict[str, str]]:
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
    follow_up = _FOLLOW_UP.search(query)
    if _ALARM_REFERENCE.search(query):
        query += " будильник разбуди будить"
    day = _past_day(item, query) if past else None

    def priority(entry: JsonObject) -> int:
        matched_day = day is not None and _mentions_day(entry, day, item.stay["hotel_timezone"])
        return (10 if matched_day else 0) + (preference_evidence(entry))

    conversation = bounded_history(
        [message.model_dump(mode="json", exclude_none=True) for message in item.conversation],
        budget=1000, max_items=len(item.conversation), query=query, recent=2,
        priority=priority if past or home else None,
    )
    expanded = past or home or follow_up

    user_id = item.profile.user["user_id"]
    history = [
        row for row in item.profile.chat_history if row["user_id"] == user_id
    ]
    events = [
        row for row in item.profile.events if row["user_id"] == user_id
        and preference_evidence(row) >= 2
    ]
    result: dict[str, object] = {
        "conversation": conversation,
        "chat_history": bounded_history(
            history, budget=649 if expanded else 419, max_items=3 if expanded else 2,
            query=query, priority=priority, pair_replies=True,
        ),
        "events": bounded_history(
            events, budget=849 if expanded else 299, max_items=3 if expanded else 1,
            query=query, priority=priority,
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
    match = re.search(r"\b(\d{1,4}|два|три)\s+(?:дня|дней|день) назад\b", query)
    if match:
        count = match[1]
        days = int(count) if count.isdigit() else {"два": 2, "три": 3}[count]
    elif re.search(r"\bпозавчера\b", query):
        days = 2
    elif re.search(r"\bвчера\b", query):
        days = 1
    else:
        return None
    at = _local_day(item.request["at"], item.stay["hotel_timezone"])
    return at - timedelta(days=days) if at and at.toordinal() > days else None


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


def validate_interpretation(
    text: str, item: RequestContext, payload: dict[str, object]
) -> RequestInterpretation:
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

    if "periods" in item.request and "timing" not in result.model_fields_set:
        raise InvalidModelOutput("interpretation_missing_timing")
    if result.record_id != item.request["record_id"]:
        raise InvalidModelOutput("interpretation_record_id")
    if result.intent == "sound_on" and result.slots.track_id is not None:
        if not any(track["track_id"] == result.slots.track_id for track in item.profile.tracks):
            raise InvalidModelOutput("interpretation_track_catalog")
    if isinstance(result, PastSettingsInterpretation) and result.slots.confirmed_settings:
        history = cast(list[JsonObject], payload.get("chat_history", []))
        evidence = ConfirmationHistory(item.profile.chat_history, history, str(item.profile.user["user_id"]))
        supported = [
            setting for setting in result.slots.confirmed_settings
            if evidence.supports(
                setting.message_id, setting.request_message_id, setting.quote, setting.device_id,
                setting.field_id, setting.value, item.profile.tracks,
            )
        ]
        dropped = len(result.slots.confirmed_settings) - len(supported)
        if dropped:
            log_event(logger, "history_confirmation_rejected", dropped=dropped, kept=len(supported))
            result = result.model_copy(update={"slots": result.slots.model_copy(
                update={"confirmed_settings": supported},
            )})
    if result.intent == "invalid_value":
        value = result.slots.requested_value
        if isinstance(value, float) and not isfinite(value):
            raise InvalidModelOutput("interpretation_nonfinite_value")
    return result
