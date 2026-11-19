"""An unexpected bug must cost one record, not the whole /run."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from concierge_service.di.config import Settings
from concierge_service.di.container import build_container
from concierge_service.domain.sleep import SleepPlanner
from concierge_service.infra.conversation_store import InMemoryConversationStore
from concierge_service.infra.hack_api import HackApiAdapter
from concierge_service.infra.mock_ai import MockAiAgent
from concierge_service.ports.room import RoomApi
from concierge_service.service import run_case as run_case_module
from concierge_service.service.run_case import RunCase
from concierge_service.transport.http.app import create_app
from test_concierge_stage1 import SESSION_ID, dataset_payload


def two_guest_payload() -> dict:
    """Two guests with one stay and one request each: user_1/request_1, user_2/request_2."""
    payload = dataset_payload()
    user, = payload["users"]
    stay, = payload["stays"]
    request, = payload["requests"]
    payload["users"].append(dict(user, user_id="user_2"))
    payload["stays"].append(dict(stay, record_id="stay_2", user_id="user_2"))
    payload["requests"].append(
        dict(request, record_id="request_2", user_id="user_2", stay_id="stay_2")
    )
    return payload


class UnexpectedErrorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.agent = MockAiAgent()
        self.room = AsyncMock(spec=RoomApi)
        self.room.create_session.return_value = SimpleNamespace(session_id=SESSION_ID)
        payload = two_guest_payload()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"dataset": payload, "reference": None},
        )))
        self.run_case = RunCase(
            HackApiAdapter(base_url="https://hack.test", client=self.client),
            room_api=self.room, sleep_planner=SleepPlanner(), ai_agent=self.agent,
            conversation_store_factory=InMemoryConversationStore,
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def run_case_once(self):
        return await self.run_case.run(case=2, dataset_code=None, token="test")

    async def test_interpretation_bug_refuses_only_that_request_and_run_returns_200(self):
        interpret = self.agent.interpret_request

        async def broken_interpret(context, *, timeout_s):
            if context.request["record_id"] == "request_1":
                raise RuntimeError("sensitive exception message")
            return await interpret(context, timeout_s=timeout_s)

        app = create_app(self.run_case)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch.object(self.agent, "interpret_request", side_effect=broken_interpret),
                self.assertLogs("concierge_service.service.request_handler", level="WARNING") as logs,
            ):
                response = await client.post(
                    "/run", json={"case": 2}, headers={"Authorization": "Bearer test"},
                )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([stay["record_id"] for stay in body["stays"]], ["stay_1", "stay_2"])
        answers = {answer["record_id"]: answer for answer in body["requests"]}
        self.assertEqual(set(answers), {"request_1", "request_2"})
        broken = answers["request_1"]
        self.assertEqual(broken["session_id"], str(SESSION_ID))
        self.assertEqual([reply["response_class"] for reply in broken["replies"]], ["refused"])
        event, = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual(event["event"], "request_unexpected_error")
        self.assertEqual(event["error_type"], "RuntimeError")
        self.assertEqual(event["error_module"], __name__)
        self.assertEqual(event["error_function"], "broken_interpret")
        self.assertGreater(event["error_line"], 0)
        self.assertNotIn("sensitive exception message", logs.output[0])

    async def test_bug_around_handler_keeps_other_guests_dialogues(self):
        handle = self.run_case._request_handler.handle

        async def broken_handle(request, *args, **kwargs):
            if request.record_id == "request_1":
                raise RuntimeError("bug")
            return await handle(request, *args, **kwargs)

        with (
            patch.object(self.run_case._request_handler, "handle", side_effect=broken_handle),
            self.assertLogs("concierge_service.service.run_case", level="WARNING") as logs,
        ):
            result = await self.run_case_once()

        self.assertEqual([answer.record_id for answer in result.requests], ["request_2"])
        event, = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual(event["event"], "request_skipped")
        self.assertEqual(event["error_function"], "broken_handle")

    async def test_planner_bug_skips_only_that_stay(self):
        plan = SleepPlanner.plan

        def broken_plan(planner, stay, *args):
            if stay.record_id == "stay_1":
                raise RuntimeError("bug")
            return plan(planner, stay, *args)

        with (
            patch.object(SleepPlanner, "plan", autospec=True, side_effect=broken_plan),
            self.assertLogs("concierge_service.service.run_case", level="WARNING") as logs,
        ):
            result = await self.run_case_once()

        self.assertEqual([stay.record_id for stay in result.stays], ["stay_2"])
        self.assertEqual(
            sorted(answer.record_id for answer in result.requests), ["request_1", "request_2"]
        )
        output = "\n".join(logs.output)
        self.assertIn('"reason": "unexpected_error"', output)
        self.assertIn("RuntimeError", output)

    async def test_preference_extraction_bug_falls_back_to_profiles(self):
        with (
            patch.object(self.agent, "extract_preference_hints", side_effect=RuntimeError("bug")),
            self.assertLogs("concierge_service.service.run_case", level="WARNING") as logs,
        ):
            result = await self.run_case_once()

        self.assertEqual([stay.record_id for stay in result.stays], ["stay_1", "stay_2"])
        self.assertIn('"reason": "unexpected_error"', "\n".join(logs.output))

    async def test_bad_hints_fall_back_to_profile_preferences(self):
        resolve = run_case_module.resolve_preferences

        def broken_resolve(user, track_ids, hints=None):
            if user.user_id == "user_1" and hints is not None:
                raise ValueError("bad hint")
            return resolve(user, track_ids, hints)

        with (
            patch.object(run_case_module, "resolve_preferences", side_effect=broken_resolve),
            patch.object(self.agent, "interpret_request", wraps=self.agent.interpret_request) as interpret,
            self.assertLogs("concierge_service.service.run_case", level="WARNING") as logs,
        ):
            result = await self.run_case_once()

        self.assertEqual([stay.record_id for stay in result.stays], ["stay_1", "stay_2"])
        self.assertIn("preference_hints_ignored", "\n".join(logs.output))
        contexts = {call.args[0].request["record_id"]: call.args[0] for call in interpret.await_args_list}
        self.assertIsNone(contexts["request_1"].preference_hints)
        self.assertEqual(contexts["request_2"].preference_hints.user_id, "user_2")

    async def test_unusable_profile_skips_only_its_guest(self):
        resolve = run_case_module.resolve_preferences

        def broken_resolve(user, track_ids, hints=None):
            if user.user_id == "user_1":
                raise ValueError("unusable profile")
            return resolve(user, track_ids, hints)

        app = create_app(self.run_case)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch.object(run_case_module, "resolve_preferences", side_effect=broken_resolve),
                self.assertLogs("concierge_service.service.run_case", level="WARNING") as logs,
            ):
                response = await client.post(
                    "/run", json={"case": 2}, headers={"Authorization": "Bearer test"},
                )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([stay["record_id"] for stay in body["stays"]], ["stay_2"])
        self.assertEqual([request["record_id"] for request in body["requests"]], ["request_2"])
        self.assertEqual(
            [call.kwargs["record_id"] for call in self.room.create_session.await_args_list],
            ["stay_2", "request_2"],
        )
        events = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual([event["event"] for event in events], ["preference_hints_ignored", "profile_skipped"])
        self.assertTrue(all(event["profile_number"] == 1 for event in events))


class MissingLlmSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_warns_when_falling_back_to_mock_ai(self):
        with self.assertLogs("concierge_service.di.container", level="WARNING") as logs:
            container = build_container(Settings(hack_api_url="https://hack.test"))
        await container.aclose()
        self.assertIn("llm_not_configured", "\n".join(logs.output))

    async def test_no_warning_when_agent_is_provided(self):
        with self.assertNoLogs("concierge_service.di.container", level="WARNING"):
            container = build_container(
                Settings(hack_api_url="https://hack.test"), ai_agent=MockAiAgent()
            )
        await container.aclose()


if __name__ == "__main__":
    unittest.main()
