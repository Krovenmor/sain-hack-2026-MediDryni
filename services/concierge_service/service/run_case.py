"""Application service for the online /run flow."""

from __future__ import annotations

import asyncio
import logging

from concierge_service.domain.answers import RunResult, StayAnswer
from concierge_service.domain.dataset import DatasetIndex, build_dataset_index
from concierge_service.domain.sleep import SleepPlanError, SleepPlanner
from concierge_service.ports.ai import AiAgent, AiAgentError, PreferenceHints, ProfileContext
from concierge_service.ports.dataset import DatasetSource
from concierge_service.ports.hack_api import HackApi
from concierge_service.ports.room import RoomApi, RoomApiError
from concierge_service.service.preference_resolver import resolve_preferences

logger = logging.getLogger(__name__)
_RUN_BUDGET_S = 45 * 60


class UnsupportedCase(Exception):
    """The request targets a case other than case 2."""


class RunCase:
    def __init__(
        self,
        hack_api: HackApi,
        *,
        room_api: RoomApi,
        sleep_planner: SleepPlanner,
        ai_agent: AiAgent,
        preference_timeout_s: float = 60.0,
    ) -> None:
        self._hack_api = hack_api
        self._room_api = room_api
        self._sleep_planner = sleep_planner
        self._ai_agent = ai_agent
        self._preference_timeout_s = preference_timeout_s

    async def run(
        self, *, case: int, dataset_code: str | None, token: str
    ) -> RunResult:
        if case != 2:
            raise UnsupportedCase
        deadline = asyncio.get_running_loop().time() + _RUN_BUDGET_S
        parsed = await self._hack_api.get_case2_data(
            dataset_code=dataset_code, token=token
        )
        index = build_dataset_index(parsed.dataset)
        hints_by_user = await self._extract_preferences(index, parsed.source, deadline=deadline)
        preferences_by_user = {
            user_id: resolve_preferences(user, index.tracks_by_id, hints_by_user.get(user_id))
            for user_id, user in index.users_by_id.items()
        }
        answers: list[StayAnswer] = []
        for stay in parsed.dataset.stays:
            try:
                preferences = preferences_by_user[stay.user_id]
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

    async def _extract_preferences(
        self, index: DatasetIndex, source: DatasetSource, *, deadline: float
    ) -> dict[str, PreferenceHints]:
        profiles = tuple(
            ProfileContext(
                user=source.users_by_id[user_id],
                events=tuple(
                    source.events_by_user_and_id[user_id, event.event_id]
                    for event in index.events_by_user[user_id]
                ),
                chat_history=tuple(
                    source.messages_by_user_and_id[user_id, message.message_id]
                    for message in index.chat_history_by_user[user_id]
                ),
                devices=source.devices,
                tracks=source.tracks,
            )
            for user_id in index.users_by_id
        )
        budget = min(self._preference_timeout_s, deadline - asyncio.get_running_loop().time())
        if budget <= 0:
            return {}

        try:
            async with asyncio.timeout(budget):
                results = await self._ai_agent.extract_preference_hints(profiles, timeout_s=budget)
        except AiAgentError as exc:
            logger.warning("Preference extraction failed: %s", exc.code)
            return {}
        except TimeoutError:
            logger.warning("Preference extraction timed out")
            return {}

        if tuple(result.user_id for result in results) != tuple(index.users_by_id):
            logger.warning("Preference extraction returned mismatched user IDs")
            return {}
        return {result.user_id: result for result in results if isinstance(result, PreferenceHints)}
