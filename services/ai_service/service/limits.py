import asyncio
import logging
from collections.abc import Callable
from typing import TypeVar

from concierge_service.ports.ai import AiAgentError

from ai_service.errors import InvalidModelOutput
from ai_service.infra.llm_client import LlmClient, LlmClientError
from ai_service.prompts import load_prompt
from run_telemetry import log_event


Result = TypeVar("Result")
_RETRY_INSTRUCTION = load_prompt("retry_invalid.md")
_DEFAULT_RATE_LIMIT_BACKOFF_S = 30.0
logger = logging.getLogger(__name__)


class LimitedCompletions:
    def __init__(self, client: LlmClient, *, requests_per_minute: int, concurrency: int):
        if requests_per_minute < 1 or concurrency < 1:
            raise ValueError("LLM limits must be positive")
        self._client = client
        self._interval = 60 / requests_per_minute
        self._slots = asyncio.Semaphore(concurrency)
        self._next_start = 0.0
        self.attempts = 0
        self.total_tokens = 0
        self.responses_with_usage = 0

    async def run(
        self,
        messages: list[dict[str, str]],
        parse: Callable[[str], Result],
        *,
        deadline: float,
        max_tokens: int,
        operation: str,
        retry_invalid: bool = True,
        length_retry_max_tokens: int | None = None,
    ) -> Result:
        clock = asyncio.get_running_loop().time
        retry_rate_limit = True
        attempt = 0
        try:
            async with asyncio.timeout_at(deadline):
                while True:
                    async with self._slots:
                        while (delay := self._next_start - clock()) > 0:
                            await asyncio.sleep(delay)
                        remaining = deadline - clock()
                        if remaining <= 0:
                            raise TimeoutError
                        self._next_start = clock() + self._interval
                        self.attempts += 1
                        attempt += 1
                        attempt_max_tokens = max_tokens
                        started = clock()
                        outcome = "interrupted"
                        tokens = None
                        reason = finish_reason = http_status = None
                        rate_limit_delay = None
                        try:
                            completion = await self._client.complete(
                                messages, max_tokens=attempt_max_tokens, timeout_s=remaining
                            )
                            usage = (completion.usage or {}).get("total_tokens")
                            if type(usage) is int and usage >= 0:
                                tokens = usage
                            finish_reason = "stop"
                            result = parse(completion.text)
                            if clock() >= deadline:
                                raise TimeoutError
                            outcome = "ok"
                            return result
                        except AiAgentError as error:
                            outcome = error.code
                            if isinstance(error, LlmClientError):
                                reason = error.reason
                                finish_reason = error.finish_reason
                                http_status = error.http_status
                                tokens = error.total_tokens
                            elif isinstance(error, InvalidModelOutput):
                                reason = error.reason
                            if error.code == "rate_limited":
                                delay = (
                                    error.retry_after_s if isinstance(error, LlmClientError) else None
                                )
                                rate_limit_delay = _DEFAULT_RATE_LIMIT_BACKOFF_S if delay is None else delay
                                self._next_start = max(self._next_start, clock() + rate_limit_delay)
                                if retry_rate_limit and self._next_start < deadline:
                                    retry_rate_limit = False
                                    continue
                            elif error.code == "invalid_response" and retry_invalid:
                                retry_invalid = False
                                if finish_reason == "length" and length_retry_max_tokens is not None:
                                    max_tokens = length_retry_max_tokens
                                else:
                                    messages = [
                                        {
                                            "role": "system",
                                            "content": f"{messages[0]['content']}\n{_RETRY_INSTRUCTION}",
                                        },
                                        *messages[1:],
                                    ]
                                continue
                            raise
                        finally:
                            if tokens is not None:
                                self.total_tokens += tokens
                                self.responses_with_usage += 1
                            log_event(
                                logger, "llm_attempt", operation=operation, attempt=attempt,
                                outcome=outcome, total_tokens=tokens,
                                reason=reason, finish_reason=finish_reason, http_status=http_status,
                                rate_limit_delay_s=rate_limit_delay,
                                max_tokens=attempt_max_tokens,
                                duration_ms=round((clock() - started) * 1000),
                            )
        except TimeoutError:
            raise AiAgentError("timeout") from None
