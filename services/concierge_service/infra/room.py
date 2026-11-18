"""HTTP adapter for the room simulator."""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal, TypeVar
from uuid import UUID

import httpx
from pydantic import Field, ValidationError

from concierge_service.domain.action_planner import DevicePatch
from concierge_service.domain.scenario import ScenarioStep
from concierge_service.infra.wire import DeviceState, Identifier, RoomState, Timestamp, WireModel
from concierge_service.ports.room import (
    RoomApiError,
    RoomKind,
    RoomLog,
    RoomLogEntry,
    RoomSession,
    RoomSnapshot,
)


class _SessionPayload(WireModel):
    session_id: UUID
    room_id: UUID
    run_id: UUID
    team_id: Annotated[str, Field(min_length=1)]
    dataset_id: Identifier
    record_id: Identifier
    stay_id: Identifier
    kind: RoomKind
    starts_at: Timestamp = Field(alias="from")
    until: Timestamp
    at: Timestamp
    initial_state: RoomState

    def to_session(self) -> RoomSession:
        return RoomSession(
            session_id=self.session_id,
            room_id=self.room_id,
            run_id=self.run_id,
            record_id=self.record_id,
            kind=self.kind,
            starts_at=self.starts_at,
            until=self.until,
            initial_state=self.initial_state,
        )


class _SnapshotPayload(WireModel):
    session_id: UUID
    room_id: UUID
    at: Timestamp
    state: RoomState


class _StepPayload(WireModel):
    at: Timestamp
    device_id: Identifier
    values: DeviceState


class _LogEntryPayload(WireModel):
    revision: Annotated[int, Field(ge=1)]
    recorded_at: Timestamp
    kind: Literal["scenario", "action"]
    steps: list[_StepPayload]


class _LogPayload(WireModel):
    session: _SessionPayload
    closed_at: Timestamp | None
    entries: list[_LogEntryPayload]
    final_state: RoomState


Payload = TypeVar("Payload", bound=WireModel)


class RoomApiAdapter:
    def __init__(self, *, base_url: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def create_session(
        self, *, record_id: str, kind: RoomKind, token: str
    ) -> RoomSession:
        payload = await self._request(
            "POST",
            "/room/session",
            _SessionPayload,
            token=token,
            json={"record_id": record_id, "kind": kind},
        )
        if payload.record_id != record_id or payload.kind != kind:
            raise RoomApiError("session does not match the record")
        return payload.to_session()

    async def load_scenario(
        self, *, session_id: UUID, steps: Sequence[ScenarioStep], token: str
    ) -> RoomSnapshot:
        payload = await self._request(
            "POST",
            f"/room/{session_id}/scenario",
            _SnapshotPayload,
            token=token,
            json={
                "steps": [
                    {"at": step.at.isoformat(), "device_id": step.device_id, "values": dict(step.values)}
                    for step in steps
                ]
            },
        )
        return _snapshot(payload, session_id)

    async def apply_action(
        self, *, session_id: UUID, patch: DevicePatch, at: datetime, token: str
    ) -> RoomSnapshot:
        payload = await self._request(
            "POST",
            f"/room/{session_id}/action",
            _SnapshotPayload,
            token=token,
            json={"at": at.isoformat(), "device_id": patch.device_id, "values": dict(patch.values)},
        )
        return _snapshot(payload, session_id)

    async def get_state(
        self, *, session_id: UUID, token: str, at: datetime | None = None
    ) -> RoomSnapshot:
        payload = await self._request(
            "GET",
            f"/room/{session_id}/state",
            _SnapshotPayload,
            token=token,
            params={"at": at.isoformat()} if at is not None else None,
        )
        return _snapshot(payload, session_id)

    async def get_log(self, *, session_id: UUID, token: str) -> RoomLog:
        payload = await self._request("GET", f"/room/{session_id}/log", _LogPayload, token=token)
        if payload.session.session_id != session_id:
            raise RoomApiError("log does not match the session")
        return RoomLog(
            session=payload.session.to_session(),
            closed_at=payload.closed_at,
            entries=tuple(
                RoomLogEntry(
                    entry.revision,
                    entry.recorded_at,
                    entry.kind,
                    tuple(ScenarioStep(step.at, step.device_id, step.values) for step in entry.steps),
                )
                for entry in payload.entries
            ),
            final_state=payload.final_state,
        )

    async def _request(
        self,
        method: str,
        path: str,
        response_model: type[Payload],
        *,
        token: str,
        json: object = None,
        params: dict[str, str] | None = None,
    ) -> Payload:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json,
                params=params,
            )
        except httpx.HTTPError:
            raise RoomApiError("simulator unavailable") from None
        if response.status_code != 200:
            raise RoomApiError(f"simulator returned HTTP {response.status_code}")
        try:
            return response_model.model_validate_json(response.content)
        except ValidationError:
            raise RoomApiError("invalid simulator response") from None


def _snapshot(payload: _SnapshotPayload, session_id: UUID) -> RoomSnapshot:
    if payload.session_id != session_id:
        raise RoomApiError("snapshot does not match the session")
    return RoomSnapshot(payload.session_id, payload.at, payload.state)
