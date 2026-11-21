import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI

from concierge_service.domain.sleep import SleepPlanner
from concierge_service.infra.hack_api import HackApiAdapter
from concierge_service.infra.mock_ai import MockAiAgent
from concierge_service.infra.room import RoomApiAdapter
from concierge_service.ports.ai import AbsoluteAmount, EmptySlots, NoSlotsInterpretation, NumericInterpretation, RelativeAmount
from concierge_service.service.demo import DemoService
from concierge_service.transport.http.demo import install_demo
from test_concierge_stage1 import dataset_payload


class DemoFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dataset = dataset_payload()
        self.datasets = {"case2-test-v1": self.dataset}
        self.run_id = str(uuid4())
        self.final_runs = set()
        self.final_dataset_id = "case2-final-test"
        self.fail_session = False
        self.room_ids = {}
        self.calls = []
        self.scenarios = []
        self.room_scenarios = {}
        self.room_actions = {}
        self.failed_action_devices = set()
        self.fail_publish = False
        self.closed_rooms = set()
        self.until = timedelta(hours=18)
        self.check_in = datetime(2027, 1, 20, 12, tzinfo=UTC)
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(self.stand))
        self.addAsyncCleanup(upstream.aclose)
        ai = MockAiAgent()
        ai.extract_preference_hints = AsyncMock(wraps=ai.extract_preference_hints)
        self.hints = ai.extract_preference_hints
        self.service = DemoService(HackApiAdapter(base_url="https://stand", client=upstream),
                                   RoomApiAdapter(base_url="https://stand", client=upstream),
                                   ai, "team-token")
        app = FastAPI()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.web = Path(tmp.name)
        (self.web / "dist").mkdir()
        (self.web / "dist/room.js").write_text("private build")
        (self.web / "index.html").write_text("entry")
        install_demo(app, self.service, "show-code", self.web)
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://demo")
        self.addAsyncCleanup(self.api.aclose)

    async def stand(self, request):
        await asyncio.sleep(0)
        self.assertEqual(request.headers["authorization"], "Bearer team-token")
        path = request.url.path
        self.calls.append(path)
        if path.endswith("getData"):
            data = self.datasets.get(request.url.params["dataset_code"])
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"dataset": data, "reference": None})
        if request.method == "GET" and path.endswith("/state"):
            sid = path.split("/")[2]
            at = datetime.fromisoformat(request.url.params["at"])
            state = self.snapshot(sid, at)
            return httpx.Response(200, json={
                "session_id": sid, "room_id": sid, "at": at.isoformat(), "state": state,
            })
        body = json.loads(request.content)
        if path == "/runs":
            if body == {"case": 2, "stage": "final"}:
                run_id = str(uuid4())
                self.final_runs.add(run_id)
                dataset_id = self.final_dataset_id
            else:
                self.assertEqual(body, {"case": 2})
                run_id, dataset_id = self.run_id, "case2-test-v1"
            return httpx.Response(201, json={"run_id": run_id, "dataset_id": dataset_id,
                                            "expires_at": (datetime.now(UTC) + timedelta(minutes=45)).isoformat()})
        if path == "/room/session":
            run_id = body["run_id"]
            self.assertIn(run_id, self.final_runs | {self.run_id})
            if self.fail_session:
                return httpx.Response(400)
            self.assertEqual(body["kind"], "stay")
            record = body["record_id"]
            final = run_id in self.final_runs
            dataset_id = self.final_dataset_id if final else "case2-test-v1"
            room_key = (run_id, record) if final else record
            sid = self.room_ids.setdefault(room_key, str(uuid4()))
            return httpx.Response(200, json={
                "session_id": sid, "run_id": run_id, "room_id": sid, "team_id": "team",
                "dataset_id": dataset_id, "record_id": record, "stay_id": record, "kind": "stay",
                "from": self.check_in.isoformat(), "at": self.check_in.isoformat(),
                "until": (self.check_in + self.until).isoformat(), "initial_state": {"light": {"brightness": 70}},
            })
        if path.endswith("/action"):
            sid = path.split("/")[2]
            if body["device_id"] in self.failed_action_devices:
                return httpx.Response(503)
            self.room_actions.setdefault(sid, []).append(body)
            at = datetime.fromisoformat(body["at"])
            return httpx.Response(200, json={
                "session_id": sid, "room_id": sid, "at": at.isoformat(), "state": self.snapshot(sid, at),
            })
        self.assertTrue(path.endswith("/scenario"))
        if path.split("/")[2] in self.closed_rooms:
            return httpx.Response(410)
        if self.fail_publish:
            return httpx.Response(503)
        self.scenarios.append(body["steps"])
        sid = path.split("/")[2]
        self.room_scenarios[sid] = body["steps"]
        self.room_actions.pop(sid, None)
        return httpx.Response(200, json={"session_id": sid, "room_id": sid,
                                        "at": self.check_in.isoformat(), "state": {"light": {"brightness": 70}}})

    def snapshot(self, sid, at):
        state = {"climate": {"temperature_c": 23}, "light": {"brightness": 70, "color_temperature_k": 4000},
                 "sound": {"volume": 20}, "dnd": {"enabled": False}, "curtains": {"position": 100}}
        steps = self.room_scenarios[sid] + self.room_actions.get(sid, [])
        for step in sorted(steps, key=lambda step: datetime.fromisoformat(step["at"])):
            if datetime.fromisoformat(step["at"]) <= at:
                state.setdefault(step["device_id"], {}).update(step["values"])
        return state

    async def start(self, number=1, **fields):
        return await self.api.post("/api/v1/run_client", headers={"Authorization": f"Bearer {number:064x}"},
                                   json={"dataset_code": "case2-test-v1", **fields})

    def final_dataset(self):
        data = dataset_payload()
        data["meta"].update(phase="final", dataset_id="case2-final-test")
        data["requests"] = []
        self.datasets["final-code"] = data
        return data

    async def room_request(self, method, path, number=1, **kwargs):
        return await self.api.request(method, f"/api/v1{path}",
                                      headers={"Authorization": f"Bearer {number:064x}"}, **kwargs)


