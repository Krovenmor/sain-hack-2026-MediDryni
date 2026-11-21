"""Hack API port owned by the application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from concierge_service.ports.dataset import ParsedDataset

class HackApi(Protocol):
    async def get_case2_data(
        self, *, dataset_code: str | None, token: str
    ) -> ParsedDataset:
        """Load and parse the full dataset for one /run call.

        The token is forwarded unchanged.
        """
        ...


@dataclass(frozen=True)
class DemoRun:
    run_id: UUID
    dataset_id: str
    expires_at: datetime
    stage: Literal["dev", "final"]


class DemoHackApi(HackApi, Protocol):
    async def create_demo_run(self, *, stage: Literal["dev", "final"], token: str) -> DemoRun: ...


class HackApiError(Exception):
    """Expected Hack API failure with a safe status and error code."""

    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(code)
