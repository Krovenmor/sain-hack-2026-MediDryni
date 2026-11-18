"""Application service for the online /run flow."""

from __future__ import annotations

import logging

from concierge_service.domain.answers import RunResult, StayAnswer
from concierge_service.domain.dataset import build_dataset_index
from concierge_service.domain.sleep import SleepPlanError, SleepPlanner
from concierge_service.ports.hack_api import HackApi
from concierge_service.ports.room import RoomApi, RoomApiError
from concierge_service.service.preference_resolver import resolve_preferences

logger = logging.getLogger(__name__)


class UnsupportedCase(Exception):
    """The request targets a case other than case 2."""


class RunCase:
    def __init__(
        self,
        hack_api: HackApi,
        *,
        room_api: RoomApi,
        sleep_planner: SleepPlanner,
    ) -> None:
        self._hack_api = hack_api
        self._room_api = room_api
        self._sleep_planner = sleep_planner

    async def run(
        self, *, case: int, dataset_code: str | None, token: str
    ) -> RunResult:
        if case != 2:
            raise UnsupportedCase
        parsed = await self._hack_api.get_case2_data(
            dataset_code=dataset_code, token=token
        )
        index = build_dataset_index(parsed.dataset)
        answers: list[StayAnswer] = []
        for stay in parsed.dataset.stays:
            try:
                preferences = resolve_preferences(index.users_by_id[stay.user_id], index.tracks_by_id)
                steps = self._sleep_planner.plan(stay, preferences, index.device_fields_by_device)
                session = await self._room_api.create_session(
                    record_id=stay.record_id, kind="stay", token=token
                )
                await self._room_api.load_scenario(
                    session_id=session.session_id, steps=steps, token=token
                )
            except (SleepPlanError, RoomApiError) as exc:
                logger.warning("Stay scenario skipped: %s", exc)
                continue
            answers.append(StayAnswer(stay.record_id, str(session.session_id)))
        return RunResult(stays=tuple(answers))
