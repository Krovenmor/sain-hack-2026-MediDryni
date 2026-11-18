"""Application service for the online /run flow."""

from __future__ import annotations

from concierge_service.domain.answers import RunResult
from concierge_service.domain.dataset import build_dataset_index
from concierge_service.ports.hack_api import HackApi


class UnsupportedCase(Exception):
    """The request targets a case other than case 2."""


class RunCase:
    def __init__(self, hack_api: HackApi) -> None:
        self._hack_api = hack_api

    async def run(
        self, *, case: int, dataset_code: str | None, token: str
    ) -> RunResult:
        if case != 2:
            raise UnsupportedCase
        parsed = await self._hack_api.get_case2_data(
            dataset_code=dataset_code, token=token
        )
        build_dataset_index(parsed.dataset)
        return RunResult()
