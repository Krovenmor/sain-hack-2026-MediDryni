"""Hack API port owned by the application."""

from __future__ import annotations

from typing import Protocol

from concierge_service.ports.dataset import ParsedDataset

class HackApi(Protocol):
    async def get_case2_data(
        self, *, dataset_code: str | None, token: str
    ) -> ParsedDataset:
        """Load and parse the full dataset for one /run call.

        The token is forwarded unchanged.
        """
        ...


class HackApiError(Exception):
    """Expected Hack API failure with a safe status and error code."""

    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(code)
