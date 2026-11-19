"""Stage 1 tests with the real adapter and a synthetic Case2Dataset."""

import copy
import json
import unittest
from unittest.mock import AsyncMock
from uuid import UUID
from datetime import time, timedelta

import httpx
from pydantic import ValidationError

from concierge_service.domain.dataset import build_dataset_index
from concierge_service.domain.sleep import SleepPlanner
from concierge_service.infra.conversation_store import InMemoryConversationStore
from concierge_service.infra.hack_api import HackApiAdapter
from concierge_service.infra.mock_ai import MockAiAgent
from concierge_service.ports.room import RoomApi
from concierge_service.service.run_case import RunCase
from concierge_service.transport.http.app import create_app
from concierge_service.transport.http.schemas import Case2Answers


INTENT_IDS = (
    "brighter", "dimmer", "warmer", "cooler", "all_off", "sound_on",
    "quieter", "curtains", "alarm_set", "alarm_shift", "alarm_cancel",
    "going_to_bed", "woke_up", "like_yesterday", "do_not_disturb",
    "like_home", "out_of_scope", "invalid_value", "medical",
)


SESSION_ID = UUID("11111111-1111-1111-1111-111111111111")


def device_catalog_payload() -> list[dict]:
    device_fields = {
        "light": [
            ("brightness", {"type": "integer", "minimum": 0, "maximum": 100}, 0),
            ("color_temperature_k", {"type": "integer", "minimum": 2200, "maximum": 6500}, 4000),
        ],
        "climate": [
            ("temperature_c", {"type": "number", "minimum": 16, "maximum": 26}, 23),
        ],
        "sound": [
            ("track_id", {"type": "string"}, "track_1"),
            ("volume", {"type": "integer", "minimum": 0, "maximum": 100}, 20),
            ("timer_minutes", {"type": "integer", "minimum": 0, "maximum": 1440}, 0),
        ],
        "curtains": [
            ("position", {"type": "integer", "minimum": 0, "maximum": 100}, 100),
        ],
        "alarm": [
            ("at", {"type": ["string", "null"], "format": "date-time"}, None),
            ("mode", {"enum": ["light", "sound", "both"]}, "both"),
            ("window_minutes", {"type": "integer", "minimum": 0, "maximum": 30}, 0),
        ],
        "dnd": [
            ("enabled", {"type": "boolean"}, False),
        ],
    }
    return [
        {
            "device_id": device_id,
            "title": device_id,
            "fields": [
                {
                    "field_id": field_id,
                    "description": field_id,
                    "value_schema": schema,
                    "default_value": default,
                    "unit": None,
                }
                for field_id, schema, default in fields
            ],
        }
        for device_id, fields in device_fields.items()
    ]


def dataset_payload() -> dict:
    return {
        "meta": {
            "schema_version": "0.3.0",
            "dataset_id": "case2-test-v1",
            "case_id": 2,
            "phase": "dev",
            "version": "1",
            "generated_at": "2026-11-18T10:00:00+03:00",
            "history_through": "2026-11-11",
        },
        "users": [
            {
                "user_id": "user_1",
                "birth_date": None,
                "timezone": "Europe/Moscow",
                "goal": {"kind": "sleep", "description": "Наладить режим сна"},
                "chronotype": "early",
                "weekday_bedtime": "22:30",
                "weekday_wake_time": "07:00",
                "average_sleep_hours": None,
                "social_jetlag": None,
                "activity_level": None,
                "sedentary_hours": None,
                "training_days": None,
                "works_from_home": None,
                "phone_before_sleep": None,
                "movement_index": None,
                "sleep_preferences": {
                    "temperature": "cool",
                    "light_sensitive": False,
                    "favorite_track_ids": [],
                    "dislikes": None,
                },
                "disabled_widget_ids": None,
            }
        ],
        "events": [
            {
                "event_id": "event_1",
                "user_id": "user_1",
                "started_at": "2026-11-10T12:00:00+03:00",
                "ended_at": None,
                "type": "note",
                "source": "hotel",
                "payload": {"text": "Гость предпочитает прохладу"},
            }
        ],
        "chat_history": [
            {
                "message_id": "message_1",
                "user_id": "user_1",
                "role": "user",
                "at": "2026-11-10T11:00:00+03:00",
                "text": "Не люблю громкий будильник",
            }
        ],
        "stays": [
            {
                "record_id": "stay_1",
                "user_id": "user_1",
                "check_in_at": "2026-11-20T15:00:00+03:00",
                "target_wake_at": "2026-11-21T07:00:00+03:00",
                "hotel_timezone": "Europe/Moscow",
            }
        ],
        "requests": [
            {
                "record_id": "request_1",
                "user_id": "user_1",
                "stay_id": "stay_1",
                "at": "2026-11-20T22:00:00+03:00",
                "initial_state": {"light": {"brightness": 50}},
                "utterances": [
                    {
                        "role": "user",
                        "at": "2026-11-20T22:00:00+03:00",
                        "text": "Сделай свет ярче",
                    },
                    {
                        "role": "user",
                        "at": "2026-11-20T22:00:01+03:00",
                        "text": "Только чуть-чуть",
                    },
                ],
            }
        ],
        "devices": device_catalog_payload(),
        "tracks": [
            {"track_id": f"track_{index}", "title": f"Трек {index}"}
            for index in range(1, 13)
        ],
        "intents": [
            {"intent_id": intent_id, "description": intent_id}
            for intent_id in INTENT_IDS
        ],
        "sleep_rules": [
            {"rule_id": f"R{index}", "version": "1", "description": "Правило"}
            for index in range(1, 9)
        ],
    }


