import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ai_service.evaluate_dev import evaluate
from ai_service.service.agent import LlmAgent


DATASET_FILE = Path(__file__).resolve().parents[1] / "docs" / "data" / "dataset.json"
MODEL_REPLIES = {
    "rq_06f827d9af": ("warmer", {"mode": "relative", "amount": None}),
    "rq_aec3456954": ("out_of_scope", {}),
    "rq_6c05184bc3": ("brighter", {"mode": "relative", "amount": None}),
    "rq_af421a5f94": ("do_not_disturb", {"enabled": True}),
    "rq_85f7a82006": ("like_yesterday", {}),
    "rq_88fc0118f4": ("quieter", {"mode": "relative", "amount": None}),
    "rq_4d1cf94f83": ("alarm_set", {"target_at": "2026-11-13T09:00:00+03:00"}),
    "rq_3727d7acea": ("alarm_cancel", {}),
    "rq_d5e18fe9a0": ("curtains", {"position": 0}),
}


def preference_reply(profile):
    user = profile["user"]
    return {
        "user_id": user["user_id"],
        "temperature": None,
        "light_sensitive": None,
        "favorite_track_ids": None,
        "gentle_alarm": None,
        "weekday_bedtime": None,
        "weekday_wake_time": None,
        "home_timezone": {
            "value": user["timezone"],
            "sources": [{"kind": "profile", "source_id": user["user_id"], "pointer": "/timezone"}],
        },
        "settings": [],
    }


class AiOfficialDatasetTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_dev_bundle_drives_agent_without_hack_api(self):
        calls = []
        users_processed = set()
        preference_calls = []

        def respond(request):
            self.assertEqual(str(request.url), "https://llm.example/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            messages = json.loads(request.content)["messages"]
            data = json.loads(messages[1]["content"])
            if isinstance(data, list) or "request" not in data:
                profiles = data if isinstance(data, list) else [data]
                for profile in profiles:
                    self.assert_profile_scope(profile)
                    user_id = profile["user"]["user_id"]
                    self.assertNotIn(user_id, users_processed)
                    users_processed.add(user_id)
                preference_calls.append(len(profiles))
                replies = [preference_reply(profile) for profile in reversed(profiles)]
                payload = replies if isinstance(data, list) else replies[0]
            else:
                record = data["request"]
                record_id = record["record_id"]
                self.assert_profile_scope(data, user_key="request")
                self.assertNotIn("reference", data)
                self.assertIn(record["user_id"], users_processed)
                self.assertNotIn("preference_hints", data)
                if record_id == "rq_06f827d9af":
                    self.assertEqual(len(record["utterances"]), 2)
                    self.assertNotIn("conversation", data)
                elif record_id == "rq_85f7a82006":
                    self.assertTrue(data["events"])
                elif record_id == "rq_d5e18fe9a0":
                    self.assertNotIn("conversation", data)
                calls.append(record_id)
                intent, slots = MODEL_REPLIES[record_id]
                payload = {"record_id": record_id, "intent": intent, "slots": slots}
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}],
                "usage": {"total_tokens": 10},
            })

        env = {
            "LLM_BASE_URL": "https://llm.example/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "test-model",
        }
        with patch.dict("os.environ", env, clear=True), patch(
            "ai_service.evaluate_dev.LlmAgent",
            side_effect=lambda client: LlmAgent(client, requests_per_minute=60000),
        ):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
                report = await evaluate(
                    http, max_users=2, max_requests=9, dataset_file=DATASET_FILE
                )

        self.assertEqual(calls, list(MODEL_REPLIES))
        self.assertEqual(report["preference_successes"], 2)
        self.assertEqual(report["requests"], 9)
        self.assertEqual(report["intent_matches"], 9)
        self.assertEqual(report["request_errors"], {})
        self.assertEqual(report["attempts"], len(preference_calls) + len(calls))

    def assert_profile_scope(self, profile, *, user_key="user"):
        self.assertLessEqual(
            {entry["user_id"] for key in ("events", "chat_history") for entry in profile.get(key, [])},
            {profile[user_key]["user_id"]},
        )
