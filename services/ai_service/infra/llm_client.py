import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite

import httpx

from concierge_service.ports.ai import AiAgentError, AiErrorCode


@dataclass(frozen=True, repr=False)
class LlmCompletion:
    text: str
    usage: dict[str, object] | None


class LlmClientError(AiAgentError):
    def __init__(
        self, code: AiErrorCode, *, retry_after_s: float | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(code)
        self.retry_after_s = retry_after_s
        self.http_status = http_status


class LlmClient:
    def __init__(
        self, http: httpx.AsyncClient, *, base_url: str, api_key: str, model: str
    ) -> None:
        self._http = http
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model

    @classmethod
    def from_env(cls, http: httpx.AsyncClient) -> "LlmClient":
        return cls(
            http,
            base_url=os.environ["LLM_BASE_URL"].strip(),
            api_key=os.environ["LLM_API_KEY"].strip(),
            model=os.environ["LLM_MODEL"].strip(),
        )

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int,
        timeout_s: float,
    ) -> LlmCompletion:
        if not isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")

        try:
            async with asyncio.timeout(timeout_s):
                response = await self._http.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "messages": [dict(message) for message in messages],
                        "max_tokens": max_tokens,
                    },
                    timeout=timeout_s,
                    follow_redirects=False,
                )
        except (TimeoutError, httpx.TimeoutException):
            raise LlmClientError("timeout") from None
        except (httpx.RequestError, httpx.InvalidURL):
            raise LlmClientError("unavailable") from None

        if response.status_code == 429:
            raise LlmClientError(
                "rate_limited",
                retry_after_s=_retry_after_seconds(response.headers.get("Retry-After")),
                http_status=response.status_code,
            )
        if not response.is_success:
            raise LlmClientError("unavailable", http_status=response.status_code)
        return _parse_completion(response)


def _parse_completion(response: httpx.Response) -> LlmCompletion:
    try:
        payload = response.json()
        choice = payload["choices"][0]
        if choice["finish_reason"] != "stop":
            raise LlmClientError("invalid_response")
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LlmClientError("invalid_response") from None

    if not isinstance(content, str) or not content.strip():
        raise LlmClientError("invalid_response")
    usage = payload.get("usage")
    return LlmCompletion(text=content, usage=usage if isinstance(usage, dict) else None)


def _retry_after_seconds(header: str | None) -> float | None:
    if header is None:
        return None
    try:
        seconds = float(header)
    except ValueError:
        try:
            date = parsedate_to_datetime(header)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, OverflowError):
            return None
    return seconds if isfinite(seconds) and seconds >= 0 else None
