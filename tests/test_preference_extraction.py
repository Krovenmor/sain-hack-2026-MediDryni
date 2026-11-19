import asyncio
import json
import unittest
from dataclasses import replace

import httpx

from ai_service.infra.llm_client import LlmClient
from ai_service.service.agent import LlmAgent
from ai_service.service.context import compact_json
from ai_service.service.preferences import PreferenceInput
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

        with self.assertLogs("ai_service.service.preferences", level="DEBUG") as logs:
            result = await self.make_agent(handle).extract_preference_hints([profile()], timeout_s=1)
        event, = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual(event["event_ids"], ["note"])
        self.assertEqual(event["message_ids"], ["chat"])
        self.assertEqual(event["profile_number"], 1)
        self.assertNotIn("Люблю", "\n".join(logs.output))
        self.assertNotIn("Private", "\n".join(logs.output))
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

    def test_short_chat_keeps_the_guests_own_messages_in_order(self):
        first = {"user_id": "guest", "message_id": "first", "text": "Доброе утро."}
        second = {"user_id": "guest", "message_id": "second", "text": "От резкого звонка вскакиваю."}
        foreign = first | {"user_id": "other", "message_id": "foreign"}
        item = replace(profile(), chat_history=[first, foreign, second])
        prepared = PreferenceInput(item, "guest", profile_number=1)
        self.assertEqual(prepared.payload()["chat_history"], [first, second])
        prepared.validate(PreferenceHints.model_validate(hints(
            gentle_alarm={"value": True, "sources": [source("message", "second", "/text")]},
        )))
        expired = PreferenceInput(item, "guest", profile_number=1, include_history=False)
        self.assertEqual(expired.payload()["chat_history"], [])

    def test_chat_over_either_limit_still_uses_evidence_and_its_neighbors(self):
        cue = {"user_id": "guest", "message_id": "cue", "text": "Люблю шум дождя."}
        filler = {"user_id": "guest", "message_id": "neutral", "text": "Спасибо."}
        later = [filler | {"message_id": str(i)} for i in range(12)]
        oversized = filler | {"message_id": "oversized", "text": "Спасибо. " * 260}
        item = replace(profile(), chat_history=[cue, filler, *later, oversized])
        prepared = PreferenceInput(item, "guest", profile_number=1)
        selected = prepared.payload()["chat_history"]
        self.assertEqual(selected, [cue, filler, *later[-10:]])
        self.assertLessEqual(len(compact_json(selected)), 2100)

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

    async def test_length_retries_single_profile_once_with_more_tokens_and_same_messages(self):
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "length", "message": {"content": "truncated"}}],
            })

        with self.assertLogs("ai_service.service.limits", level="INFO") as logs:
            result = await self.make_agent(handle).extract_preference_hints([profile()], timeout_s=1)

        self.assertEqual(result, [PreferenceFailure(user_id="guest", error="invalid_response")])
        self.assertEqual([body["max_tokens"] for body in requests], [10_000, 16_000])
        self.assertEqual(requests[0]["messages"], requests[1]["messages"])
        events = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual([event["max_tokens"] for event in events], [10_000, 16_000])

    async def test_schema_error_retries_single_profile_with_original_limit_and_correction(self):
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return completion(
                {"user_id": "guest", "private-field": "private-value"} if len(requests) == 1 else hints()
            )

        with self.assertLogs("ai_service.errors", level="DEBUG") as logs:
            result = await self.make_agent(handle).extract_preference_hints([profile()], timeout_s=1)

        self.assertEqual(result, [PreferenceHints.model_validate(hints())])
        issue, = [entry for entry in json.loads(logs.records[0].getMessage())["issues"]
                  if entry["type"] == "extra_forbidden"]
        self.assertEqual(issue["path"], ["<variant>"])
        self.assertNotIn("private-", "\n".join(logs.output))
        self.assertEqual([body["max_tokens"] for body in requests], [10_000, 10_000])
        first, second = [body["messages"] for body in requests]
        self.assertEqual(first[1:], second[1:])
        self.assertIn("Предыдущий результат не прошёл проверку", second[0]["content"])

    async def test_retries_only_invalid_member_of_preference_batch(self):
        calls = []
        budgets = []

        def handle(request):
            body = json.loads(request.content)
            budgets.append(body["max_tokens"])
            payload = json.loads(body["messages"][1]["content"])
            if isinstance(payload, list):
                calls.append([entry["user"]["user_id"] for entry in payload])
                return completion([hints("second"), {"user_id": "first"}])
            calls.append(payload["user"]["user_id"])
            return completion({"user_id": "first"})

        agent = self.make_agent(handle, batch_size=3)
        with self.assertLogs("ai_service", level="INFO") as logs:
            results = await agent.extract_preference_hints(
                [profile("first"), profile("second")], timeout_s=1
            )
        events = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual(events[0]["rejected"], {"preference_schema": 1})
        self.assertEqual(events[-1]["reason"], "preference_schema")
        self.assertEqual(calls, [["first", "second"], "first"])
        self.assertEqual(budgets, [20_000, 10_000])
        self.assertEqual(results, [
            PreferenceFailure(user_id="first", error="invalid_response"),
            PreferenceHints.model_validate(hints("second")),
        ])

    async def test_length_fallback_for_three_profiles_uses_full_single_profile_budget(self):
        budgets = []
        user_ids = ["first", "second", "third"]

        def handle(request):
            body = json.loads(request.content)
            budgets.append(body["max_tokens"])
            payload = json.loads(body["messages"][1]["content"])
            if isinstance(payload, list):
                self.assertEqual([item["user"]["user_id"] for item in payload], user_ids)
                return httpx.Response(200, json={
                    "choices": [{"finish_reason": "length", "message": {"content": "truncated"}}],
                })
            return completion(hints(payload["user"]["user_id"]))

        agent = self.make_agent(handle, batch_size=3)
        results = await agent.extract_preference_hints(
            [profile(user_id) for user_id in user_ids], timeout_s=1
        )
        self.assertEqual(budgets, [30_000, 10_000, 10_000, 10_000])
        self.assertEqual(results, [PreferenceHints.model_validate(hints(user_id)) for user_id in user_ids])

    async def test_http_size_errors_split_a_batch_but_other_http_errors_end_it(self):
        for status, error, split in (
            (413, {}, True),
            (400, {"code": "context_length_exceeded"}, True),
            (400, {"message": "max_tokens must be less than 16385"}, True),
            (400, {"message": "unsupported parameter"}, False),
            (401, {}, False),
            (503, {}, False),
        ):
            with self.subTest(status=status, error=error):
                budgets = []

                def handle(request):
                    body = json.loads(request.content)
                    budgets.append(body["max_tokens"])
                    payload = json.loads(body["messages"][1]["content"])
                    if isinstance(payload, list):
                        return httpx.Response(status, json={"error": error})
                    return completion(hints(payload["user"]["user_id"]))

                users = ["first", "second"]
                agent = self.make_agent(handle, batch_size=2)
                results = await agent.extract_preference_hints([profile(u) for u in users], timeout_s=1)
                self.assertEqual(budgets, [20_000, 10_000, 10_000] if split else [20_000])
                self.assertEqual(results, [
                    PreferenceHints.model_validate(hints(u)) if split
                    else PreferenceFailure(user_id=u, error="unavailable") for u in users
                ])
