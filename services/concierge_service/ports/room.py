"""Simulator operations used by the concierge."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from concierge_service.domain.action_planner import DevicePatch
from concierge_service.domain.device_catalog import RoomState
from concierge_service.domain.scenario import ScenarioStep

RoomKind = Literal["stay", "request"]


@dataclass(frozen=True)
class RoomSession:
    session_id: UUID
    room_id: UUID
    run_id: UUID
    record_id: str
    kind: RoomKind
    starts_at: datetime
    until: datetime
    initial_state: RoomState


@dataclass(frozen=True)
class RoomSnapshot:
    session_id: UUID
    at: datetime
    state: RoomState


@dataclass(frozen=True)
class RoomLogEntry:
    revision: int
    recorded_at: datetime
    kind: Literal["scenario", "action"]
    steps: tuple[ScenarioStep, ...]


@dataclass(frozen=True)
class RoomLog:
    session: RoomSession
    closed_at: datetime | None
    entries: tuple[RoomLogEntry, ...]
    final_state: RoomState


class RoomApiError(Exception):
    """The simulator rejected an operation or returned an invalid result."""


class RoomApi(Protocol):
    async def create_session(
        self, *, record_id: str, kind: RoomKind, token: str
    ) -> RoomSession: ...

    async def load_scenario(
        self, *, session_id: UUID, steps: Sequence[ScenarioStep], token: str
    ) -> RoomSnapshot: ...

    async def apply_action(
        self, *, session_id: UUID, patch: DevicePatch, at: datetime, token: str
    ) -> RoomSnapshot: ...

    async def get_state(
        self, *, session_id: UUID, token: str, at: datetime | None = None
    ) -> RoomSnapshot: ...

    async def get_log(self, *, session_id: UUID, token: str) -> RoomLog: ...
