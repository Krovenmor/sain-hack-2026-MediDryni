"""Retry policy for idempotent calls to Hack API."""

import asyncio
import logging

import httpx

from run_telemetry import log_event

logger = logging.getLogger(__name__)
_TRANSIENT_STATUSES = {408, 500, 502, 503, 504}
_RETRY_DELAY_S = 0.25


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
    json: object = None,
) -> httpx.Response:
    try:
        response = await client.request(method, url, headers=headers, params=params, json=json)
    except httpx.TransportError:
        status = None
    else:
        status = response.status_code
        if status not in _TRANSIENT_STATUSES:
            return response

    log_event(logger, "hack_api_retry", method=method, http_status=status)
    await asyncio.sleep(_RETRY_DELAY_S)
    return await client.request(method, url, headers=headers, params=params, json=json)