class Stage1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.payload = dataset_payload()
        self.reference = None
        self.get_data_requests: list[httpx.Request] = []
        self.upstream_response: httpx.Response | None = None
        self.connection_failed = False

        upstream = await self.enterAsyncContext(
            httpx.AsyncClient(transport=httpx.MockTransport(self._get_data))
        )
        self.adapter = HackApiAdapter(base_url="https://hack.test", client=upstream)
        room = AsyncMock(spec=RoomApi)
        room.create_session.return_value.session_id = SESSION_ID
        app = create_app(RunCase(
            self.adapter, room_api=room, sleep_planner=SleepPlanner(), ai_agent=MockAiAgent(),
            conversation_store_factory=InMemoryConversationStore,
        ))
        self.client = await self.enterAsyncContext(
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        )

    def _get_data(self, request: httpx.Request) -> httpx.Response:
        self.get_data_requests.append(request)
        if self.connection_failed:
            raise httpx.ConnectError("private upstream details", request=request)
        if self.upstream_response is not None:
            return self.upstream_response
        return httpx.Response(200, json={"dataset": self.payload, "reference": self.reference})

    async def _run(self, **body: object) -> httpx.Response:
        return await self.client.post(
            "/run", json={"case": 2, **body}, headers={"Authorization": "Bearer run-token"}
        )

    async def test_run_fetches_dataset_once_with_same_token_and_no_sensitive_logs(self) -> None:
        health = await self.client.get("/health")

        with self.assertLogs(level="INFO") as logs:
            response = await self._run(dataset_code="case2-test-v1")

        for marker in ("case2-test-v1", "run-token", "Сделай свет ярче", "initial_state"):
            self.assertNotIn(marker, "\n".join(logs.output))
        events = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual(len({event["run_id"] for event in events}), 1)
        self.assertEqual(events[0]["event"], "run_started")
        self.assertEqual(events[-1]["event"], "run_finished")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stays"], [
            {"record_id": "stay_1", "session_id": str(SESSION_ID)}
        ])
        request_answer, = response.json()["requests"]
        self.assertEqual(request_answer["record_id"], "request_1")
        self.assertEqual(
            [reply["response_class"] for reply in request_answer["replies"]], ["refused"]
        )
        request, = self.get_data_requests
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.url.path, "/case/2/getData")
        self.assertEqual(dict(request.url.params), {"dataset_code": "case2-test-v1"})
        self.assertEqual(request.headers["Authorization"], "Bearer run-token")

    async def test_run_omits_dataset_code_when_not_provided(self) -> None:
        response = await self._run()
        self.assertEqual(response.status_code, 200)
        request, = self.get_data_requests
        self.assertEqual(dict(request.url.params), {})

    async def test_run_accepts_final_dataset_without_requests(self) -> None:
        self.payload["meta"]["phase"] = "final"
        self.payload["requests"] = []
        self.assertEqual((await self._run()).status_code, 200)

    async def test_missing_and_malformed_bearer_are_rejected_before_loading(self) -> None:
        for header in (None, "Basic token", "Bearer ", "Bearer two tokens"):
            with self.subTest(header=header):
                headers = {} if header is None else {"Authorization": header}
                response = await self.client.post("/run", json={"case": 2}, headers=headers)
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.get_data_requests, [])

    async def test_unsupported_case_is_rejected_before_loading(self) -> None:
        response = await self._run(case=1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "unsupported_case")
        self.assertEqual(self.get_data_requests, [])

    async def test_adapter_preserves_utterance_times_profile_and_raw_source(self) -> None:
        self.reference = {"meta": {}, "stays": [], "requests": []}
        parsed = await self.adapter.get_case2_data(dataset_code=None, token="run-token")

        user, = parsed.dataset.users
        request, = parsed.dataset.requests
        self.assertEqual(user.timezone.key, "Europe/Moscow")
        self.assertEqual(user.weekday_bedtime, time(22, 30))
        self.assertIs(user.sleep_preferences.light_sensitive, False)
        self.assertEqual(user.sleep_preferences.favorite_track_ids, ())
        self.assertIsNone(user.sleep_preferences.dislikes)
        self.assertEqual(request.utterances[1].at - request.utterances[0].at, timedelta(seconds=1))
        self.assertEqual(request.at.utcoffset(), timedelta(hours=3))
        self.assertEqual(parsed.source.requests_by_id["request_1"], self.payload["requests"][0])
        self.assertEqual(parsed.source.users_by_id["user_1"], self.payload["users"][0])
        self.assertEqual(
            parsed.source.events_by_user_and_id["user_1", "event_1"], self.payload["events"][0]
        )
        self.assertEqual(
            parsed.source.messages_by_user_and_id["user_1", "message_1"],
            self.payload["chat_history"][0],
        )
        self.assertFalse(hasattr(parsed, "reference"))

    async def test_event_and_message_ids_are_scoped_to_the_user(self) -> None:
        self.payload["users"].append(dict(self.payload["users"][0], user_id="user_2"))
        self.payload["events"].append(dict(
            self.payload["events"][0], user_id="user_2", payload={"text": "another event"},
        ))
        self.payload["chat_history"].append(dict(
            self.payload["chat_history"][0], user_id="user_2", text="another message",
        ))
        parsed = await self.adapter.get_case2_data(dataset_code=None, token="run-token")
        index = build_dataset_index(parsed.dataset)

        for position, user_id in enumerate(("user_1", "user_2")):
            self.assertEqual(index.events_by_user[user_id][0].event_id, "event_1")
            self.assertEqual(index.chat_history_by_user[user_id][0].message_id, "message_1")
            self.assertEqual(
                parsed.source.events_by_user_and_id[user_id, "event_1"],
                self.payload["events"][position],
            )
            self.assertEqual(
                parsed.source.messages_by_user_and_id[user_id, "message_1"],
                self.payload["chat_history"][position],
            )

        self.payload["chat_history"].append(copy.deepcopy(self.payload["chat_history"][0]))
        self.assertEqual((await self._run()).status_code, 502)

    async def test_index_orders_user_records_by_instant_and_preserves_ties(self) -> None:
        earlier = copy.deepcopy(self.payload["requests"][0])
        earlier.update(record_id="request_0", at="2026-11-20T23:00:00+05:00")
        tied = copy.deepcopy(self.payload["requests"][0])
        tied.update(record_id="a_tie", at="2026-11-20T19:00:00+00:00")
        self.payload["requests"].extend([earlier, tied])
        self.payload["events"].append(dict(
            self.payload["events"][0], event_id="event_0", started_at="2026-11-09T12:00:00+03:00"
        ))
        self.payload["chat_history"].append(dict(
            self.payload["chat_history"][0], message_id="message_0", at="2026-11-09T12:00:00+03:00"
        ))

        parsed = await self.adapter.get_case2_data(dataset_code=None, token="run-token")
        index = build_dataset_index(parsed.dataset)

        self.assertEqual(
            [record.record_id for record in index.requests_by_user["user_1"]],
            ["request_0", "request_1", "a_tie"],
        )
        self.assertEqual([record.record_id for record in index.stays_by_user["user_1"]], ["stay_1"])
        self.assertEqual(
            [event.event_id for event in index.events_by_user["user_1"]], ["event_0", "event_1"]
        )
        self.assertEqual(
            [message.message_id for message in index.chat_history_by_user["user_1"]],
            ["message_0", "message_1"],
        )
        self.assertEqual(index.device_fields_by_device["light"]["brightness"].default_value, 0)

    async def test_invalid_wire_dataset_is_rejected(self) -> None:
        cases = [
            ("stays", "check_in_at", "2026-11-20T15:00:00"),
            ("stays", "check_in_at", "2026-11-20T15:00:00Z"),
            ("stays", "check_in_at", "2026-11-20T15:00:00+00:60"),
            ("stays", "check_in_at", "2026-02-30T15:00:00+03:00"),
            ("stays", "hotel_timezone", "Unknown/Zone"),
            ("requests", "initial_state", {"light": {}}),
            ("requests", "initial_state", {"light": {"brightness": []}}),
            ("requests", "utterances", ["Сделай свет ярче"]),
            ("requests", "utterances", []),
            ("events", "payload", {"bpm": 65}),
        ]
        for collection, field, value in cases:
            with self.subTest(collection=collection, field=field, value=value):
                self.payload = dataset_payload()
                self.payload[collection][0][field] = value
                response = await self._run()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()["detail"]["code"], "invalid_dataset")

    async def test_required_fields_and_catalog_lengths_match_wire_contract(self) -> None:
        for field in ("birth_date", "weekday_bedtime", "sleep_preferences"):
            with self.subTest(field=field):
                self.payload = dataset_payload()
                del self.payload["users"][0][field]
                self.assertEqual((await self._run()).status_code, 502)
        for field in ("stays", "tracks", "intents", "sleep_rules"):
            with self.subTest(field=field):
                self.payload = dataset_payload()
                self.payload[field].pop()
                self.assertEqual((await self._run()).status_code, 502)

    async def test_link_and_identity_errors_are_rejected(self) -> None:
        requests = [
            {"stay_id": "missing"},
            {"initial_state": {"missing": {"brightness": 1}}},
            {"initial_state": {"light": {"missing": 1}}},
        ]
        for changes in requests:
            with self.subTest(changes=changes):
                self.payload = dataset_payload()
                self.payload["requests"][0].update(changes)
                response = await self._run()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()["detail"]["code"], "invalid_dataset")

        self.payload = dataset_payload()
        self.payload["users"].append(dict(self.payload["users"][0], user_id="user_2"))
        self.payload["requests"][0]["user_id"] = "user_2"
        self.assertEqual((await self._run()).status_code, 502)

        self.payload = dataset_payload()
        self.payload["users"].append(copy.deepcopy(self.payload["users"][0]))
        self.assertEqual((await self._run()).status_code, 502)

    async def test_upstream_contract_and_error_codes_are_sanitized(self) -> None:
        for status, expected_status, code in [
            (302, 502, "upstream_error"),
            (401, 401, "unauthorized"),
            (403, 403, "forbidden"),
            (404, 404, "dataset_not_found"),
            (500, 503, "upstream_error"),
        ]:
            with self.subTest(status=status):
                self.upstream_response = httpx.Response(status, json={
                    "dataset": self.payload, "reference": None, "code": "private details"
                })
                response = await self._run()
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json(), {"detail": {"code": code}})

    async def test_invalid_json_and_unavailable_upstream(self) -> None:
        for upstream_response in (
            httpx.Response(200, content=b"not JSON"),
            httpx.Response(200, json={"dataset": self.payload}),
        ):
            self.upstream_response = upstream_response
            response = await self._run()
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["detail"]["code"], "invalid_response")

        self.connection_failed = True
        response = await self._run()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "unavailable")


class Case2AnswersTests(unittest.TestCase):
    def test_requires_shape_and_valid_explicit_reply_offset(self) -> None:
        answer = {
            "stays": [],
            "requests": [{
                "record_id": "request_1",
                "session_id": "session_1",
                "replies": [{
                    "role": "agent",
                    "at": "2026-11-20T22:00:00+03:00",
                    "text": "Увеличил яркость.",
                    "response_class": "done",
                }],
            }],
        }
        self.assertEqual(Case2Answers.model_validate(answer).model_dump(), answer)

        answer["requests"][0]["replies"][0]["at"] = "2026-11-20T22:00:00+00:60"
        with self.assertRaises(ValidationError):
            Case2Answers.model_validate(answer)


if __name__ == "__main__":
    unittest.main()
