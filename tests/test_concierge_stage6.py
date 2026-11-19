"""Reliability tests for the online run."""

import asyncio
import json
import unittest
from collections import Counter
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from concierge_service.domain.action_planner import DevicePatch
from concierge_service.domain.sleep import SleepPlanner
from concierge_service.infra.conversation_store import InMemoryConversationStore
from concierge_service.infra.hack_api import HackApiAdapter
from concierge_service.infra.mock_ai import MockAiAgent
from concierge_service.infra.room import RoomApiAdapter
from concierge_service.ports.hack_api import HackApi
from concierge_service.ports.room import RoomApi, RoomApiError
from concierge_service.service.run_case import RunCase
from concierge_service.transport.http.app import create_app
from test_concierge_stage1 import SESSION_ID, dataset_payload
from test_concierge_stage2 import WAKE, session_payload, snapshot_payload


class UpstreamRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_only_transient_reads_and_session_creation(self):
        calls = Counter()
        session_requests = []
        payload = dataset_payload()

        def upstream(request):
            path = request.url.path
            calls[path] += 1
            if path == "/case/2/getData":
                self.assertEqual(dict(request.url.params), {"dataset_code": "private-dataset"})
                if calls[path] == 1:
                    return httpx.Response(503, text="private-body")
                return httpx.Response(200, json={"dataset": payload, "reference": None})
            if path == "/room/session":
                session_requests.append((dict(request.headers), json.loads(request.content)))
                if calls[path] == 1:
                    raise httpx.ReadTimeout("private-exception", request=request)
                return httpx.Response(200, json=session_payload())
            if path.endswith("/state"):
                if calls[path] == 1:
                    return httpx.Response(502)
                return httpx.Response(200, json=snapshot_payload())
            if path.endswith("/log"):
                return httpx.Response(403)
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            hack = HackApiAdapter(base_url="https://hack.test", client=client)
            room = RoomApiAdapter(base_url="https://hack.test", client=client)
            token = "private-token"
            with (
                patch("concierge_service.infra.http_retry._RETRY_DELAY_S", 0),
                self.assertLogs("concierge_service.infra.http_retry", level="INFO") as logs,
            ):
                parsed = await hack.get_case2_data(dataset_code="private-dataset", token=token)
                session = await room.create_session(record_id="stay_1", kind="stay", token=token)
                await room.get_state(session_id=session.session_id, token=token)
                with self.assertRaises(RoomApiError) as forbidden:
                    await room.get_log(session_id=session.session_id, token=token)
                with self.assertRaises(RoomApiError):
                    await room.load_scenario(session_id=session.session_id, steps=(), token=token)
                with self.assertRaises(RoomApiError):
                    await room.apply_action(
                        session_id=session.session_id, patch=DevicePatch("light", {"brightness": 20}),
                        at=datetime.fromisoformat(WAKE), token=token,
                    )

        self.assertEqual(parsed.dataset.meta.dataset_id, payload["meta"]["dataset_id"])
        self.assertEqual(session.session_id, SESSION_ID)
        self.assertEqual(forbidden.exception.http_status, 403)
        self.assertEqual(session_requests[0], session_requests[1])
        self.assertEqual(calls, {
            "/case/2/getData": 2,
            "/room/session": 2,
            f"/room/{SESSION_ID}/state": 2,
            f"/room/{SESSION_ID}/log": 1,
            f"/room/{SESSION_ID}/scenario": 1,
            f"/room/{SESSION_ID}/action": 1,
        })
        self.assertEqual(len(logs.records), 3)
        self.assertNotIn("private-", "\n".join(logs.output))


