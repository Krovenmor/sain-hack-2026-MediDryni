import json
import unittest
from dataclasses import replace
from typing import get_args

import httpx

from ai_service.infra.llm_client import LlmClient
from ai_service.service.agent import LlmAgent
from concierge_service.ports.ai import AiAgentError, DialogueMessage, Intent, ProfileContext, RequestContext


AT = "2026-11-20T22:00:00+03:00"


def request_context(*utterances):
    return RequestContext(
        profile=ProfileContext(
            user={"user_id": "guest"},
            events=[],
            chat_history=[],
            devices=[{
                "device_id": "climate",
                "fields": [{
                    "field_id": "target",
                    "value_schema": {"type": "number", "minimum": 16, "maximum": 26},
                }],
            }],
            tracks=[{"track_id": "rain", "title": "Дождь"}],
        ),
        request={
            "record_id": "request_1",
            "at": AT,
            "initial_state": {"climate": {"target": 20}},
            "utterances": [
                {"role": "user", "at": AT, "text": text} for text in utterances
            ],
        },
        stay={"hotel_timezone": "Europe/Moscow"},
        intents=[{"intent_id": "warmer", "description": "Повысить температуру"}],
        conversation=[],
        preference_hints=None,
    )


def completion(text):
    return httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": text}}],
    })


class RequestInterpretationTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self, handler, *, requests_per_minute=60000):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        return LlmAgent(LlmClient(
            http, base_url="https://proxy.example/v1", api_key="test", model="test-model"
        ), requests_per_minute=requests_per_minute)

    async def test_passes_current_request_and_catalog_in_one_call(self):
        item = request_context("Сделай теплее", "Совсем немного")
        old = DialogueMessage(role="user", at=AT, text="Включи будильник")
        device = item.profile.devices[0]
        field = device["fields"][0]
        item = replace(item, conversation=[old], profile=replace(
            item.profile,
            devices=[device | {"fields": [field | {"default_value": 20, "description": "Лишнее"}]}],
        ))
        calls = []

        def handle(request):
            calls.append(request)
            messages = json.loads(request.content)["messages"]
            self.assertEqual([message["role"] for message in messages], ["system", "user"])
            self.assertNotIn("$defs", messages[0]["content"])
            for intent in get_args(Intent):
                self.assertIn(intent, messages[0]["content"])
            self.assertEqual(json.loads(messages[1]["content"]), {
                "request": item.request,
                "hotel_timezone": "Europe/Moscow",
                "devices": [device],
                "tracks": item.profile.tracks,
                "conversation": [old.model_dump()],
            })
            return completion(json.dumps({
                "record_id": "request_1", "intent": "warmer",
                "slots": {"mode": "relative", "amount": None},
            }))

        result = await self.make_agent(handle).interpret_request(item, timeout_s=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.intent, "warmer")
        self.assertIsNone(result.slots.amount)

    async def test_preserves_valid_slots_and_special_intents(self):
        for utterance, intent, slots in (
            ("Свет на 20 процентов", "dimmer", {"mode": "absolute", "value": 20}),
            ("Ярче", "brighter", {"mode": "relative", "amount": None}),
            ("Прохладнее на 2 градуса", "cooler", {"mode": "relative", "amount": 2}),
            ("Теплее на градус", "warmer", {"mode": "relative", "amount": 1}),
            ("Включи дождь", "sound_on", {"track_id": "rain", "volume": None, "timer_minutes": None}),
            ("На пять процентов тише", "quieter", {"mode": "relative", "amount": 5}),
            ("Закрой шторы", "curtains", {"position": 0}),
            ("Разбуди в семь", "alarm_set", {"target_at": "2026-11-21T07:00:00+03:00"}),
            ("Сдвинь будильник на 15 минут раньше", "alarm_shift", {"mode": "relative", "minutes": -15}),
            ("Отмени будильник", "alarm_cancel", {}),
            ("Выключи всё", "all_off", {}),
            ("Я ложусь", "going_to_bed", {}),
            ("Я проснулся", "woke_up", {}),
            ("Сделай как дома", "like_home", {}),
            ("Сделай как вчера", "like_yesterday", {}),
            ("Сделай как три дня назад", "like_yesterday", {"days_ago": 3}),
            ("Не беспокоить", "do_not_disturb", {"enabled": True}),
            ("Поставь 35 градусов", "invalid_value",
             {"subject": "температура", "requested_value": 35, "reason": "out_of_range"}),
            ("Что выпить, чтобы уснуть?", "medical", {}),
            ("Вызови такси", "out_of_scope", {}),
        ):
            with self.subTest(intent=intent):
                payload = {"record_id": "request_1", "intent": intent, "slots": slots}
                agent = self.make_agent(lambda request: completion(json.dumps(payload)))
                result = await agent.interpret_request(request_context(utterance), timeout_s=1)
                self.assertEqual(result.record_id, "request_1")
                self.assertEqual(result.intent, intent)
                self.assertEqual(result.slots.model_dump(exclude_unset=True), slots)

    async def test_rejects_bad_model_output_with_safe_error(self):
        valid = {
            "record_id": "request_1", "intent": "warmer",
            "slots": {"mode": "relative", "amount": 2},
        }
        for output in (
            "not JSON",
            json.dumps(valid | {"record_id": "other_request"}),
            json.dumps(valid | {"slots": {"mode": "relative", "amount": "2"}}),
            json.dumps(valid | {"response_class": "done"}),
            json.dumps(valid | {
                "intent": "sound_on",
                "slots": {"track_id": "unknown", "volume": None, "timer_minutes": None},
            }),
        ):
            with self.subTest(output=output):
                agent = self.make_agent(lambda request: completion(output))
                with self.assertRaisesRegex(AiAgentError, "^invalid_response$"):
                    await agent.interpret_request(request_context("Теплее"), timeout_s=1)

    async def test_retry_and_dialogue_reference(self):
        item = replace(
            request_context("Передвинь его на полчаса позже"),
            conversation=[
                DialogueMessage(role="user", at=AT, text="Поставь будильник на семь"),
                DialogueMessage(role="agent", at=AT, text="Поставил будильник на 07:00."),
            ],
        )
        calls = []

        def handle(request):
            data = json.loads(json.loads(request.content)["messages"][1]["content"])
            calls.append(data)
            if len(calls) == 1:
                return completion("invalid")
            response = completion(json.dumps({
                "record_id": "request_1", "intent": "alarm_shift",
                "slots": {"mode": "relative", "minutes": 30},
            }))
            return httpx.Response(200, json=response.json() | {"usage": {"total_tokens": 77}})

        with self.assertLogs("ai_service.service.limits", level="INFO") as logs:
            result = await self.make_agent(handle).interpret_request(item, timeout_s=1)
        events = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual([item["operation"] for item in events], ["interpret_request"] * 2)
        self.assertEqual([item["outcome"] for item in events], ["invalid_response", "ok"])
        self.assertEqual([item["reason"] for item in events], ["model_json", None])
        self.assertEqual([item["total_tokens"] for item in events], [None, 77])
        self.assertNotIn("Передвинь его", "\n".join(logs.output))
        self.assertEqual(result.slots.minutes, 30)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["conversation"], [entry.model_dump() for entry in item.conversation])
        self.assertEqual(calls[0]["request"]["initial_state"], item.request["initial_state"])

    async def test_rejected_proxy_completion_counts_tokens_without_logging_content(self):
        responses = iter([
            httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "length", "message": {"content": "private-content"},
                }],
                "usage": {"total_tokens": 123},
            }),
            httpx.Response(200, json={
                "choices": [{"finish_reason": "private-reason"}],
                "usage": {"total_tokens": 12},
            }),
        ])
        agent = self.make_agent(lambda _: next(responses))

        with self.assertLogs("ai_service.service.limits", level="INFO") as logs:
            with self.assertRaisesRegex(AiAgentError, "^invalid_response$"):
                await agent.interpret_request(request_context("Ярче"), timeout_s=1)

        events = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual([event["reason"] for event in events], ["proxy_finish_reason"] * 2)
        self.assertEqual([event["finish_reason"] for event in events], ["length", "unknown"])
        self.assertEqual([event["total_tokens"] for event in events], [123, 12])
        self.assertEqual(agent.usage()["total_tokens"], 135)
        self.assertEqual(agent.usage()["responses_with_usage"], 2)
        self.assertNotIn("private-", "\n".join(logs.output))

    async def test_unresolved_reference_is_not_retried(self):
        agent = self.make_agent(lambda request: completion(json.dumps({
            "record_id": "request_1", "error": "unresolved_reference",
        })))
        with self.assertRaisesRegex(AiAgentError, "^unresolved_reference$"):
            await agent.interpret_request(request_context("Как в тот раз"), timeout_s=1)
        self.assertEqual(agent.usage()["attempts"], 1)

    async def test_rate_limit_retries_once_only_within_deadline(self):
        for retry_after, attempts in (("0", 2), ("120", 1)):
            with self.subTest(retry_after=retry_after):
                agent = self.make_agent(lambda request: httpx.Response(
                    429, headers={"Retry-After": retry_after}
                ))
                with self.assertRaisesRegex(AiAgentError, "^rate_limited$"):
                    await agent.interpret_request(request_context("Теплее"), timeout_s=1)
                self.assertEqual(agent.usage()["attempts"], attempts)

    async def test_requests_and_preferences_share_rate_budget(self):
        agent = self.make_agent(
            lambda request: completion(json.dumps({
                "record_id": "request_1", "intent": "brighter",
                "slots": {"mode": "relative", "amount": None},
            })),
            requests_per_minute=30,
        )
        item = request_context("Ярче")
        await agent.interpret_request(item, timeout_s=1)
        queued = await agent.extract_preference_hints([item.profile], timeout_s=0.03)
        self.assertEqual(queued[0].error, "timeout")
        self.assertEqual(agent.usage()["attempts"], 1)

    async def test_context_limit_keeps_relevant_old_history_and_recent_turns(self):
        item = request_context("Поменяй будильник как договаривались")
        old = DialogueMessage(role="user", at=AT, text="О будильнике договорились на 07:00")
        recent = DialogueMessage(role="agent", at=AT, text="Принял")
        filler = DialogueMessage(role="user", at=AT, text="шторы " * 200)
        item = replace(item, conversation=[old, *([filler] * 12), recent])

        def handle(request):
            payload = json.loads(json.loads(request.content)["messages"][1]["content"])
            history = payload["conversation"]
            self.assertEqual(history[0]["text"], old.text)
            self.assertEqual(history[-1]["text"], recent.text)
            self.assertLess(len(history), len(item.conversation))
            return completion(json.dumps({
                "record_id": "request_1", "error": "unresolved_reference",
            }))

        with self.assertRaisesRegex(AiAgentError, "^unresolved_reference$"):
            await self.make_agent(handle).interpret_request(item, timeout_s=1)

    async def test_implicit_follow_up_receives_the_last_two_dialogue_messages(self):
        item = request_context("А можно поменьше?")
        old = DialogueMessage(role="user", at=AT, text="Закрой шторы")
        recent = [
            DialogueMessage(role="user", at=AT, text="Добавь яркости свету"),
            DialogueMessage(role="agent", at=AT, text="Изменил яркость света."),
        ]
        item = replace(item, conversation=[old, *recent])

        def handle(request):
            payload = json.loads(json.loads(request.content)["messages"][1]["content"])
            self.assertEqual(payload["conversation"], [message.model_dump() for message in recent])
            return completion(
                '{"record_id":"request_1","intent":"dimmer","slots":{"mode":"relative","amount":null}}'
            )

        result = await self.make_agent(handle).interpret_request(item, timeout_s=1)
        self.assertEqual(result.intent, "dimmer")

    async def test_yesterday_keeps_matching_stay_note_ahead_of_later_notes(self):
        item = request_context("Сделай как вчера")
        yesterday = {
            "user_id": "guest", "event_id": "matching", "source": "hotel",
            "started_at": "2026-11-20T08:00:00+03:00",
            "payload": {"text": "Заезд 19.11: поставили 18 °C."},
        }
        later_notes = [
            yesterday | {"event_id": f"later_{index}", "payload": {"text": "Проверили шторы."}}
            for index in range(5)
        ]
        item = replace(item, profile=replace(item.profile, events=[yesterday, *later_notes]))

        def handle(request):
            payload = json.loads(json.loads(request.content)["messages"][1]["content"])
            self.assertIn(yesterday, payload["events"])
            self.assertLessEqual(len(payload["events"]), 3)
            return completion('{"record_id":"request_1","intent":"like_yesterday","slots":{}}')

        result = await self.make_agent(handle).interpret_request(item, timeout_s=1)
        self.assertEqual(result.intent, "like_yesterday")
        self.assertIsNone(result.slots.days_ago)
