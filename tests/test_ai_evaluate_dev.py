import json
import unittest
from unittest.mock import patch

import httpx

from ai_service.evaluate_dev import evaluate
from ai_service.service.agent import LlmAgent


class AiEvaluateDevTests(unittest.IsolatedAsyncioTestCase):
    async def test_scores_mocked_dev_run_without_sending_reference_to_model(self):
        at = "2026-11-18T20:00:00+03:00"
        dataset = {
            "meta": {"phase": "dev"},
            "users": [{"user_id": "guest"}],
            "events": [],
            "chat_history": [],
            "devices": [],
            "tracks": [],
            "intents": [{"intent_id": "warmer", "description": "Теплее"}],
            "stays": [{"record_id": "stay", "hotel_timezone": "Europe/Moscow"}],
            "requests": [{
                "record_id": "request", "user_id": "guest", "stay_id": "stay",
                "at": at, "initial_state": {},
                "utterances": [{"role": "user", "at": at, "text": "Теплее"}],
            }],
        }
        preferences = dict.fromkeys((
            "temperature", "light_sensitive", "favorite_track_ids", "gentle_alarm",
            "weekday_bedtime", "weekday_wake_time", "home_timezone",
        )) | {"user_id": "guest", "settings": []}
        calls = []

        def respond(request):
            calls.append(request.url.path)
            if request.url.host == "hack.example":
                self.assertEqual(request.headers["Authorization"], "Bearer hack-secret")
                return httpx.Response(200, json={
                    "dataset": dataset,
                    "reference": {"requests": [{
                        "record_id": "request", "intent_id": "warmer",
                        "rationale": "reference-only-marker",
                    }]},
                })
            self.assertEqual(request.url.host, "llm.example")
            self.assertEqual(request.headers["Authorization"], "Bearer llm-secret")
            self.assertNotIn(b"reference-only-marker", request.content)
            self.assertNotIn(b"hack-secret", request.content)
            payload = preferences if len(calls) == 2 else {
                "record_id": "request", "intent": "warmer",
                "slots": {"mode": "relative", "amount": None},
            }
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}],
                "usage": {"total_tokens": 10},
            })

        env = {
            "HACK_API_URL": "https://hack.example",
            "HACK_TEAM_TOKEN": "hack-secret",
            "LLM_BASE_URL": "https://llm.example/v1",
            "LLM_API_KEY": "llm-secret",
            "LLM_MODEL": "model",
        }
        with patch.dict("os.environ", env), patch(
            "ai_service.evaluate_dev.LlmAgent",
            side_effect=lambda client: LlmAgent(client, requests_per_minute=60000),
        ):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
                result = await evaluate(http, max_users=1, max_requests=1)

        self.assertEqual(calls, ["/case/2/getData", "/v1/chat/completions", "/v1/chat/completions"])
        self.assertEqual(result["preference_successes"], 1)
        self.assertEqual(result["intent_matches"], 1)
        self.assertEqual(result["total_tokens"], 20)