class RunBudgetTests(unittest.IsolatedAsyncioTestCase):
    def make_run(self, hack_api, room_api, ai_agent=None):
        return RunCase(
            hack_api, room_api=room_api, sleep_planner=SleepPlanner(),
            ai_agent=ai_agent or MockAiAgent(), conversation_store_factory=InMemoryConversationStore,
        )

    async def test_runs_four_users_concurrently_and_preserves_each_dialogue(self):
        payload = dataset_payload()
        user, = payload["users"]
        stay, = payload["stays"]
        later, = payload["requests"]
        payload["users"] = []
        payload["stays"] = []
        payload["requests"] = []
        for number in range(1, 6):
            user_id = f"user_{number}"
            stay_id = f"stay_{number}"
            earlier = dict(
                later, record_id=f"early_{number}", at="2026-11-20T21:00:00+03:00",
                user_id=user_id, stay_id=stay_id,
                utterances=[{"role": "user", "at": "2026-11-20T21:00:00+03:00", "text": user_id}],
            )
            payload["users"].append(dict(user, user_id=user_id))
            payload["stays"].append(dict(stay, record_id=stay_id, user_id=user_id))
            payload["requests"].extend([
                dict(later, record_id=f"late_{number}", user_id=user_id, stay_id=stay_id),
                earlier,
            ])

        active = set()
        first_wave = asyncio.Event()
        proceed = asyncio.Event()
        agent = MockAiAgent()
        interpret = agent.interpret_request

        async def blocked_interpret(context, *, timeout_s):
            user_id = context.profile.user["user_id"]
            if context.request["record_id"].startswith("early_"):
                self.assertEqual(context.conversation, ())
                active.add(user_id)
                if len(active) == 4:
                    first_wave.set()
                await proceed.wait()
            else:
                self.assertEqual([message.role for message in context.conversation], ["user", "agent"])
                self.assertEqual(context.conversation[0].text, user_id)
            return await interpret(context, timeout_s=timeout_s)

        room = AsyncMock(spec=RoomApi)
        room.create_session.return_value = SimpleNamespace(session_id=SESSION_ID)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"dataset": payload, "reference": None},
        ))) as client:
            run_case = self.make_run(HackApiAdapter(base_url="https://hack.test", client=client), room, agent)
            with patch.object(agent, "interpret_request", side_effect=blocked_interpret):
                run = asyncio.create_task(run_case.run(case=2, dataset_code=None, token="test"))
                try:
                    await asyncio.wait_for(first_wave.wait(), timeout=2)
                    self.assertEqual(active, {"user_1", "user_2", "user_3", "user_4"})
                finally:
                    proceed.set()
                    result = await asyncio.wait_for(run, timeout=2)
        self.assertEqual([answer.record_id for answer in result.requests], [
            record_id for number in range(1, 6) for record_id in (f"early_{number}", f"late_{number}")
        ])

    async def test_deadline_stops_room_io_and_keeps_completed_records(self):
        payload = dataset_payload()
        request, = payload["requests"]
        payload["requests"].extend([
            dict(request, record_id="blocked"),
            dict(request, record_id="queued"),
        ])
        session_calls = []
        cancelled = asyncio.Event()

        async def create_session(*, record_id, kind, token):
            session_calls.append(record_id)
            if record_id == "blocked":
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return SimpleNamespace(session_id=SESSION_ID)

        room = AsyncMock(spec=RoomApi)
        room.create_session.side_effect = create_session
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"dataset": payload, "reference": None},
        ))) as client:
            run_case = self.make_run(HackApiAdapter(base_url="https://hack.test", client=client), room)
            with (
                patch("concierge_service.service.run_case._RUN_BUDGET_S", 0.2),
                self.assertLogs("concierge_service.service.run_case", level="WARNING") as logs,
            ):
                result = await asyncio.wait_for(
                    run_case.run(case=2, dataset_code=None, token="test"), timeout=2,
                )

        self.assertTrue(cancelled.is_set())
        self.assertEqual(session_calls, ["stay_1", request["record_id"], "blocked"])
        self.assertEqual([answer.record_id for answer in result.stays], ["stay_1"])
        self.assertEqual([answer.record_id for answer in result.requests], [request["record_id"]])
        self.assertIn("run_deadline_reached", logs.output[0])

    async def test_deadline_also_cancels_dataset_fetch_and_returns_504(self):
        async def fetch(**kwargs):
            await asyncio.Event().wait()

        hack = AsyncMock(spec=HackApi)
        hack.get_case2_data.side_effect = fetch
        room = AsyncMock(spec=RoomApi)
        app = create_app(self.make_run(hack, room))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("concierge_service.service.run_case._RUN_BUDGET_S", 0.01):
                response = await client.post(
                    "/run", json={"case": 2}, headers={"Authorization": "Bearer test"},
                )
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json(), {"detail": {"code": "run_deadline_exceeded"}})
        room.create_session.assert_not_called()