class DemoStartTests(DemoFixture):
    def assert_same_stay(self, actual, expected):
        self.assertEqual(actual["status"], expected["status"])
        self.assertEqual({k: v for k, v in actual["stay"].items() if k != "clock"},
                         {k: v for k, v in expected["stay"].items() if k != "clock"})

    async def test_wildcard_address_redirects_to_localhost(self):
        response = await self.api.get("http://0.0.0.0:8000/")
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "http://localhost:8000/")

    async def test_gate_blocks_upstream_and_direct_assets(self):
        self.assertEqual((await self.api.get("/")).text, "entry")
        self.assertEqual((await self.api.get("/demo/room.js")).status_code, 403)
        for n in range(5):
            self.assertEqual((await self.start(n, access_code="wrong")).status_code, 403)
        self.assertEqual((await self.start(100, access_code="wrong")).status_code, 429)
        self.assertEqual(self.calls, [])

    async def test_single_stay_autostart_and_retry_failed_publication(self):
        self.fail_publish = True
        self.assertEqual((await self.start(access_code="show-code")).status_code, 502)
        self.assertFalse(self.service.clients[f"{1:064x}"].published)
        self.fail_publish = False
        response = await self.start()
        self.assertEqual(response.json()["status"], "ready", response.text)
        self.assertEqual(len(self.room_ids), 1)
        self.assertEqual(self.hints.await_count, 1)
        self.assertEqual((await self.start(stay_record_id="other")).status_code, 409)
        self.assertEqual((await self.api.get("/demo/room.js")).text, "private build")
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("Secure", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        self.assertEqual((await self.start(2)).status_code, 403)
        client = self.service.clients[f"{1:064x}"]
        client.run = replace(client.run, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        self.assertEqual((await self.api.get("/demo/room.js")).status_code, 410)
        self.assertEqual((await self.start()).status_code, 410)
        self.assertFalse(self.service.clients)

    async def test_five_clients_share_one_plan_and_resume_distinct_rooms(self):
        stay = self.dataset["stays"][0]
        self.dataset["stays"] += [stay | {"record_id": f"stay_{n}"} for n in range(2, 6)]
        selection = await self.start(access_code="show-code")
        self.assertEqual(selection.json()["status"], "choose_stay", selection.text)
        self.assertEqual(len(selection.json()["stays"]), 5)
        self.assertEqual(self.calls, ["/case/2/getData"])
        with patch.object(SleepPlanner, "plan", autospec=True, side_effect=SleepPlanner.plan) as planner:
            responses = await asyncio.gather(*[
                self.start(n, access_code="show-code", stay_record_id="stay_1") for n in (1, 2, 1, 3, 4, 5)
            ])
            self.assertEqual([r.status_code for r in responses], [200] * 6)
            self.assertEqual(planner.call_count, 1)
        view = responses[0].json()["stay"]
        self.assertEqual(view["guestName"], stay["user_id"])
        self.assertEqual(view["wakeUp"]["time"], "07:00")
        self.assertEqual(len({c.room.session_id for c in self.service.clients.values()}), 5)
        self.assertEqual(self.calls.count("/runs"), 1)
        self.assertEqual(self.calls.count("/case/2/getData"), 1)
        self.assertEqual(self.hints.await_count, 1)
        before = list(self.calls)
        self.assert_same_stay((await self.start()).json(), responses[0].json())
        self.assertEqual(self.calls, before)
        self.assertEqual((await self.start(6, access_code="show-code", stay_record_id="stay_1")).json()
                         ["detail"]["code"], "demo_full")
        self.assertEqual(len(self.scenarios), 5)
        alarm = next(step for step in self.scenarios[0] if step["device_id"] == "alarm")
        self.assertEqual(datetime.fromisoformat(alarm["at"]), self.check_in)
        self.assertEqual(datetime.fromisoformat(alarm["values"]["at"]), self.check_in + timedelta(hours=16))

    async def test_selected_dataset_can_differ_from_the_dev_pool(self):
        guest_data = dataset_payload()
        guest_data["stays"][0]["record_id"] = "guest_stay"
        guest_data["requests"][0]["stay_id"] = "guest_stay"
        self.datasets["guest-dataset"] = guest_data
        response = await self.start(access_code="show-code", dataset_code="guest-dataset")
        self.assertEqual(response.json()["stay"]["stay_record_id"], "guest_stay")
        self.assertEqual(set(self.room_ids), {"stay_1"})
        self.assertEqual(self.calls.count("/case/2/getData"), 2)
        self.assertEqual((await self.start()).status_code, 409)

    async def test_invalid_inputs_allocate_no_room(self):
        response = await self.api.post("/api/v1/run_client", json={"dataset_code": "sample"})
        self.assertEqual(response.status_code, 401)
        response = await self.start(dataset_code="", access_code="show-code")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.calls, [])
        self.assertEqual((await self.start(access_code="show-code", dataset_code="missing")).status_code, 404)
        self.assertEqual((await self.start(stay_record_id="missing")).status_code, 404)
        self.assertFalse(self.room_ids)

    async def test_short_room_is_not_published(self):
        self.until = timedelta(hours=1)
        response = await self.start(access_code="show-code")
        self.assertEqual(response.json()["detail"]["code"], "demo_full")
        self.assertFalse(self.scenarios)


    async def test_five_final_clients_and_two_tabs_share_one_original_schedule(self):
        data = self.final_dataset()
        data["stays"].append(data["stays"][0] | {"record_id": "stay_2"})
        args = {"access_code": "show-code", "dataset_code": "final-code"}
        self.assertEqual((await self.start(**args)).json()["status"], "choose_stay")
        self.assertFalse(self.final_runs)
        with patch.object(SleepPlanner, "plan", autospec=True, side_effect=SleepPlanner.plan) as planner:
            responses = await asyncio.gather(*(self.start(n, stay_record_id="stay_1", **args)
                                               for n in (1, 1, 2, 3, 4, 5)))
        self.assertTrue(all(r.json()["status"] == "ready" for r in responses))
        self.assertEqual(planner.call_count, 1)
        self.assertEqual(self.hints.await_count, 1)
        self.assertEqual(self.calls.count("/case/2/getData"), 1)
        self.assertEqual(self.calls.count("/runs"), 5)
        self.assertEqual(self.calls.count("/room/session"), 5)
        self.assertEqual(len(self.final_runs), 5)
        self.assertEqual(set(self.room_ids), {(r, "stay_1") for r in self.final_runs})
        self.assertEqual(len(set(self.room_ids.values())), 5)
        self.assertEqual(len(self.scenarios), 5)
        steps = self.service.plans["final-code", "stay_1"].steps
        expected = [{"at": s.at.isoformat(), "device_id": s.device_id, "values": s.values} for s in steps]
        self.assertTrue(all(published == expected for published in self.scenarios))
        alarm = next(step for step in expected if step["device_id"] == "alarm")
        self.assertEqual(alarm["values"]["at"], data["stays"][0]["target_wake_at"])
        before = self.calls.copy()
        self.assert_same_stay((await self.start(dataset_code="final-code")).json(), responses[0].json())
        self.assertEqual(self.calls, before)

    async def test_final_retry_continues_from_confirmed_run_and_session(self):
        self.final_dataset()
        self.fail_session = True
        args = {"dataset_code": "final-code", "access_code": "show-code"}
        self.assertEqual((await self.start(**args)).status_code, 502)
        self.assertEqual((await self.start()).status_code, 409)
        self.fail_session = False
        self.fail_publish = True
        self.assertEqual((await self.start(**args)).status_code, 502)
        calls_after_failure = self.calls.count("/room/session")
        self.fail_publish = False
        self.assertEqual((await self.start(**args)).json()["status"], "ready")
        self.assertEqual(self.calls.count("/runs"), 1)
        self.assertEqual(self.calls.count("/room/session"), calls_after_failure)

    async def test_final_run_must_match_dataset(self):
        self.final_dataset()
        self.final_dataset_id = "unrelated"
        for _ in range(2):
            response = await self.start(dataset_code="final-code", access_code="show-code")
            self.assertEqual(response.json()["detail"]["code"], "run_dataset_mismatch")
        self.assertEqual(self.calls.count("/runs"), 1)
        self.assertNotIn("/room/session", self.calls)

    async def test_expired_dev_run_does_not_close_final_room(self):
        self.final_dataset()
        self.assertEqual((await self.start(access_code="show-code")).status_code, 200)
        dev = self.service.clients[f"{1:064x}"]
        dev.run = self.service.dev_run = replace(dev.run, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        self.assertEqual((await self.start(2, dataset_code="final-code", access_code="show-code")).status_code, 200)
        self.assertEqual((await self.start()).status_code, 410)
        self.assertEqual((await self.start(2, dataset_code="final-code")).status_code, 200)
        self.assertEqual((await self.api.get("/demo/room.js")).status_code, 200)
        final = self.service.clients[f"{2:064x}"]
        final.run = replace(final.run, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        self.assertEqual((await self.api.get("/demo/room.js")).status_code, 410)
        self.assertEqual((await self.start(2, dataset_code="final-code")).json()["detail"]["code"], "show_ended")

    async def test_final_stand_closes_pending_room(self):
        self.final_dataset()
        self.fail_publish = True
        args = {"dataset_code": "final-code", "access_code": "show-code"}
        self.assertEqual((await self.start(**args)).status_code, 502)
        self.closed_rooms.update(self.room_ids.values())
        self.assertEqual((await self.start(**args)).json()["detail"]["code"], "show_ended")
        self.assertEqual((await self.api.get("/demo/room.js")).status_code, 410)
        self.assertEqual(self.calls.count("/runs"), 1)

    async def test_settings_publish_once_preserve_other_periods_and_resume(self):
        data = self.final_dataset()
        for n in (1, 2):
            response = await self.start(n, dataset_code="final-code", access_code="show-code")
            self.assertEqual(response.status_code, 200, response.text)
        payload = {"revision": 0, "periods": {"night": {"temperature": 19}}, "alarm": {"time": "06:30"}}
        self.fail_publish = True
        self.assertEqual((await self.room_request("PATCH", "/stay/current", json=payload)).status_code, 502)
        self.assertEqual((await self.room_request("GET", "/stay/current")).json()["revision"], 0)
        self.fail_publish = False
        responses = await asyncio.gather(*(self.room_request("PATCH", "/stay/current", json=payload)
                                           for _ in range(2)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        result = next(r.json() for r in responses if r.status_code == 200)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["periods"][1]["temperature"], 19)
        self.assertEqual(result["wakeUp"]["time"], "06:30")
        self.assertEqual((await self.start(dataset_code="final-code")).json()["stay"]["revision"], 1)
        other = (await self.room_request("GET", "/stay/current", 2)).json()
        self.assertEqual(other["revision"], 0)
        self.assertEqual(other["wakeUp"]["time"], "07:00")
        self.assertEqual(result["periods"][2], other["periods"][2])
        self.assertEqual(len(self.scenarios), 3)
        client = self.service.clients[f"{1:064x}"]
        night = client.plan.boundaries[1]
        for at, expected in ((night - timedelta(seconds=1), 23), (night, 19), (client.plan.boundaries[2], 17.5)):
            state = await self.room_request("GET", "/room/state", params={"at": at.isoformat()})
            self.assertEqual(state.json()["state"]["climate"]["temperature_c"], expected)
            self.assertIn("06:30", state.json()["state"]["alarm"]["at"])
        self.assertEqual(data["stays"][0]["target_wake_at"][11:16], "07:00")

    async def test_clock_rewinds_and_snapshot_does_not_move_time(self):
        self.assertEqual((await self.room_request("GET", "/stay/current")).status_code, 401)
        self.assertEqual((await self.start(access_code="show-code")).status_code, 200)
        clock = (await self.room_request("GET", "/demo/clock")).json()
        start = datetime.fromisoformat(clock["min"])
        end = datetime.fromisoformat(clock["max"])
        target = start + timedelta(hours=2)
        paused = await self.room_request("PATCH", "/demo/clock", json={"at": target.isoformat(), "paused": True})
        self.assertEqual(paused.json()["now"], target.isoformat())
        self.assertTrue(paused.json()["paused"])
        snapshot = await self.room_request("GET", "/room/state", params={"at": end.isoformat()})
        self.assertEqual(snapshot.json()["at"], end.isoformat())
        self.assertIn("07:00", snapshot.json()["state"]["alarm"]["at"])
        self.assertEqual((await self.room_request("GET", "/demo/clock")).json(), paused.json())
        self.assertEqual((await self.room_request("PATCH", "/demo/clock",
                                                 json={"at": (end + timedelta(seconds=1)).isoformat()})).status_code, 422)
        rewind = await self.room_request("PATCH", "/demo/clock", json={"at": start.isoformat()})
        self.assertEqual(rewind.json()["now"], start.isoformat())
        self.assertTrue(rewind.json()["paused"])
        self.assertTrue((await self.room_request("PATCH", "/demo/clock", json={"at": end.isoformat()})).json()["paused"])
        resumed = await self.room_request("PATCH", "/demo/clock", json={"at": start.isoformat(), "paused": False})
        self.assertFalse(resumed.json()["paused"])

    async def test_invalid_settings_do_not_publish(self):
        await self.start(access_code="show-code")
        for payload in (
            {"periods": {"night": {"temperature": 99}}},
            {"periods": {"night": {"curtains": True}}},
            {"periods": {"unknown": {"temperature": 18}}},
            {"alarm": {"time": "12:00"}},
            {"alarm": {"track": "unknown", "enabled": False}},
        ):
            response = await self.room_request("PATCH", "/stay/current", json={"revision": 0, **payload})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(len(self.scenarios), 1)

    async def test_chat_live_and_scheduled_changes_use_server_time(self):
        self.final_dataset()
        for key in (1, 2):
            self.assertEqual((await self.start(key, dataset_code="final-code", access_code="show-code")).status_code, 200)
        client = self.service.clients[f"{1:064x}"]
        start, night, morning, _ = client.plan.boundaries
        at = start + timedelta(minutes=5)
        await self.room_request("PATCH", "/demo/clock", json={"at": at.isoformat(), "paused": True})
        interpreter = AsyncMock(side_effect=[
            NumericInterpretation(record_id="one", intent="cooler", slots=RelativeAmount(mode="relative", amount=1)),
            NumericInterpretation(record_id="two", intent="warmer", slots=AbsoluteAmount(mode="absolute", value=19)),
        ])
        self.service.ai.interpret_request = interpreter
        first = {"client_message_id": "one", "text": "сделай на градус прохладнее"}
        first_two = await asyncio.gather(*(self.room_request("POST", "/messages", json=first) for _ in range(2)))
        self.assertEqual(first_two[0].json(), first_two[1].json())
        self.assertEqual(first_two[0].json()["changes"], {"climate": {"temperature_c": 22}})
        self.assertEqual(self.calls.count(f"/room/{client.room.session_id}/action"), 1)
        self.assertEqual(self.scenarios[-1][0]["at"], start.isoformat())
        self.assertEqual(interpreter.await_args_list[0].args[0].request["at"], at.isoformat())
        view = (await self.room_request("GET", "/stay/current")).json()
        self.assertEqual(view["periods"][0]["temperature"], 22)
        self.assertEqual(view["revision"], 1)

        second = await self.room_request("POST", "/messages", json={
            "client_message_id": "two", "text": "ночью поставь 19 градусов",
        })
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["reply"]["text"], "Обновил настройки на ночь.")
        self.assertEqual(interpreter.await_args.args[0].request["at"], night.isoformat())
        self.assertEqual(len(interpreter.await_args.args[0].conversation), 2)
        self.assertEqual(self.calls.count(f"/room/{client.room.session_id}/action"), 1)
        self.assertEqual(len(self.scenarios), 3)
        for when, value in ((at, 22), (night - timedelta(seconds=1), 22), (night, 19), (morning, 17.5)):
            result = await self.room_request("GET", "/room/state", params={"at": when.isoformat()})
            self.assertEqual(result.json()["state"]["climate"]["temperature_c"], value)
        self.assertEqual((await self.room_request("GET", "/stay/current", 2)).json()["revision"], 0)

        latest = (await self.room_request("GET", "/messages", params={"limit": 2})).json()
        self.assertEqual([m["id"] for m in latest["items"]], ["two.user", "two.reply"])
        earlier = (await self.room_request("GET", "/messages", params={"before": latest["next_before"]})).json()
        self.assertEqual([m["id"] for m in earlier["items"]], ["one.user", "one.reply"])
        self.assertIsNone(earlier["next_before"])
        self.assertEqual((await self.room_request("GET", "/messages", 2)).json()["items"], [])
        conflict = await self.room_request("POST", "/messages", json=first | {"text": "другой текст"})
        self.assertEqual(conflict.status_code, 409)

    async def test_unconfirmed_future_chat_does_not_change_plan(self):
        await self.start(access_code="show-code")
        self.fail_publish = True
        self.service.ai.interpret_request = AsyncMock(return_value=NumericInterpretation(
            record_id="fail", intent="warmer", slots=AbsoluteAmount(mode="absolute", value=19),
        ))
        response = await self.room_request("POST", "/messages", json={
            "client_message_id": "fail", "text": "ночью поставь 19 градусов",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["changes"], {})
        self.assertEqual(response.json()["revision"], 0)
        self.assertIn("Не удалось подтвердить", response.json()["reply"]["text"])

    async def test_chat_partial_action_keeps_only_confirmed_fields(self):
        await self.start(access_code="show-code")
        client = self.service.clients[f"{1:064x}"]
        await self.room_request("PATCH", "/demo/clock", json={"at": client.clock.start.isoformat(), "paused": True})
        sid = str(client.room.session_id)
        for step in self.room_scenarios[sid]:
            if step["device_id"] == "sound":
                step["values"]["volume"] = 20
        self.failed_action_devices.add("sound")
        self.service.ai.interpret_request = AsyncMock(return_value=NoSlotsInterpretation(
            record_id="partial", intent="all_off", slots=EmptySlots(),
        ))
        response = await self.room_request("POST", "/messages", json={
            "client_message_id": "partial", "text": "выключи свет и звук",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["changes"], {"light": {"brightness": 0}})
        self.assertIn("Часть изменений", response.json()["reply"]["text"])
        self.assertEqual(client.revision, 1)
