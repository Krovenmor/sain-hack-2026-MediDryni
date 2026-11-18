import asyncio
from collections.abc import Iterator, Sequence
from math import isfinite
from typing import cast

from concierge_service.ports.ai import (
    AiAgentError,
    PreferenceFailure,
    PreferenceResult,
    ProfileContext,
    RequestContext,
    RequestInterpretation,
)

from ai_service.infra.llm_client import LlmClient, LlmClientError
from ai_service.service.context import compact_json
from ai_service.service.limits import LimitedCompletions
from ai_service.service.preferences import (
    PreferenceInput,
    parse_preference_batch,
    preference_messages,
)
from ai_service.service.request_interpretation import request_messages, validate_interpretation


_PREFERENCE_SINGLE_MAX_TOKENS = 10_000
_PREFERENCE_LENGTH_RETRY_MAX_TOKENS = 16_000


class LlmAgent:
    def __init__(
        self, client: LlmClient, *, requests_per_minute: int = 30,
        concurrency: int = 4, preference_batch_size: int = 3,
    ) -> None:
        if not 1 <= preference_batch_size <= 3:
            raise ValueError("preference_batch_size must be between 1 and 3")
        self._batch_size = preference_batch_size
        self._completions = LimitedCompletions(
            client, requests_per_minute=requests_per_minute, concurrency=concurrency
        )

    def usage(self) -> dict[str, int]:
        return {
            "attempts": self._completions.attempts,
            "responses_with_usage": self._completions.responses_with_usage,
            "total_tokens": self._completions.total_tokens,
        }

    async def extract_preference_hints(
        self, items: Sequence[ProfileContext], *, timeout_s: float
    ) -> list[PreferenceResult]:
        deadline = _deadline(timeout_s)
        results: list[PreferenceResult] = []
        for batch in _preference_batches(items, self._batch_size, deadline):
            if len(batch) == 1:
                results.append(await self._extract_one(batch[0], deadline))
                continue
            try:
                parsed = await self._completions.run(
                    preference_messages(batch),
                    lambda text: parse_preference_batch(text, batch),
                    deadline=deadline, max_tokens=_PREFERENCE_SINGLE_MAX_TOKENS * len(batch),
                    operation="preference_batch",
                    retry_invalid=False,
                )
            except AiAgentError as error:
                size_error = isinstance(error, LlmClientError) and error.reason == "request_too_large"
                if error.code != "invalid_response" and not size_error:
                    results.extend(
                        PreferenceFailure(user_id=item.user_id, error=error.code) for item in batch
                    )
                    continue
                parsed = {}
            for item in batch:
                if item.user_id in parsed:
                    results.append(parsed[item.user_id])
                else:
                    results.append(await self._extract_one(item, deadline, retry_invalid=False))
        return results

    async def _extract_one(
        self, item: PreferenceInput, deadline: float, *, retry_invalid: bool = True
    ) -> PreferenceResult:
        try:
            return await self._completions.run(
                preference_messages([item]), item.parse,
                deadline=deadline, max_tokens=_PREFERENCE_SINGLE_MAX_TOKENS,
                retry_invalid=retry_invalid,
                length_retry_max_tokens=_PREFERENCE_LENGTH_RETRY_MAX_TOKENS,
                operation="preference_single",
            )
        except AiAgentError as error:
            return PreferenceFailure(user_id=item.user_id, error=error.code)

    async def interpret_request(
        self, item: RequestContext, *, timeout_s: float
    ) -> RequestInterpretation:
        deadline = _deadline(timeout_s)
        return await self._completions.run(
            request_messages(item), lambda text: validate_interpretation(text, item),
            deadline=deadline, max_tokens=1500,
            operation="interpret_request",
        )


def _deadline(timeout_s: float) -> float:
    if not isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be finite and positive")
    return asyncio.get_running_loop().time() + timeout_s


def _preference_batches(
    items: Sequence[ProfileContext], batch_size: int, deadline: float
) -> Iterator[list[PreferenceInput]]:
    clock = asyncio.get_running_loop().time
    batch: list[PreferenceInput] = []
    batch_chars = 0
    for profile_number, item in enumerate(items, 1):
        user_id = cast(str, item.user["user_id"])
        expired = clock() >= deadline
        data = PreferenceInput(item, user_id, profile_number=profile_number, include_history=not expired)
        size = len(compact_json(data.payload())) if not expired else 0
        if batch and (len(batch) >= batch_size or batch_chars + size > 32000):
            yield batch
            batch, batch_chars = [], 0
        batch.append(data)
        batch_chars += size
    if batch:
        yield batch
