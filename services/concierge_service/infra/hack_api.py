"""HTTP adapter for the Hack API."""

from __future__ import annotations

from datetime import date, time

import httpx
from pydantic import ValidationError

from concierge_service.domain import dataset as domain
from concierge_service.infra.dataset_schemas import (
    DatasetPayload,
    GetDataPayload,
    SleepPreferencesPayload,
    UserPayload,
)
from concierge_service.infra.http_retry import request_with_retry
from concierge_service.ports.dataset import DatasetSource, ParsedDataset
from concierge_service.ports.hack_api import HackApiError


class HackApiAdapter:
    def __init__(self, *, base_url: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def get_case2_data(
        self, *, dataset_code: str | None, token: str
    ) -> ParsedDataset:
        params = None if dataset_code is None else {"dataset_code": dataset_code}
        try:
            response = await request_with_retry(
                self._client, "GET",
                f"{self._base_url}/case/2/getData",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError:
            raise HackApiError(503, "unavailable") from None

        if response.status_code != 200:
            status = response.status_code
            known_code = {
                401: "unauthorized",
                403: "forbidden",
                404: "dataset_not_found",
            }.get(status)
            if known_code is not None:
                raise HackApiError(status, known_code)
            raise HackApiError(503 if status >= 500 else 502, "upstream_error")

        try:
            envelope = GetDataPayload.model_validate(response.json())
        except ValueError:
            raise HackApiError(502, "invalid_response") from None

        raw_dataset = envelope.dataset
        try:
            payload = DatasetPayload.model_validate(raw_dataset)
        except ValidationError:
            raise HackApiError(502, "invalid_dataset") from None

        return ParsedDataset(
            dataset=_to_domain(payload),
            source=DatasetSource(
                users_by_id={row["user_id"]: row for row in raw_dataset["users"]},
                events_by_user_and_id={
                    (row["user_id"], row["event_id"]): row for row in raw_dataset["events"]
                },
                messages_by_user_and_id={
                    (row["user_id"], row["message_id"]): row for row in raw_dataset["chat_history"]
                },
                stays_by_id={row["record_id"]: row for row in raw_dataset["stays"]},
                requests_by_id={row["record_id"]: row for row in raw_dataset["requests"]},
                devices=tuple(raw_dataset["devices"]),
                tracks=tuple(raw_dataset["tracks"]),
                intents=tuple(raw_dataset["intents"]),
            ),
        )


def _to_domain(payload: DatasetPayload) -> domain.Case2Dataset:
    meta = payload.meta
    return domain.Case2Dataset(
        meta=domain.DatasetMeta(
            schema_version=meta.schema_version,
            dataset_id=meta.dataset_id,
            case_id=meta.case_id,
            phase=meta.phase,
            version=meta.version,
            generated_at=meta.generated_at,
            history_through=date.fromisoformat(meta.history_through),
        ),
        users=tuple(_to_user(user) for user in payload.users),
        events=tuple(
            domain.Event(event.event_id, event.user_id, event.started_at, event.ended_at)
            for event in payload.events
        ),
        chat_history=tuple(
            domain.ChatMessage(
                message.message_id, message.user_id, message.role, message.at, message.text
            )
            for message in payload.chat_history
        ),
        stays=tuple(
            domain.Stay(
                stay.record_id, stay.user_id, stay.check_in_at,
                stay.target_wake_at, stay.hotel_timezone,
            )
            for stay in payload.stays
        ),
        requests=tuple(
            domain.Request(
                record_id=request.record_id,
                user_id=request.user_id,
                stay_id=request.stay_id,
                at=request.at,
                initial_state=request.initial_state,
                utterances=tuple(
                    domain.Utterance(utterance.at, utterance.text)
                    for utterance in request.utterances
                ),
            )
            for request in payload.requests
        ),
        devices=tuple(
            domain.Device(
                device.device_id,
                device.title,
                tuple(
                    domain.DeviceField(
                        field.field_id, field.description, field.value_schema,
                        field.default_value, field.unit,
                    )
                    for field in device.fields
                ),
            )
            for device in payload.devices
        ),
        tracks=tuple(domain.Track(track.track_id, track.title) for track in payload.tracks),
        intents=tuple(
            domain.IntentSpec(intent.intent_id, intent.description)
            for intent in payload.intents
        ),
        sleep_rules=tuple(
            domain.SleepRule(rule.rule_id, rule.version, rule.description)
            for rule in payload.sleep_rules
        ),
    )


def _to_user(user: UserPayload) -> domain.User:
    return domain.User(
        user_id=user.user_id,
        timezone=user.timezone,
        chronotype=user.chronotype,
        weekday_bedtime=time.fromisoformat(user.weekday_bedtime) if user.weekday_bedtime else None,
        weekday_wake_time=(
            time.fromisoformat(user.weekday_wake_time) if user.weekday_wake_time else None
        ),
        sleep_preferences=_to_preferences(user.sleep_preferences) if user.sleep_preferences else None,
    )


def _to_preferences(preferences: SleepPreferencesPayload) -> domain.SleepPreferences:
    return domain.SleepPreferences(
        temperature=preferences.temperature,
        light_sensitive=preferences.light_sensitive,
        favorite_track_ids=(
            tuple(preferences.favorite_track_ids)
            if preferences.favorite_track_ids is not None
            else None
        ),
        dislikes=tuple(preferences.dislikes) if preferences.dislikes is not None else None,
    )
