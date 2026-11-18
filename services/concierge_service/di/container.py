"""Manual composition root."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from concierge_service.di.config import Settings
from concierge_service.infra.hack_api import HackApiAdapter
from concierge_service.service.run_case import RunCase


@dataclass
class Container:
    run_case: RunCase
    http_client: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.http_client.aclose()


def build_container(settings: Settings) -> Container:
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.hack_api_timeout_s),
        follow_redirects=False,
    )
    hack_api = HackApiAdapter(base_url=settings.hack_api_url, client=client)
    return Container(run_case=RunCase(hack_api), http_client=client)
