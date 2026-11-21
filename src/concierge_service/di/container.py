"""Manual composition root."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ai_service.infra.llm_client import LlmClient
from ai_service.service.agent import LlmAgent
from concierge_service.di.config import Settings
from concierge_service.domain.sleep import SleepPlanner
from concierge_service.infra.conversation_store import InMemoryConversationStore
from concierge_service.infra.hack_api import HackApiAdapter
from concierge_service.infra.mock_ai import MockAiAgent
from concierge_service.infra.room import RoomApiAdapter
from concierge_service.ports.ai import AiAgent
from concierge_service.service.demo import DemoService
from concierge_service.service.run_case import RunCase
from shared.telemetry import log_event

logger = logging.getLogger(__name__)


@dataclass
class Container:
    run_case: RunCase
    http_client: httpx.AsyncClient
    demo: DemoService

    async def aclose(self) -> None:
        await self.http_client.aclose()


def build_container(settings: Settings, *, ai_agent: AiAgent | None = None) -> Container:
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.hack_api_timeout_s),
        follow_redirects=False,
    )
    hack_api = HackApiAdapter(base_url=settings.hack_api_url, client=client)
    room_api = RoomApiAdapter(base_url=settings.hack_api_url, client=client)
    if ai_agent is None and settings.llm is not None:
        llm = settings.llm
        ai_agent = LlmAgent(LlmClient(
            client, base_url=llm.base_url, api_key=llm.api_key, model=llm.model
        ))
    if ai_agent is None:
        log_event(
            logger, "llm_not_configured", level=logging.WARNING,
            detail="LLM_BASE_URL, LLM_API_KEY and LLM_MODEL are not set; using the mock AI, "
            "every request will be refused",
        )
        ai_agent = MockAiAgent()
    run_case = RunCase(
        hack_api,
        room_api=room_api,
        sleep_planner=SleepPlanner(),
        ai_agent=ai_agent,
        conversation_store_factory=InMemoryConversationStore,
    )
    return Container(run_case, client, DemoService(hack_api, room_api, ai_agent, settings.team_token))
