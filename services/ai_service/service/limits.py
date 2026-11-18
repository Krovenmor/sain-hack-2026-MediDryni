import asyncio
from collections.abc import Callable
from typing import TypeVar

from concierge_service.ports.ai import AiAgentError

from ai_service.infra.llm_client import LlmClient, LlmClientError
from ai_service.prompts import load_prompt


Result = TypeVar("Result")
_RETRY_INSTRUCTION = load_prompt("retry_invalid.md")


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
        retry_invalid: bool = True,
    ) -> Result:
        clock = asyncio.get_running_loop().time
        retry_rate_limit = True
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
                        try:
                            completion = await self._client.complete(
                                messages, max_tokens=max_tokens, timeout_s=remaining
                            )
                            usage = (completion.usage or {}).get("total_tokens")
                            if type(usage) is int and usage >= 0:
                                self.total_tokens += usage
                                self.responses_with_usage += 1
                            result = parse(completion.text)
                            if clock() >= deadline:
                                raise TimeoutError
                            return result
                        except AiAgentError as error:
                            if error.code == "rate_limited":
                                delay = (
                                    error.retry_after_s if isinstance(error, LlmClientError) else None
                                )
                                self._next_start = max(
                                    self._next_start, clock() + (2 if delay is None else delay)
                                )
                                if retry_rate_limit and self._next_start < deadline:
                                    retry_rate_limit = False
                                    continue
                            elif error.code == "invalid_response" and retry_invalid:
                                retry_invalid = False
                                messages = [
                                    {
                                        "role": "system",
                                        "content": f"{messages[0]['content']}\n{_RETRY_INSTRUCTION}",
                                    },
                                    *messages[1:],
                                ]
                                continue
                            raise
        except TimeoutError:
            raise AiAgentError("timeout") from None
