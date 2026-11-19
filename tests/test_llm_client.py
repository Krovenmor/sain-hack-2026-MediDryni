import asyncio
import json
import traceback
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch

import httpx

from ai_service.cmd.check_proxy import check
from ai_service.infra.llm_client import LlmClient, LlmClientError


MESSAGES = [{"role": "user", "content": "private prompt"}]


class LlmClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_check_uses_env_and_reports_only_token_counts(self):
        def handle(request):
            self.assertEqual(str(request.url), "https://proxy.example/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            self.assertEqual(json.loads(request.content)["model"], "test-model")
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": "ОК"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 400, "total_tokens": 410},
            })

        with patch.dict("os.environ", {
            "LLM_BASE_URL": "https://proxy.example/v1",
            "LLM_API_KEY": "test-key\n",
            "LLM_MODEL": "test-model",
        }):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                result = await check(client)
        self.assertEqual(result, {
            "status": "ok", "prompt_tokens": 10, "completion_tokens": 400, "total_tokens": 410,
        })

    def make_client(self, handler):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        return LlmClient(
            http, base_url="https://proxy.example/v1/", api_key="test-key", model="test-model"
        )

    async def test_sends_single_completion_and_returns_text_and_usage(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": " {} "}}],
                "usage": {"total_tokens": 120},
            })

        result = await self.make_client(handle).complete(
            MESSAGES, max_tokens=1200, timeout_s=1
        )
        self.assertEqual(result.text, " {} ")
        self.assertEqual(result.usage, {"total_tokens": 120})
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "https://proxy.example/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        self.assertEqual(json.loads(request.content), {
            "model": "test-model", "messages": MESSAGES, "max_tokens": 1200
        })

    async def test_missing_usage_is_allowed(self):
        client = self.make_client(lambda request: httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
        }))
        result = await client.complete(MESSAGES, max_tokens=1200, timeout_s=1)
        self.assertIsNone(result.usage)

    async def test_rate_limit_exposes_retry_after_without_retrying(self):
        for header in ("5", format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30)),
                       "private header"):
            with self.subTest(header=header):
                requests = []

                def handle(request):
                    requests.append(request)
                    return httpx.Response(429, headers={"Retry-After": header})

                with self.assertRaises(LlmClientError) as error:
                    await self.make_client(handle).complete(
                        MESSAGES, max_tokens=1200, timeout_s=1
                    )
                self.assertEqual(error.exception.code, "rate_limited")
                if header == "5":
                    self.assertEqual(error.exception.retry_after_s, 5)
                elif header == "private header":
                    self.assertIsNone(error.exception.retry_after_s)
                else:
                    self.assertLessEqual(error.exception.retry_after_s, 30)
                    self.assertGreater(error.exception.retry_after_s, 0)
                self.assertEqual(len(requests), 1)

    async def test_rejects_empty_truncated_and_malformed_responses(self):
        responses = [
            httpx.Response(200, content=b"private non-JSON body"),
            httpx.Response(200, json={"choices": []}),
            httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": "   "}}],
            }),
            httpx.Response(200, json={
                "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
            }),
        ]
        for response in responses:
            with self.subTest(response=response):
                with self.assertRaises(LlmClientError) as error:
                    await self.make_client(lambda request: response).complete(
                        MESSAGES, max_tokens=1200, timeout_s=1
                    )
                self.assertEqual(error.exception.code, "invalid_response")

    async def test_transport_and_http_errors_do_not_expose_sensitive_data(self):
        for failure, expected in (
            (httpx.ConnectError("private prompt and test-key"), "unavailable"),
            (httpx.ReadTimeout("private prompt and test-key"), "timeout"),
            (httpx.Response(503, text="private prompt and test-key"), "unavailable"),
        ):
            with self.subTest(action=type(failure).__name__):
                def handle(request):
                    if isinstance(failure, Exception):
                        raise failure
                    return failure

                client = self.make_client(handle)
                with self.assertRaises(LlmClientError):
                    try:
                        await client.complete(MESSAGES, max_tokens=1200, timeout_s=1)
                    except LlmClientError as error:
                        self.assertEqual(error.code, expected)
                        formatted = traceback.format_exc()
                        self.assertNotIn("private prompt", formatted)
                        self.assertNotIn("test-key", formatted)
                        raise

    async def test_total_timeout_cancels_in_flight_request(self):
        stopped = asyncio.Event()

        async def handle(request):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        client = self.make_client(handle)
        with self.assertRaises(LlmClientError) as error:
            await client.complete(MESSAGES, max_tokens=1200, timeout_s=0.05)
        self.assertEqual(error.exception.code, "timeout")
        self.assertTrue(stopped.is_set())

    async def test_caller_cancellation_propagates(self):
        started = asyncio.Event()

        async def handle(request):
            started.set()
            await asyncio.Event().wait()

        client = self.make_client(handle)
        task = asyncio.create_task(client.complete(MESSAGES, max_tokens=1200, timeout_s=1))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
