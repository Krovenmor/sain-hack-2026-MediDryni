"""Concierge preference enrichment through the AiAgent port."""

import asyncio
import copy
import unittest
from dataclasses import replace
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import httpx

from concierge_service.domain.dataset import SleepPreferences, User
from concierge_service.domain.sleep import SleepPlanner
from concierge_service.infra.hack_api import HackApiAdapter
from concierge_service.ports.ai import AiAgent, AiAgentError, PreferenceFailure, PreferenceHints
from concierge_service.ports.room import RoomApi
from concierge_service.service.preference_resolver import resolve_preferences
from concierge_service.service.run_case import RunCase
from test_concierge_stage1 import dataset_payload


def preference_hints(user_id: str, **values: object) -> PreferenceHints:
    payload = dict.fromkeys((
        "temperature", "light_sensitive", "favorite_track_ids", "gentle_alarm",
        "weekday_bedtime", "weekday_wake_time", "home_timezone",
    ))
    payload.update({
        key: {"value": value, "sources": [{"kind": "event", "source_id": "event_1"}]}
        for key, value in values.items()
    })
    return PreferenceHints.model_validate(dict(payload, user_id=user_id, settings=[]))


class PreferenceResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            "user_1", ZoneInfo("Europe/Moscow"), "late", time(0), time(7),
            SleepPreferences("cool", False, (), ("резкие звуки",)),
        )

    def test_profile_keeps_explicit_false_empty_tracks_and_midnight(self) -> None:
        hints = preference_hints(
            self.user.user_id, temperature="warm", light_sensitive=True,
            favorite_track_ids=["track_1"], gentle_alarm=False, weekday_bedtime="21:30",
            home_timezone="Europe/Berlin",
        )
        preferences = resolve_preferences(self.user, {"track_1"}, hints)

        self.assertEqual(preferences.night_temperature, 17.5)
        self.assertFalse(preferences.light_sensitive)
        self.assertEqual(preferences.favorite_track_ids, ())
        self.assertTrue(preferences.gentle_alarm)
        self.assertEqual(preferences.bedtime, time(0))
        self.assertEqual(preferences.home_timezone, ZoneInfo("Europe/Moscow"))

    def test_hints_fill_missing_fields_before_defaults(self) -> None:
        user = replace(self.user, timezone=None, weekday_bedtime=None, sleep_preferences=None)
        hints = preference_hints(
            user.user_id, temperature="warm", light_sensitive=True,
            favorite_track_ids=["track_1"], gentle_alarm=True, weekday_bedtime="21:30",
            home_timezone="Europe/Berlin",
        )
        preferences = resolve_preferences(user, {"track_1"}, hints)

        self.assertEqual(preferences.night_temperature, 21.5)
        self.assertTrue(preferences.light_sensitive)
        self.assertEqual(preferences.favorite_track_ids, ("track_1",))
        self.assertTrue(preferences.gentle_alarm)
        self.assertEqual(preferences.bedtime, time(21, 30))
        self.assertEqual(preferences.home_timezone, ZoneInfo("Europe/Berlin"))

    def test_empty_hints_keep_profile_and_defaults(self) -> None:
        self.assertEqual(
            resolve_preferences(self.user, {"track_1"}, preference_hints(self.user.user_id)),
            resolve_preferences(self.user, {"track_1"}),
        )


class ConciergePreferenceFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.dataset = dataset_payload()
        self.dataset["users"][0]["sleep_preferences"] = None
        self.dataset["users"].append(dict(
            self.dataset["users"][0], user_id="user_2",
            sleep_preferences={
                "temperature": "cool", "light_sensitive": False,
                "favorite_track_ids": [], "dislikes": [],
            },
        ))
        self.dataset["stays"].extend([
            dict(self.dataset["stays"][0], record_id="stay_2"),
            dict(self.dataset["stays"][0], record_id="stay_3", user_id="user_2"),
        ])
        for collection, id_field, time_field in (
            ("events", "event_id", "started_at"), ("chat_history", "message_id", "at"),
        ):
            first = self.dataset[collection][0]
            earlier = dict(first, **{id_field: "earlier", time_field: "2026-11-09T09:00:00+00:00"})
            self.dataset[collection].extend([dict(first, user_id="user_2"), earlier])
        self.original_input = copy.deepcopy(self.dataset)

        upstream = await self.enterAsyncContext(httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={
                "dataset": self.dataset,
                "reference": {"private_dev_reference": True},
            })
        )))
        self.hack_api = HackApiAdapter(base_url="https://hack.test", client=upstream)
        self.room_api = AsyncMock(spec=RoomApi)
        self.room_api.create_session.side_effect = lambda **args: SimpleNamespace(
            session_id=uuid5(NAMESPACE_URL, args["record_id"])
        )
        self.agent = AsyncMock(spec=AiAgent)

    async def _run(self, *, timeout_s: float = 60.0) -> None:
        runner = RunCase(
            self.hack_api, room_api=self.room_api, sleep_planner=SleepPlanner(),
            ai_agent=self.agent, preference_timeout_s=timeout_s,
        )
        result = await runner.run(case=2, dataset_code=None, token="synthetic-hack-token")
        self.assertEqual([answer.record_id for answer in result.stays], ["stay_1", "stay_2", "stay_3"])
        self.assertEqual(result.requests, ())
        self.agent.interpret_request.assert_not_awaited()

    def _temperatures(self) -> list[float]:
        return [
            next(step.values["temperature_c"] for step in call.kwargs["steps"] if step.device_id == "climate")
            for call in self.room_api.load_scenario.await_args_list
        ]

    async def test_one_batch_with_isolated_raw_profiles_is_reused_across_stays(self) -> None:
        self.agent.extract_preference_hints.return_value = [
            preference_hints("user_1", temperature="warm", favorite_track_ids=["track_1"]),
            preference_hints("user_2", temperature="warm", favorite_track_ids=["track_1"]),
        ]

        await self._run()

        self.agent.extract_preference_hints.assert_awaited_once()
        call = self.agent.extract_preference_hints.await_args
        self.assertEqual(call.kwargs, {"timeout_s": 60.0})
        first, second = call.args[0]
        self.assertEqual(first.user, self.original_input["users"][0])
        self.assertEqual(second.user, self.original_input["users"][1])
        for profile in (first, second):
            self.assertEqual(profile.devices, tuple(self.original_input["devices"]))
            self.assertEqual(profile.tracks, tuple(self.original_input["tracks"]))
        for name, id_field in (("events", "event_id"), ("chat_history", "message_id")):
            self.assertEqual(
                getattr(first, name), (self.original_input[name][2], self.original_input[name][0])
            )
            self.assertEqual(getattr(second, name), (self.original_input[name][1],))
            self.assertEqual(getattr(first, name)[0][id_field], "earlier")
        self.assertEqual(self._temperatures(), [21.5, 21.5, 17.5])
        for call in self.room_api.load_scenario.await_args_list[:2]:
            self.assertTrue(any(step.values.get("track_id") == "track_1" for step in call.kwargs["steps"]))
        self.assertFalse(any(
            step.values.get("track_id") for step in self.room_api.load_scenario.await_args.kwargs["steps"]
        ))

    async def test_individual_failure_preserves_other_users_hints(self) -> None:
        self.dataset["users"][1]["sleep_preferences"] = None
        self.agent.extract_preference_hints.return_value = [
            PreferenceFailure(user_id="user_1", error="invalid_response"),
            preference_hints("user_2", temperature="warm"),
        ]
        await self._run()
        self.assertEqual(self._temperatures(), [19.5, 19.5, 21.5])

    async def test_adapter_reported_invalid_response_falls_back_without_retry(self) -> None:
        self.agent.extract_preference_hints.side_effect = AiAgentError("invalid_response")

        with self.assertLogs("concierge_service.service.run_case", level="WARNING"):
            await self._run()

        self.agent.extract_preference_hints.assert_awaited_once()
        self.assertEqual(self._temperatures(), [19.5, 19.5, 17.5])

    async def test_mismatched_batch_cannot_supply_hints_to_a_different_user(self) -> None:
        self.agent.extract_preference_hints.return_value = [
            preference_hints("user_2", temperature="warm"),
            preference_hints("user_1", temperature="warm"),
        ]

        with self.assertLogs("concierge_service.service.run_case", level="WARNING"):
            await self._run()

        self.assertEqual(self._temperatures(), [19.5, 19.5, 17.5])

    async def test_timeout_cancels_agent_and_keeps_scenarios(self) -> None:
        cancelled = asyncio.Event()

        async def wait(*args, **kwargs):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        self.agent.extract_preference_hints.side_effect = wait
        with self.assertLogs("concierge_service.service.run_case", level="WARNING"):
            await self._run(timeout_s=0.01)

        self.agent.extract_preference_hints.assert_awaited_once()
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self._temperatures(), [19.5, 19.5, 17.5])

    async def test_agent_budget_is_capped_by_remaining_run_time(self) -> None:
        self.agent.extract_preference_hints.return_value = [
            preference_hints("user_1"), preference_hints("user_2")
        ]

        with patch("concierge_service.service.run_case._RUN_BUDGET_S", 10):
            await self._run()

        call = self.agent.extract_preference_hints.await_args
        self.assertGreater(call.kwargs["timeout_s"], 0)
        self.assertLess(call.kwargs["timeout_s"], 10)
