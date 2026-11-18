import asyncio
import json
import unittest
from dataclasses import replace

import httpx

from ai_service.infra.llm_client import LlmClient
from ai_service.service.agent import LlmAgent
from concierge_service.ports.ai import PreferenceFailure, PreferenceHints, ProfileContext


def profile(user_id="guest"):
    return ProfileContext(
        user={
            "user_id": user_id,
            "timezone": "Europe/Moscow",
            "sleep_preferences": {"dislikes": ["Резкий будильник"]},
        },
        events=[
            {
                "user_id": user_id, "event_id": "note",
                "payload": {"text": "Люблю прохладу. В прошлом заезде ставил 18 °C."},
            },
            {"user_id": "other", "event_id": "foreign", "payload": {"text": "Private"}},
        ],
        chat_history=[{"user_id": user_id, "message_id": "chat", "text": "Люблю дождь"}],
        devices=[{"device_id": "climate", "fields": [{"field_id": "target"}]}],
        tracks=[{"track_id": "rain", "title": "Дождь"}],
    )


def hints(user_id="guest", **values):
    return {
        "user_id": user_id, "temperature": None, "light_sensitive": None,
        "favorite_track_ids": None, "gentle_alarm": None, "weekday_bedtime": None,
        "weekday_wake_time": None, "home_timezone": None, "settings": [],
    } | values


def source(kind="event", source_id="note", pointer="/payload/text"):
    return {"kind": kind, "source_id": source_id, "pointer": pointer}


def completion(payload):
    return httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}],
    })


class PreferenceExtractionTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self, handler, *, batch_size=1):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        return LlmAgent(LlmClient(
            http, base_url="https://proxy.example/v1", api_key="test", model="test-model"
        ), requests_per_minute=60000, preference_batch_size=batch_size)

    async def test_extracts_hints_and_only_sends_one_guests_history(self):
        note = source()
        payload = hints(
            temperature={"value": "cool", "sources": [note]},
            favorite_track_ids={"value": ["rain"], "sources": [source("message", "chat", "/text")]},
            gentle_alarm={
                "value": True,
                "sources": [source("profile", "guest", "/sleep_preferences/dislikes/0")],
            },
            home_timezone={
                "value": "Europe/Moscow",
                "sources": [source("profile", "guest", "/timezone")],
            },
            settings=[{
                "context": "past_stay", "observed_at": None, "device_id": "climate",
                "field_id": "target", "value": 18, "sources": [note],
            }],
        )

        def handle(request):
            messages = json.loads(request.content)["messages"]
            self.assertEqual([message["role"] for message in messages], ["system", "user"])
            guest_input = json.loads(messages[1]["content"])
            self.assertEqual(guest_input["user"]["user_id"], "guest")
            self.assertEqual([event["event_id"] for event in guest_input["events"]], ["note"])
            self.assertEqual([message["message_id"] for message in guest_input["chat_history"]], ["chat"])
            return completion(payload)

        result = await self.make_agent(handle).extract_preference_hints([profile()], timeout_s=1)
        self.assertEqual(result, [PreferenceHints.model_validate(payload)])

    async def test_invalid_sources_and_catalog_references_fail(self):
        note = source()
        invalid_payloads = [
            hints("other"),
            hints(temperature={"value": "cool", "sources": [source(source_id="foreign")]}),
            hints(temperature={"value": "cool", "sources": [source(pointer="/payload/missing")]}),
            hints(temperature={"value": "cool", "sources": [source(pointer="/payload/~2text")]}),
            hints(favorite_track_ids={"value": ["unknown"], "sources": [note]}),
            hints(favorite_track_ids={"value": ["rain", "rain"], "sources": [note]}),
            hints(home_timezone={"value": "Fake/Timezone", "sources": [note]}),
            hints(settings=[{
                "context": "home", "observed_at": None, "device_id": "climate",
                "field_id": "unknown", "value": 18, "sources": [note],
            }]),
            {"user_id": "guest"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                agent = self.make_agent(lambda request: completion(payload))
                result = await agent.extract_preference_hints([profile()], timeout_s=1)
                self.assertEqual(result, [PreferenceFailure(user_id="guest", error="invalid_response")])

    async def test_filters_unrelated_entries_without_changing_evidence_pointers(self):
        item = profile()
        unrelated = {"user_id": "guest", "event_id": "bread", "payload": {"text": "Купил хлеб."}}
        link_only = {"user_id": "guest", "event_id": "link", "payload": {"message_ids": ["chat"]}}
        item = replace(
            item,
            events=[*item.events, unrelated, link_only],
            chat_history=[*item.chat_history, {"user_id": "guest", "message_id": "irrelevant",
                                              "text": "Купил сумку."}],
        )
        payload = hints(temperature={"value": "cool", "sources": [source()]})

        def handle(request):
            messages = json.loads(request.content)["messages"]
            guest_input = json.loads(messages[1]["content"])
            self.assertNotIn("$defs", messages[0]["content"])
            self.assertEqual(guest_input["events"], [item.events[0]])
            return completion(payload)

        result = await self.make_agent(handle).extract_preference_hints([item], timeout_s=1)
        self.assertEqual(result, [PreferenceHints.model_validate(payload)])

    async def test_individual_failure_preserves_input_order_and_empty_hints(self):
        calls = []

        def handle(request):
            messages = json.loads(request.content)["messages"]
            user_id = json.loads(messages[1]["content"])["user"]["user_id"]
            calls.append(user_id)
            if user_id == "first":
                return httpx.Response(503)
            return completion(hints(user_id))

        agent = self.make_agent(handle)
        self.assertEqual(await agent.extract_preference_hints([], timeout_s=1), [])
        results = await agent.extract_preference_hints(
            [profile("first"), profile("second")], timeout_s=1
        )
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(results, [
            PreferenceFailure(user_id="first", error="unavailable"),
            PreferenceHints.model_validate(hints("second")),
        ])

    async def test_budget_is_shared_across_users(self):
        calls = 0

        async def handle(request):
            nonlocal calls
            calls += 1
            await asyncio.Event().wait()

        results = await self.make_agent(handle).extract_preference_hints(
            [profile("first"), profile("second")], timeout_s=0.05
        )
        self.assertEqual(calls, 1)
        self.assertEqual(results, [
            PreferenceFailure(user_id=user_id, error="timeout")
            for user_id in ("first", "second")
        ])

    async def test_retries_only_invalid_member_of_preference_batch(self):
        calls = []

        def handle(request):
            payload = json.loads(json.loads(request.content)["messages"][1]["content"])
            if isinstance(payload, list):
                calls.append([entry["user"]["user_id"] for entry in payload])
                return completion([hints("second"), {"user_id": "first"}])
            calls.append(payload["user"]["user_id"])
            return completion({"user_id": "first"})

        agent = self.make_agent(handle, batch_size=3)
        results = await agent.extract_preference_hints(
            [profile("first"), profile("second")], timeout_s=1
        )
        self.assertEqual(calls, [["first", "second"], "first"])
        self.assertEqual(results, [
            PreferenceFailure(user_id="first", error="invalid_response"),
            PreferenceHints.model_validate(hints("second")),
        ])
