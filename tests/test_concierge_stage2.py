"""Sleep rules and the Room API contract for stage 2."""

import json
import unittest
from dataclasses import replace
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from concierge_service.domain.dataset import DeviceField, SleepPreferences, Stay, User
from concierge_service.domain.scenario import ScenarioStep
from concierge_service.domain.sleep import (
    SleepPlanError,
    SleepPlanner,
    resolve_bedtime,
)
from concierge_service.infra.hack_api import HackApiAdapter
from concierge_service.infra.mock_ai import MockAiAgent
from concierge_service.infra.room import RoomApiAdapter
from concierge_service.ports.room import RoomApiError
from concierge_service.service.preference_resolver import resolve_preferences
from concierge_service.service.run_case import RunCase
from concierge_service.transport.http.app import create_app
from test_concierge_stage1 import SESSION_ID, dataset_payload, device_catalog_payload

HOTEL = ZoneInfo("Europe/Moscow")
CHECK_IN = "2026-11-20T15:00:00+03:00"
WAKE = "2026-11-21T07:00:00+03:00"
ROOM_ID = UUID("22222222-2222-2222-2222-222222222222")
RUN_ID = UUID("33333333-3333-3333-3333-333333333333")

def catalog() -> dict[str, dict[str, DeviceField]]:
    return {
        device["device_id"]: {field["field_id"]: DeviceField(**field) for field in device["fields"]}
        for device in device_catalog_payload()
    }


class SleepPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stay = Stay(
            "stay_1", "user_1", datetime.fromisoformat(CHECK_IN), datetime.fromisoformat(WAKE), HOTEL
        )
        self.user = User(
            "user_1",
            HOTEL,
            "early",
            time(23),
            time(7),
            SleepPreferences("cool", True, ("missing", "rain", "wind"), ("резкие звуки",)),
        )
        self.preferences = resolve_preferences(self.user, {"rain", "wind"})
        self.devices = catalog()
        self.planner = SleepPlanner()

    def test_night_signals_obey_r1_to_r7(self) -> None:
        steps = self.planner.plan(self.stay, self.preferences, self.devices)
        states = {(step.at.isoformat(), step.device_id): step.values for step in steps}
        day = "2026-11-20"
        next_day = "2026-11-21"
        self.assertEqual(states[(f"{day}T22:00:00+03:00", "light")], {"brightness": 10, "color_temperature_k": 2700})
        self.assertEqual(states[(f"{day}T23:00:00+03:00", "light")], {"brightness": 0})
        self.assertEqual(states[(f"{day}T23:00:00+03:00", "curtains")], {"position": 0})
        self.assertEqual(states[(f"{day}T23:00:00+03:00", "climate")], {"temperature_c": 17.5})
        self.assertEqual(
            states[(f"{day}T23:00:00+03:00", "sound")],
            {"track_id": "rain", "volume": 20, "timer_minutes": 30},
        )
        self.assertEqual(states[(f"{day}T23:30:00+03:00", "sound")], {"volume": 0})
        self.assertEqual(states[(CHECK_IN, "alarm")], {"at": WAKE, "mode": "light", "window_minutes": 0})
        self.assertEqual(states[(f"{next_day}T06:35:00+03:00", "curtains")], {"position": 100})
        self.assertEqual(states[(f"{day}T23:00:00+03:00", "dnd")], {"enabled": True})
        self.assertTrue(all(self.stay.check_in_at <= step.at <= self.stay.target_wake_at for step in steps))
        self.assertEqual([step.at for step in steps], sorted(step.at for step in steps))

    def test_late_check_in_shrinks_r1_without_moving_bedtime(self) -> None:
        stay = replace(self.stay, check_in_at=datetime.fromisoformat("2026-11-20T22:50:00+03:00"))
        steps = self.planner.plan(stay, self.preferences, self.devices)
        light = [(step.at.isoformat(), step.values["brightness"]) for step in steps if step.device_id == "light"]
        self.assertEqual(light, [("2026-11-20T22:50:00+03:00", 10), ("2026-11-20T23:00:00+03:00", 0)])

    def test_check_in_after_bedtime_applies_night_without_restarting_timer(self) -> None:
        stay = replace(self.stay, check_in_at=datetime.fromisoformat("2026-11-20T23:20:00+03:00"))
        steps = self.planner.plan(stay, self.preferences, self.devices)
        lights = [step for step in steps if step.device_id == "light"]
        self.assertEqual(lights, [ScenarioStep(stay.check_in_at, "light", {"brightness": 0})])
        audio = [step for step in steps if step.device_id == "sound"]
        self.assertEqual(audio[0].at, stay.check_in_at)
        self.assertEqual(audio[0].values["timer_minutes"], 10)
        self.assertEqual(audio[1].at.isoformat(), "2026-11-20T23:30:00+03:00")

    def test_r4_turns_sound_off_when_there_are_no_favorite_tracks(self) -> None:
        preferences = replace(self.preferences, favorite_track_ids=())
        steps = self.planner.plan(self.stay, preferences, self.devices)
        audio = [step.values for step in steps if step.device_id == "sound"]
        self.assertEqual(audio, [{"volume": 0}])

    def test_r8_moves_home_bedtime_toward_hotel_clock(self) -> None:
        for zone, expected in (
            ("UTC", "2026-11-21T01:00:00+03:00"),
            ("Asia/Omsk", "2026-11-20T21:00:00+03:00"),
            ("Africa/Johannesburg", "2026-11-21T00:00:00+03:00"),
        ):
            with self.subTest(zone=zone):
                preferences = replace(self.preferences, home_timezone=ZoneInfo(zone))
                bedtime = resolve_bedtime(self.stay, preferences).astimezone(HOTEL)
                self.assertEqual(bedtime.isoformat(), expected)

    def test_dawn_uses_elapsed_minutes_across_dst(self) -> None:
        zone = ZoneInfo("Europe/Berlin")
        stay = replace(
            self.stay,
            hotel_timezone=zone,
            check_in_at=datetime.fromisoformat("2026-03-28T21:00:00+01:00"),
            target_wake_at=datetime.fromisoformat("2026-03-29T03:10:00+02:00"),
        )
        prefs = replace(self.preferences, home_timezone=zone)
        steps = self.planner.plan(stay, prefs, self.devices)
        dawn, = (step for step in steps if step.device_id == "curtains" and step.values["position"] == 100)
        self.assertEqual(dawn.at.isoformat(), "2026-03-29T01:45:00+01:00")

    def test_rejects_unsupported_field_and_values_before_contacting_room(self) -> None:
        original = self.devices["light"].pop("color_temperature_k")
        with self.assertRaisesRegex(SleepPlanError, "unknown device or field"):
            self.planner.plan(self.stay, self.preferences, self.devices)

        self.devices["light"]["color_temperature_k"] = replace(original, value_schema={"minimum": 3000})
        with self.assertRaisesRegex(SleepPlanError, "outside device field schema"):
            self.planner.plan(self.stay, self.preferences, self.devices)

    def test_profile_defaults(self) -> None:
        preference = resolve_preferences(replace(self.user, weekday_bedtime=None, sleep_preferences=None), ())
        self.assertEqual(preference.bedtime, time(22))
        self.assertEqual(preference.night_temperature, 19.5)
        self.assertEqual(preference.favorite_track_ids, ())
        self.assertFalse(preference.gentle_alarm)


def session_payload(record_id: str = "stay_1") -> dict:
    return {
        "session_id": str(SESSION_ID),
        "room_id": str(ROOM_ID),
        "run_id": str(RUN_ID),
        "team_id": "team-test",
        "dataset_id": "case2-test-v1",
        "record_id": record_id,
        "stay_id": record_id,
        "kind": "stay",
        "from": CHECK_IN,
        "until": WAKE,
        "at": CHECK_IN,
        "initial_state": {"light": {"brightness": 70}},
    }


def snapshot_payload() -> dict:
    return {
        "session_id": str(SESSION_ID),
        "room_id": str(ROOM_ID),
        "at": WAKE,
        "state": {"light": {"brightness": 0}},
    }


class RoomApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_room_lifecycle_uses_run_token_without_run_id(self) -> None:
        requests: list[httpx.Request] = []
        steps = [{"at": WAKE, "device_id": "light", "values": {"brightness": 0}}]

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/room/session":
                return httpx.Response(200, json=session_payload())
            if request.url.path.endswith("/log"):
                return httpx.Response(200, json={
                    "session": session_payload(),
                    "closed_at": None,
                    "entries": [{
                        "revision": 1, "recorded_at": CHECK_IN, "kind": "scenario", "steps": steps,
                    }],
                    "final_state": snapshot_payload()["state"],
                })
            return httpx.Response(200, json=snapshot_payload())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = RoomApiAdapter(base_url="https://hack.test", client=client)
            token = "synthetic-run-token"
            session = await api.create_session(record_id="stay_1", kind="stay", token=token)
            at = datetime.fromisoformat(WAKE)
            step = ScenarioStep(at, "light", {"brightness": 0})
            snapshot = await api.load_scenario(session_id=session.session_id, steps=(step,), token=token)
            state = await api.get_state(session_id=session.session_id, token=token, at=at)
            log = await api.get_log(session_id=session.session_id, token=token)

        self.assertEqual(session.run_id, RUN_ID)
        self.assertEqual(state, snapshot)
        self.assertEqual(log.entries[0].steps, (step,))
        self.assertEqual(
            [(request.method, request.url.path) for request in requests],
            [
                ("POST", "/room/session"),
                ("POST", f"/room/{SESSION_ID}/scenario"),
                ("GET", f"/room/{SESSION_ID}/state"),
                ("GET", f"/room/{SESSION_ID}/log"),
            ],
        )
        self.assertEqual(json.loads(requests[0].content), {"record_id": "stay_1", "kind": "stay"})
        self.assertEqual(json.loads(requests[1].content), {"steps": steps})
        self.assertEqual(dict(requests[2].url.params), {"at": WAKE})
        self.assertTrue(all(request.headers["Authorization"] == f"Bearer {token}" for request in requests))

    async def test_rejects_mismatched_sessions_and_sanitizes_upstream_errors(self) -> None:
        responses = iter([
            httpx.Response(200, json=session_payload("stay_other")),
            httpx.Response(403, json={"code": "private upstream message"}),
        ])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(responses))) as client:
            api = RoomApiAdapter(base_url="https://hack.test", client=client)
            with self.assertRaisesRegex(RoomApiError, "^session does not match the record$"):
                await api.create_session(record_id="stay_1", kind="stay", token="token")
            with self.assertRaisesRegex(RoomApiError, "^simulator returned HTTP 403$"):
                await api.get_log(session_id=SESSION_ID, token="token")


class ScenarioRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_loads_each_stay_and_reports_only_successful_scenarios(self) -> None:
        payload = dataset_payload()
        payload["stays"].append(dict(payload["stays"][0], record_id="stay_2"))
        failed_session = UUID("44444444-4444-4444-4444-444444444444")
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/case/2/getData":
                return httpx.Response(200, json={"dataset": payload, "reference": None})
            if request.url.path == "/room/session":
                body = json.loads(request.content)
                self.assertEqual(body, {"record_id": body["record_id"], "kind": "stay"})
                session = session_payload(body["record_id"])
                if body["record_id"] == "stay_1":
                    session["session_id"] = str(failed_session)
                return httpx.Response(200, json=session)
            if request.url.path == f"/room/{failed_session}/scenario":
                return httpx.Response(400, json={"message": "private upstream data"})
            if request.url.path == f"/room/{SESSION_ID}/scenario":
                self.assertTrue(json.loads(request.content)["steps"])
                return httpx.Response(200, json=snapshot_payload())
            self.fail(f"unexpected endpoint {request.url.path}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            app = create_app(RunCase(
                HackApiAdapter(base_url="https://hack.test", client=upstream),
                room_api=RoomApiAdapter(base_url="https://hack.test", client=upstream),
                sleep_planner=SleepPlanner(),
                ai_agent=MockAiAgent(),
            ))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                with self.assertLogs("concierge_service.service.run_case", level="WARNING") as logs:
                    response = await client.post(
                        "/run", json={"case": 2, "dataset_code": "case2-test-v1"},
                        headers={"Authorization": "Bearer synthetic-token"},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "stays": [{"record_id": "stay_2", "session_id": str(SESSION_ID)}],
            "requests": [],
        })
        self.assertEqual(
            [item.url.path for item in requests],
            [
                "/case/2/getData", "/room/session", f"/room/{failed_session}/scenario",
                "/room/session", f"/room/{SESSION_ID}/scenario",
            ],
        )
        self.assertTrue(
            all(item.headers["Authorization"] == "Bearer synthetic-token" for item in requests)
        )
        self.assertEqual(logs.records[0].getMessage(), "Stay scenario skipped: simulator returned HTTP 400")
