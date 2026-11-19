"""Interpret and execute one request in its own simulator session."""

import asyncio
import logging
from time import monotonic
from uuid import UUID

from concierge_service.domain.action_planner import (
    ActionPlanError,
    patch_matches_state,
)
from concierge_service.domain.answers import RequestAnswer
from concierge_service.domain.dataset import Request
from concierge_service.domain.device_catalog import DeviceFields, DeviceValueError
from concierge_service.domain.sleep import ResolvedPreferences
from concierge_service.ports.ai import (
    AiAgent,
    AiAgentError,
    InvalidValueInterpretation,
    RequestContext,
    RequestInterpretation,
)
from concierge_service.ports.room import RoomApi, RoomApiError
from concierge_service.service.diagnostics import catalog_failure, interpretation_slots, setting_values
from concierge_service.service.reply_composer import RequestOutcome, compose_replies
from concierge_service.service.request_planning import plan_request
from shared.telemetry import log_event

logger = logging.getLogger(__name__)
_INTERPRETATION_TIMEOUT_S = 60.0


class RequestHandler:
    def __init__(self, room_api: RoomApi, ai_agent: AiAgent) -> None:
        self._room_api = room_api
        self._ai_agent = ai_agent

    async def handle(
        self,
        request: Request,
        context: RequestContext,
        catalog: DeviceFields,
        *,
        preferences: ResolvedPreferences,
        token: str,
        deadline: float,
    ) -> RequestAnswer | None:
        started = monotonic()
        intent = None
        ai_error = None
        try:
            session = await self._room_api.create_session(
                record_id=request.record_id, kind="request", token=token
            )
        except RoomApiError as exc:
            log_event(
                logger, "request_session_failed", level=logging.WARNING,
                record_id=request.record_id, http_status=exc.http_status,
                duration_ms=round((monotonic() - started) * 1000),
            )
            return None

        try:
            interpreted = await self._interpret(request, context, deadline)
        except AiAgentError as exc:
            ai_error = exc.code
            outcome = "missing_history" if exc.code == "unresolved_reference" else "unrecognized"
            replies = compose_replies(request, outcome)
        except TimeoutError:
            ai_error = "timeout"
            outcome = "unrecognized"
            replies = compose_replies(request, outcome)
        else:
            intent = interpreted.intent
            if logger.isEnabledFor(logging.DEBUG):
                log_event(
                    logger, "request_interpreted", level=logging.DEBUG,
                    intent=intent, slots=interpretation_slots(interpreted),
                )
            outcome = await self._execute(
                request, interpreted, context, preferences, catalog, session.session_id, token
            )
            replies = compose_replies(request, outcome, intent=interpreted.intent)

        log_event(
            logger, "request_finished", record_id=request.record_id, intent=intent,
            outcome=outcome, ai_error=ai_error, reply_class=replies[0].response_class,
            duration_ms=round((monotonic() - started) * 1000),
        )
        return RequestAnswer(request.record_id, str(session.session_id), replies)

    async def _interpret(
        self, request: Request, context: RequestContext, deadline: float
    ) -> RequestInterpretation:
        budget = min(_INTERPRETATION_TIMEOUT_S, deadline - asyncio.get_running_loop().time())
        if budget <= 0:
            raise AiAgentError("timeout")
        async with asyncio.timeout(budget):
            result = await self._ai_agent.interpret_request(context, timeout_s=budget)
        if result.record_id != request.record_id:
            raise AiAgentError("invalid_response")
        return result

    async def _execute(
        self,
        request: Request,
        interpreted: RequestInterpretation,
        context: RequestContext,
        preferences: ResolvedPreferences,
        catalog: DeviceFields,
        session_id: UUID,
        token: str,
    ) -> RequestOutcome:
        if interpreted.intent in ("medical", "out_of_scope"):
            return interpreted.intent
        if isinstance(interpreted, InvalidValueInterpretation):
            return interpreted.slots.reason
        try:
            patches = plan_request(request, interpreted, context, preferences, catalog)
        except ActionPlanError as exc:
            cause = exc.__cause__
            detail = catalog_failure(cause) if isinstance(cause, DeviceValueError) else {}
            log_event(
                logger, "action_plan_rejected", level=logging.DEBUG, intent=interpreted.intent,
                reason=exc.reason, **detail,
            )
            return exc.reason

        log_event(logger, "action_plan_validated", level=logging.DEBUG, actions=len(patches))
        for index, patch in enumerate(patches):
            if logger.isEnabledFor(logging.DEBUG):
                initial = request.initial_state.get(patch.device_id, {})
                log_event(
                    logger, "action_planned", level=logging.DEBUG, device=patch.device_id,
                    fields=sorted(patch.values), values=setting_values(patch.values),
                    before=setting_values({key: initial[key] for key in patch.values if key in initial}),
                )
            try:
                snapshot = await self._room_api.apply_action(
                    session_id=session_id, patch=patch, at=request.at, token=token
                )
                matched = patch_matches_state(patch, snapshot.state)
                if logger.isEnabledFor(logging.DEBUG):
                    actual = snapshot.state.get(patch.device_id, {})
                    log_event(
                        logger, "action_snapshot", level=logging.DEBUG, device=patch.device_id,
                        matches_patch=matched, matches_time=snapshot.at == request.at,
                        values=setting_values({key: actual[key] for key in patch.values if key in actual}),
                    )
                if snapshot.at != request.at or not matched:
                    raise RoomApiError("simulator did not confirm the settings at the request time")
            except RoomApiError as exc:
                log_event(
                    logger, "request_action_failed", level=logging.WARNING,
                    device=patch.device_id, confirmed_actions=index, http_status=exc.http_status,
                )
                return "partial" if index else "room_error"

        return "applied" if patches else "unchanged"
