"""Manual composition root."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from concierge_service.di.config import Settings
from concierge_service.domain.sleep import SleepPlanner
from concierge_service.infra.hack_api import HackApiAdapter
from concierge_service.infra.mock_ai import MockAiAgent
from concierge_service.infra.room import RoomApiAdapter
from concierge_service.ports.ai import AiAgent
from concierge_service.service.run_case import RunCase


@dataclass
class Container:
    run_case: RunCase
    http_client: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.http_client.aclose()


def build_container(settings: Settings, *, ai_agent: AiAgent | None = None) -> Container:
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.hack_api_timeout_s),
        follow_redirects=False,
    )
    hack_api = HackApiAdapter(base_url=settings.hack_api_url, client=client)
    room_api = RoomApiAdapter(base_url=settings.hack_api_url, client=client)
    run_case = RunCase(
        hack_api,
        room_api=room_api,
        sleep_planner=SleepPlanner(),
        ai_agent=ai_agent if ai_agent is not None else MockAiAgent(),
    )
    return Container(run_case=run_case, http_client=client)
