"""Request planning, simulator execution and conversation isolation."""

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import httpx

from concierge_service.domain.action_planner import ActionPlanError, DevicePatch, plan_actions
from concierge_service.domain.dataset import Request, Utterance
from concierge_service.domain.sleep import ResolvedPreferences, SleepPlanner
from concierge_service.infra.conversation_store import InMemoryConversationStore
from concierge_service.infra.hack_api import HackApiAdapter
from concierge_service.infra.mock_ai import MockAiAgent
from concierge_service.infra.room import RoomApiAdapter
from concierge_service.ports.ai import (
    REQUEST_INTERPRETATION_ADAPTER,
    AiAgent,
    AiAgentError,
    ProfileContext,
    RequestContext,
)
from concierge_service.ports.room import RoomApi, RoomApiError, RoomSnapshot
from concierge_service.service.request_handler import RequestHandler
from concierge_service.service.run_case import RunCase
from concierge_service.transport.http.app import create_app
from test_concierge_stage1 import SESSION_ID, dataset_payload
from test_concierge_stage2 import catalog, session_payload, snapshot_payload

AT = "2026-11-20T22:00:00+03:00"


class ActionPlannerTests(unittest.TestCase):
    def test_baseline_intents_change_only_target_fields(self) -> None:
        state = {
            "light": {"brightness": 50, "color_temperature_k": 4000},
            "climate": {"temperature_c": 21},
            "sound": {"volume": 40, "track_id": "track_1", "timer_minutes": 30},
            "curtains": {"position": 90},
            "dnd": {"enabled": True},
        }
        cases = [
            ("brighter", {}, (DevicePatch("light", {"brightness": 65}),)),
            ("dimmer", {"amount": 5}, (DevicePatch("light", {"brightness": 45}),)),
            ("warmer", {"amount": 1.5}, (DevicePatch("climate", {"temperature_c": 22.5}),)),
            ("cooler", {"target": 18.5}, (DevicePatch("climate", {"temperature_c": 18.5}),)),
            ("quieter", {}, (DevicePatch("sound", {"volume": 25}),)),
            ("curtains", {"target": 0}, (DevicePatch("curtains", {"position": 0}),)),
            ("do_not_disturb", {"target": False}, (DevicePatch("dnd", {"enabled": False}),)),
            ("all_off", {}, (DevicePatch("light", {"brightness": 0}), DevicePatch("sound", {"volume": 0}))),
        ]
        for intent, kwargs, expected in cases:
            with self.subTest(intent=intent):
                self.assertEqual(plan_actions(intent, state, catalog(), **kwargs), expected)
        self.assertEqual(state["sound"], {"volume": 40, "track_id": "track_1", "timer_minutes": 30})

    def test_default_step_reaches_boundary_but_explicit_invalid_values_are_refused(self) -> None:
        state = {"light": {"brightness": 95}}
        self.assertEqual(plan_actions("brighter", state, catalog()), (DevicePatch("light", {"brightness": 100}),))
        for kwargs in ({"target": 101}, {"target": 95.5}, {"amount": 10}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ActionPlanError, "^invalid_value$"):
                plan_actions("brighter", state, catalog(), **kwargs)

        self.assertEqual(plan_actions("brighter", state, catalog(), target=95), ())
        with self.assertRaisesRegex(ActionPlanError, "^invalid_value$"):
            plan_actions("brighter", {"light": {"brightness": 100}}, catalog())
        with self.assertRaisesRegex(ActionPlanError, "^missing_state$"):
            plan_actions("brighter", {}, catalog())


class RequestHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        now = datetime.fromisoformat(AT)
        self.request = Request(
            "request_1", "user_1", "stay_1", now,
            {"light": {"brightness": 50}, "sound": {"volume": 20}},
            (Utterance(now, "Выключи всё"),),
        )
        self.context = RequestContext(
            profile=ProfileContext(user={"user_id": "user_1"}, events=[], chat_history=[], devices=[], tracks=[]),
            request={"record_id": self.request.record_id}, stay={}, intents=[], conversation=[],
            preference_hints=None,
        )
        self.agent = AsyncMock(spec=AiAgent)
        self.agent.interpret_request.return_value = REQUEST_INTERPRETATION_ADAPTER.validate_python({
            "record_id": self.request.record_id, "intent": "all_off", "slots": {},
        })
        self.room = AsyncMock(spec=RoomApi)
        self.room.create_session.return_value = SimpleNamespace(session_id=SESSION_ID)
        self.handler = RequestHandler(self.room, self.agent)

    async def _handle(self, fields):
        return await self.handler.handle(
            self.request, self.context, fields, token="synthetic-token",
            preferences=ResolvedPreferences(None, time(23), 19.5, False, (), False),
            deadline=asyncio.get_running_loop().time() + 5,
        )

    async def test_entire_plan_is_validated_before_first_action(self) -> None:
        fields = catalog()
        volume = fields["sound"]["volume"]
        fields["sound"]["volume"] = replace(volume, value_schema={"type": "integer", "minimum": 1})
        answer = await self._handle(fields)
        self.room.apply_action.assert_not_awaited()
        self.assertEqual(answer.replies[0].response_class, "refused")

    async def test_partial_failure_reports_only_confirmed_progress(self) -> None:
        self.room.apply_action.side_effect = [
            RoomSnapshot(SESSION_ID, self.request.at, {
                "light": {"brightness": 0, "private_field": "private-snapshot-marker"}
            }),
            RoomApiError("private-error-marker", http_status=503),
        ]
        with self.assertLogs("concierge_service.service.request_handler", level="DEBUG") as logs:
            answer = await self._handle(catalog())
        events = {event["event"]: event for event in (
            json.loads(record.getMessage()) for record in logs.records
        )}
        self.assertEqual(events["action_snapshot"]["values"], {"brightness": 0})
        self.assertEqual(events["request_action_failed"]["http_status"], 503)
        self.assertNotIn("private-", "\n".join(logs.output))
        self.assertEqual(self.room.apply_action.await_count, 2)
        self.assertEqual(answer.replies[0].response_class, "refused")
        self.assertIn("Часть изменений применил", answer.replies[0].text)

    async def test_already_satisfied_state_returns_done_without_action(self) -> None:
        self.request = replace(self.request, initial_state={
            "light": {"brightness": 0}, "sound": {"volume": 0},
        })
        answer = await self._handle(catalog())
        self.room.apply_action.assert_not_awaited()
        self.assertEqual(answer.replies[0].response_class, "done")

    async def test_failed_session_does_not_invent_an_answer(self) -> None:
        self.room.create_session.side_effect = RoomApiError("simulator unavailable")
        with self.assertLogs("concierge_service.service.request_handler", level="WARNING"):
            self.assertIsNone(await self._handle(catalog()))
        self.agent.interpret_request.assert_not_awaited()
        self.room.apply_action.assert_not_awaited()

    async def test_agent_error_still_returns_refusal_in_existing_session(self) -> None:
        self.agent.interpret_request.side_effect = AiAgentError("unavailable")
        answer = await self._handle(catalog())
        self.room.create_session.assert_awaited_once_with(
            record_id="request_1", kind="request", token="synthetic-token"
        )
        self.assertEqual(answer.replies[0].response_class, "refused")
        self.room.apply_action.assert_not_awaited()

    async def test_interpretation_for_another_record_cannot_execute(self) -> None:
        self.agent.interpret_request.return_value = REQUEST_INTERPRETATION_ADAPTER.validate_python({
            "record_id": "request_other", "intent": "all_off", "slots": {},
        })
        answer = await self._handle(catalog())
        self.assertEqual(answer.replies[0].response_class, "refused")
        self.room.apply_action.assert_not_awaited()

    async def test_unconfirmed_state_cannot_be_reported_as_done(self) -> None:
        self.room.apply_action.return_value = RoomSnapshot(
            SESSION_ID, self.request.at, self.request.initial_state
        )
        with self.assertLogs("concierge_service.service.request_handler", level="WARNING"):
            answer = await self._handle(catalog())
        self.assertEqual(answer.replies[0].response_class, "refused")
        self.room.apply_action.assert_awaited_once()


class RequestRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_mocked_run_sends_actions_and_keeps_history_per_user_and_run(self) -> None:
        payload = dataset_payload()
        payload["users"].append(dict(payload["users"][0], user_id="user_2"))
        payload["stays"].append(dict(payload["stays"][0], record_id="stay_2", user_id="user_2"))
        later = payload["requests"][0]
        first = dict(
            later, record_id="request_0", at="2026-11-20T21:00:00+03:00",
            initial_state={"light": {"brightness": 20, "color_temperature_k": 4000}},
            utterances=[{"role": "user", "at": "2026-11-20T21:00:00+03:00", "text": "Добавь света"}],
        )
        unsupported = dict(
            later, record_id="request_other", user_id="user_2", stay_id="stay_2",
        )
        broken = dict(first, record_id="request_broken", at="2026-11-20T20:00:00+03:00")
        payload["requests"].extend([unsupported, first, broken])
        sessions = {}
        actions = []

        def simulator(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer synthetic-token")
            path = request.url.path
            if path == "/case/2/getData":
                return httpx.Response(200, json={"dataset": payload, "reference": None})
            if path == "/room/session":
                body = json.loads(request.content)
                record_id = body["record_id"]
                self.assertEqual(set(body), {"record_id", "kind"})
                if record_id == "request_broken":
                    return httpx.Response(503)
                session = session_payload(record_id)
                session.update(session_id=str(uuid5(NAMESPACE_URL, record_id)), kind=body["kind"])
                if body["kind"] == "request":
                    requested = next(item for item in payload["requests"] if item["record_id"] == record_id)
                    session.update(at=requested["at"], stay_id=requested["stay_id"],
                                   initial_state=requested["initial_state"])
                sessions[session["session_id"]] = session
                return httpx.Response(200, json=session)
            session = sessions[path.split("/")[2]]
            snapshot = dict(snapshot_payload(), session_id=session["session_id"])
            if path.endswith("/action"):
                self.assertEqual(session["kind"], "request")
                action = json.loads(request.content)
                actions.append(action)
                device_state = dict(session["initial_state"].get(action["device_id"], {}), **action["values"])
                snapshot.update(at=action["at"], state={action["device_id"]: device_state})
            else:
                self.assertTrue(path.endswith("/scenario"))
                self.assertEqual(session["kind"], "stay")
            return httpx.Response(200, json=snapshot)

        agent = AsyncMock(spec=AiAgent)
        agent.extract_preference_hints.side_effect = MockAiAgent().extract_preference_hints

        async def interpret(context: RequestContext, *, timeout_s: float):
            record_id = context.request["record_id"]
            intent, slots = ("brighter", {"mode": "relative", "amount": None})
            if record_id == "request_other":
                intent, slots = "out_of_scope", {}
            return REQUEST_INTERPRETATION_ADAPTER.validate_python({
                "record_id": record_id, "intent": intent, "slots": slots,
            })

        agent.interpret_request.side_effect = interpret
        async with httpx.AsyncClient(transport=httpx.MockTransport(simulator)) as upstream:
            base = "https://hack.test"
            app = create_app(RunCase(
                HackApiAdapter(base_url=base, client=upstream),
                room_api=RoomApiAdapter(base_url=base, client=upstream),
                sleep_planner=SleepPlanner(), ai_agent=agent,
                conversation_store_factory=InMemoryConversationStore,
            ))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                for _ in range(2):
                    with self.assertLogs("concierge_service.service.request_handler", level="WARNING"):
                        response = await client.post(
                            "/run", json={"case": 2},
                            headers={"Authorization": "Bearer synthetic-token"},
                        )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(response.json()["stays"]), 2)
                    answers = response.json()["requests"]
                    self.assertEqual([answer["record_id"] for answer in answers],
                                     ["request_0", "request_1", "request_other"])
                    self.assertEqual(
                        [reply["response_class"] for reply in answers[1]["replies"]], ["done"]
                    )
                    self.assertEqual(
                        [reply["at"] for reply in answers[1]["replies"]],
                        [later["utterances"][-1]["at"]],
                    )
                    self.assertEqual(answers[2]["replies"][0]["response_class"], "refused")

        self.assertEqual(actions, [
            {"at": first["at"], "device_id": "light", "values": {"brightness": 35}},
            {"at": later["at"], "device_id": "light", "values": {"brightness": 65}},
        ] * 2)
        contexts = [call.args[0] for call in agent.interpret_request.await_args_list]
        self.assertEqual(len(contexts), 6)
        first_user = [context for context in contexts if context.profile.user["user_id"] == "user_1"]
        second_user = [context for context in contexts if context.profile.user["user_id"] == "user_2"]
        self.assertEqual([context.request for context in first_user], [first, later] * 2)
        self.assertEqual([context.request for context in second_user], [unsupported] * 2)
        for context in first_user[::2] + second_user:
            self.assertEqual(context.conversation, ())
        for context in first_user[1::2]:
            self.assertEqual([message.role for message in context.conversation], ["user", "agent"])
            self.assertEqual(context.conversation[0].text, first["utterances"][0]["text"])
