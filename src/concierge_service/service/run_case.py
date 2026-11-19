"""Application service for the online /run flow."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC
from time import monotonic
from uuid import uuid4

from concierge_service.domain.answers import RequestAnswer, RunResult, StayAnswer
from concierge_service.domain.dataset import DatasetIndex, build_dataset_index
from concierge_service.domain.device_catalog import DeviceValueError
from concierge_service.domain.sleep import ResolvedPreferences, SleepPlanError, SleepPlanner
from concierge_service.ports.ai import (
    AiAgent,
    AiAgentError,
    DialogueMessage,
    PreferenceHints,
    ProfileContext,
    RequestContext,
)
from concierge_service.ports.dataset import DatasetSource, ParsedDataset
from concierge_service.ports.hack_api import HackApi
from concierge_service.ports.room import RoomApi, RoomApiError
from concierge_service.ports.store import ConversationStore
from concierge_service.service.diagnostics import catalog_failure, preference_details, setting_values
from concierge_service.service.preference_resolver import resolve_preferences
from concierge_service.service.request_handler import RequestHandler
from shared.telemetry import log_context, log_event

logger = logging.getLogger(__name__)
_RUN_BUDGET_S = 45 * 60 - 5  # Reserve time to validate and serialize the response.
_REQUEST_USER_CONCURRENCY = 4
_PREFERENCE_RETURN_GRACE_S = 0.25


class UnsupportedCase(Exception):
    """The request targets a case other than case 2."""


class RunDeadlineExceeded(Exception):
    """The run expired before the input dataset could be loaded."""


class RunCase:
    def __init__(
        self,
        hack_api: HackApi,
        *,
        room_api: RoomApi,
        sleep_planner: SleepPlanner,
        ai_agent: AiAgent,
        conversation_store_factory: Callable[[], ConversationStore],
        preference_timeout_s: float = 600.0,
    ) -> None:
        self._hack_api = hack_api
        self._room_api = room_api
        self._sleep_planner = sleep_planner
        self._ai_agent = ai_agent
        self._request_handler = RequestHandler(room_api, ai_agent)
        self._conversation_store_factory = conversation_store_factory
        self._preference_timeout_s = preference_timeout_s

    async def run(
        self, *, case: int, dataset_code: str | None, token: str
    ) -> RunResult:
        if case != 2:
            raise UnsupportedCase
        with log_context(run_id=uuid4().hex):
            started = monotonic()
            log_event(logger, "run_started")
            try:
                result = await self._run(dataset_code=dataset_code, token=token)
            except Exception as exc:
                log_event(
                    logger, "run_failed", level=logging.WARNING, error_type=type(exc).__name__,
                    duration_ms=round((monotonic() - started) * 1000),
                )
                raise
            log_event(
                logger, "run_finished", stays=len(result.stays), requests=len(result.requests),
                duration_ms=round((monotonic() - started) * 1000),
            )
            return result

    async def _run(self, *, dataset_code: str | None, token: str) -> RunResult:
        deadline = asyncio.get_running_loop().time() + _RUN_BUDGET_S
        time_limit = asyncio.timeout_at(deadline)
        stays: list[StayAnswer] = []
        requests_by_user: dict[str, list[RequestAnswer]] = {}
        dataset_loaded = False
        try:
            async with time_limit:
                parsed = await self._hack_api.get_case2_data(dataset_code=dataset_code, token=token)
                dataset_loaded = True
                await self._process_dataset(
                    parsed, stays, requests_by_user, token=token, deadline=deadline
                )
        except TimeoutError:
            if not time_limit.expired():
                raise
            if not dataset_loaded:
                raise RunDeadlineExceeded from None
            log_event(logger, "run_deadline_reached", level=logging.WARNING)
        return RunResult(
            stays=tuple(stays),
            requests=tuple(answer for answers in requests_by_user.values() for answer in answers),
        )

    async def _process_dataset(
        self,
        parsed: ParsedDataset,
        stays: list[StayAnswer],
        requests_by_user: dict[str, list[RequestAnswer]],
        *,
        token: str,
        deadline: float,
    ) -> None:
        index = build_dataset_index(parsed.dataset)
        log_event(
            logger, "dataset_loaded", users=len(index.users_by_id), stays=len(index.stays_by_id),
            requests=len(index.requests_by_id),
        )
        profiles = _build_profiles(index, parsed.source)
        preference_started = monotonic()
        hints_by_user = await self._extract_preferences(profiles, deadline=deadline)
        log_event(
            logger, "preferences_finished", profiles=len(profiles), succeeded=len(hints_by_user),
            duration_ms=round((monotonic() - preference_started) * 1000),
        )
        preferences_by_user = {
            user_id: resolve_preferences(user, index.tracks_by_id, hints_by_user.get(user_id))
            for user_id, user in index.users_by_id.items()
        }
        if logger.isEnabledFor(logging.DEBUG):
            for profile_number, resolved in enumerate(preferences_by_user.values(), 1):
                log_event(
                    logger, "preference_resolved", level=logging.DEBUG, profile_number=profile_number,
                    bedtime=resolved.bedtime.isoformat(), has_home_timezone=resolved.home_timezone is not None,
                    night_temperature=resolved.night_temperature, light_sensitive=resolved.light_sensitive,
                    gentle_alarm=resolved.gentle_alarm, favorite_track_ids=[
                        track for track in resolved.favorite_track_ids
                        if "track_id" in setting_values({"track_id": track})
                    ],
                )
        profile_numbers = {user_id: number for number, user_id in enumerate(profiles, 1)}
        for stay in parsed.dataset.stays:
            try:
                preferences = preferences_by_user[stay.user_id]
                steps = self._sleep_planner.plan(stay, preferences, index.device_fields_by_device)
                if logger.isEnabledFor(logging.DEBUG):
                    log_event(
                        logger, "stay_scenario", level=logging.DEBUG, record_id=stay.record_id,
                        profile_number=profile_numbers[stay.user_id],
                        steps=[
                            {
                                "seconds_from_check_in": (
                                    step.at.astimezone(UTC) - stay.check_in_at.astimezone(UTC)
                                ).total_seconds(),
                                "device": step.device_id, "fields": sorted(step.values),
                                "values": setting_values(step.values),
                            }
                            for step in steps
                        ],
                    )
                session = await self._room_api.create_session(
                    record_id=stay.record_id, kind="stay", token=token
                )
                await self._room_api.load_scenario(
                    session_id=session.session_id, steps=steps, token=token
                )
            except (SleepPlanError, RoomApiError) as exc:
                reason = "invalid_scenario" if isinstance(exc, SleepPlanError) else "simulator_error"
                log_event(
                    logger, "stay_skipped", level=logging.WARNING,
                    record_id=stay.record_id, reason=reason,
                    http_status=exc.http_status if isinstance(exc, RoomApiError) else None,
                )
                if isinstance(exc.__cause__, DeviceValueError):
                    log_event(
                        logger, "stay_catalog_rejected", level=logging.DEBUG, record_id=stay.record_id,
                        **catalog_failure(exc.__cause__),
                    )
                continue
            stays.append(StayAnswer(stay.record_id, str(session.session_id)))
            log_event(logger, "stay_finished", record_id=stay.record_id, steps=len(steps))
        await self._process_requests(
            index, parsed.source, profiles, hints_by_user, preferences_by_user,
            requests_by_user, token=token, deadline=deadline,
        )

    async def _extract_preferences(
        self, profiles: dict[str, ProfileContext], *, deadline: float
    ) -> dict[str, PreferenceHints]:
        now = asyncio.get_running_loop().time()
        # The agent needs time to return completed hints after its own timeout.
        budget = min(self._preference_timeout_s, deadline - now - _PREFERENCE_RETURN_GRACE_S)
        if budget <= 0:
            return {}

        try:
            async with asyncio.timeout_at(min(deadline, now + budget + _PREFERENCE_RETURN_GRACE_S)):
                results = await self._ai_agent.extract_preference_hints(
                    tuple(profiles.values()), timeout_s=budget
                )
        except AiAgentError as exc:
            log_event(logger, "preferences_failed", level=logging.WARNING, reason=exc.code)
            return {}
        except TimeoutError:
            log_event(logger, "preferences_failed", level=logging.WARNING, reason="timeout")
            return {}

        if tuple(result.user_id for result in results) != tuple(profiles):
            log_event(
                logger, "preferences_failed", level=logging.WARNING, reason="mismatched_user_ids"
            )
            return {}
        hints = {}
        for profile_number, result in enumerate(results, 1):
            if isinstance(result, PreferenceHints):
                hints[result.user_id] = result
                log_event(logger, "preference_profile", profile_number=profile_number,
                          status="ok", observations=len(result.settings))
                if logger.isEnabledFor(logging.DEBUG):
                    log_event(
                        logger, "preference_hints", level=logging.DEBUG, profile_number=profile_number,
                        **preference_details(result),
                    )
            else:
                log_event(logger, "preference_profile", profile_number=profile_number,
                          status=result.error)
        return hints

    async def _process_requests(
        self,
        index: DatasetIndex,
        source: DatasetSource,
        profiles: dict[str, ProfileContext],
        hints: dict[str, PreferenceHints],
        preferences: dict[str, ResolvedPreferences],
        requests_by_user: dict[str, list[RequestAnswer]],
        *,
        token: str,
        deadline: float,
    ) -> None:
        store = self._conversation_store_factory()
        user_slots = asyncio.Semaphore(_REQUEST_USER_CONCURRENCY)

        async def process_user(user_id: str, profile_number: int) -> None:
            async with user_slots:
                for request in index.requests_by_user[user_id]:
                    context = RequestContext(
                        profile=profiles[user_id],
                        request=source.requests_by_id[request.record_id],
                        stay=source.stays_by_id[request.stay_id],
                        intents=source.intents,
                        conversation=store.history(user_id),
                        preference_hints=hints.get(user_id),
                    )
                    with log_context(record_id=request.record_id, profile_number=profile_number):
                        answer = await self._request_handler.handle(
                            request, context, index.device_fields_by_device,
                            preferences=preferences[user_id], token=token, deadline=deadline,
                        )
                    if answer is None:
                        continue
                    requests_by_user[user_id].append(answer)
                    utterances = tuple(
                        DialogueMessage(role="user", at=item.at.isoformat(), text=item.text)
                        for item in request.utterances
                    )
                    replies = tuple(
                        DialogueMessage(role=reply.role, at=reply.at, text=reply.text, applied=answer.applied or None)
                        for reply in answer.replies
                    )
                    store.append(user_id, utterances + replies)

        async with asyncio.TaskGroup() as tasks:
            for profile_number, (user_id, requests) in enumerate(index.requests_by_user.items(), 1):
                if requests:
                    requests_by_user[user_id] = []
                    tasks.create_task(process_user(user_id, profile_number))


def _build_profiles(index: DatasetIndex, source: DatasetSource) -> dict[str, ProfileContext]:
    return {
        user_id: ProfileContext(
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
    }
