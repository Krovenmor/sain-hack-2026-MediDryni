"""Interpret and execute one request in its own simulator session."""

import asyncio
import logging
from uuid import UUID

from concierge_service.domain.action_planner import (
    ActionPlanError,
    patch_matches_state,
    plan_actions,
)
from concierge_service.domain.answers import RequestAnswer
from concierge_service.domain.dataset import JSONScalar, Request
from concierge_service.domain.device_catalog import DeviceFields
from concierge_service.ports.ai import (
    AiAgent,
    AiAgentError,
    CurtainsInterpretation,
    DoNotDisturbInterpretation,
    NumericInterpretation,
    RelativeAmount,
    RequestContext,
    RequestInterpretation,
)
from concierge_service.ports.room import RoomApi, RoomApiError
from concierge_service.service.reply_composer import RequestOutcome, compose_replies

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
        token: str,
        deadline: float,
    ) -> RequestAnswer | None:
        try:
            session = await self._room_api.create_session(
                record_id=request.record_id, kind="request", token=token
            )
        except RoomApiError as exc:
            logger.warning("Request session skipped: %s", exc)
            return None

        interpreted = await self._interpret(request, context, deadline)
        if interpreted is None:
            replies = compose_replies(request, "unrecognized")
        else:
            outcome = await self._execute(request, interpreted, catalog, session.session_id, token)
            replies = compose_replies(request, outcome, intent=interpreted.intent)

        return RequestAnswer(request.record_id, str(session.session_id), replies)

    async def _interpret(
        self, request: Request, context: RequestContext, deadline: float
    ) -> RequestInterpretation | None:
        budget = min(_INTERPRETATION_TIMEOUT_S, deadline - asyncio.get_running_loop().time())
        if budget <= 0:
            return None
        try:
            async with asyncio.timeout(budget):
                result = await self._ai_agent.interpret_request(context, timeout_s=budget)
        except (AiAgentError, TimeoutError):
            return None
        return result if result.record_id == request.record_id else None

    async def _execute(
        self,
        request: Request,
        interpreted: RequestInterpretation,
        catalog: DeviceFields,
        session_id: UUID,
        token: str,
    ) -> RequestOutcome:
        target, amount = _requested_value(interpreted)
        try:
            patches = plan_actions(
                interpreted.intent, request.initial_state, catalog, target=target, amount=amount
            )
        except ActionPlanError as exc:
            return exc.reason

        for index, patch in enumerate(patches):
            try:
                snapshot = await self._room_api.apply_action(
                    session_id=session_id, patch=patch, at=request.at, token=token
                )
                if snapshot.at != request.at or not patch_matches_state(patch, snapshot.state):
                    raise RoomApiError("simulator did not confirm the settings at the request time")
            except RoomApiError as exc:
                logger.warning("Request action stopped: %s", exc)
                return "partial" if index else "room_error"

        return "applied" if patches else "unchanged"


def _requested_value(interpreted: RequestInterpretation) -> tuple[JSONScalar, float | None]:
    if isinstance(interpreted, NumericInterpretation):
        slots = interpreted.slots
        if isinstance(slots, RelativeAmount):
            return None, slots.amount
        return slots.value, None
    if isinstance(interpreted, CurtainsInterpretation):
        return interpreted.slots.position, None
    if isinstance(interpreted, DoNotDisturbInterpretation):
        return interpreted.slots.enabled, None
    return None, None
