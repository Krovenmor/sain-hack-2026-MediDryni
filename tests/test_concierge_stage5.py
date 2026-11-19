"""Stage 5 requests and real-adapter configuration."""

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai_service.service.agent import LlmAgent
from concierge_service.di.config import Settings
from concierge_service.di.container import build_container
from concierge_service.domain.action_planner import ActionPlanError, DevicePatch
from concierge_service.domain.dataset import Request, Utterance
from concierge_service.domain.sleep import ResolvedPreferences
from concierge_service.ports.ai import (
    REQUEST_INTERPRETATION_ADAPTER,
    AiAgent,
    ObservedSetting,
    ProfileContext,
    RequestContext,
    SourceRef,
)
from concierge_service.ports.room import RoomApi
from concierge_service.service.request_handler import RequestHandler
from concierge_service.service.request_planning import plan_request
from test_concierge_stage1 import SESSION_ID
from test_concierge_stage2 import catalog
from test_concierge_stage3 import preference_hints

AT = datetime.fromisoformat("2026-11-20T22:00:00+03:00")
PREFERENCES = ResolvedPreferences(None, time(23), 17.5, False, ("track_1",), True)
STATE = {
    "light": {"brightness": 30, "color_temperature_k": 4500},
    "climate": {"temperature_c": 21},
    "sound": {"track_id": "track_2", "volume": 0, "timer_minutes": 15},
    "curtains": {"position": 40},
    "alarm": {"at": "2026-11-21T08:00:00+03:00", "mode": "light", "window_minutes": 5},
    "dnd": {"enabled": True},
}


def interpretation(intent, **slots):
    return REQUEST_INTERPRETATION_ADAPTER.validate_python({
        "record_id": "request_1", "intent": intent, "slots": slots,
    })


def observation(device, field, value, at, *, scope="past_stay"):
    return ObservedSetting(
        context=scope, observed_at=at, device_id=device, field_id=field, value=value,
        sources=[SourceRef(kind="event", source_id="event_1")],
    )


class ExtendedActionTests(unittest.TestCase):
    def setUp(self):
        self.request = Request(
            "request_1", "user_1", "stay_1", AT, STATE, (Utterance(AT, "просьба"),)
        )
        self.context = RequestContext(
            profile=ProfileContext(
                user={"user_id": "user_1"}, events=(), chat_history=(), devices=(), tracks=(),
            ),
            request={"record_id": "request_1"}, stay={"hotel_timezone": "Europe/Moscow"},
            intents=(), conversation=(), preference_hints=None,
        )

    def _plan(self, intent, **slots):
        return plan_request(
            self.request, interpretation(intent, **slots), self.context, PREFERENCES, catalog()
        )

    def test_alarm_preserves_unspecified_mode_and_window(self):
        self.assertEqual(
            self._plan("alarm_set", target_at="2026-11-21T06:30:00+03:00"),
            (DevicePatch("alarm", {"at": "2026-11-21T06:30:00+03:00"}),),
        )
        self.assertEqual(
            self._plan("alarm_shift", mode="relative", minutes=-15, alarm_mode="sound", window_minutes=0),
            (DevicePatch("alarm", {
                "at": "2026-11-21T07:45:00+03:00", "mode": "sound", "window_minutes": 0,
            }),),
        )
        self.assertEqual(self._plan("alarm_cancel"), (DevicePatch("alarm", {"at": None}),))

    def test_alarm_rejects_past_time_and_shift_without_current_alarm(self):
        with self.assertRaisesRegex(ActionPlanError, "^invalid_value$"):
            self._plan("alarm_set", target_at="2026-11-20T21:00:00+03:00")
        self.request = replace(self.request, initial_state={"alarm": {"at": None}})
        with self.assertRaisesRegex(ActionPlanError, "^missing_state$"):
            self._plan("alarm_shift", mode="relative", minutes=15)

    def test_alarm_shift_crosses_dst_as_elapsed_minutes(self):
        self.context = replace(self.context, stay={"hotel_timezone": "Europe/Berlin"})
        self.request = replace(
            self.request,
            at=datetime.fromisoformat("2026-03-28T22:00:00+01:00"),
            initial_state={"alarm": {"at": "2026-03-29T01:50:00+01:00"}},
        )
        self.assertEqual(
            self._plan("alarm_shift", mode="relative", minutes=20),
            (DevicePatch("alarm", {"at": "2026-03-29T03:10:00+02:00"}),),
        )

    def test_sound_uses_favorite_track_but_preserves_unspecified_timer(self):
        self.assertEqual(
            self._plan("sound_on", track_id=None, volume=None, timer_minutes=None),
            (DevicePatch("sound", {"track_id": "track_1", "volume": 20}),),
        )
        self.assertEqual(
            self._plan("sound_on", track_id="track_2", volume=30, timer_minutes=0),
            (DevicePatch("sound", {"track_id": "track_2", "volume": 30, "timer_minutes": 0}),),
        )
        self.assertEqual(STATE["sound"]["timer_minutes"], 15)

    def test_sound_falls_back_to_confirmed_bedtime_track_when_no_favorite_is_known(self):
        hints = preference_hints("user_1").model_copy(update={"settings": [
            observation("sound", "track_id", "track_1", None, scope="bedtime"),
            observation("sound", "timer_minutes", 30, None, scope="bedtime"),
        ]})
        context = replace(
            self.context,
            profile=replace(self.context.profile, tracks=({"track_id": "track_1"},)),
            preference_hints=hints,
        )
        interpreted = interpretation("sound_on", track_id=None, volume=None, timer_minutes=None)
        preferences = replace(PREFERENCES, favorite_track_ids=())

        self.assertEqual(
            plan_request(self.request, interpreted, context, preferences, catalog()),
            (DevicePatch("sound", {"track_id": "track_1", "volume": 20}),),
        )

    def test_modes_change_only_defined_settings(self):
        night = {patch.device_id: patch.values for patch in self._plan("going_to_bed")}
        self.assertEqual(night["light"], {"brightness": 0})
        self.assertEqual(night["curtains"], {"position": 0})
        self.assertEqual(night["climate"], {"temperature_c": 17.5})
        self.assertEqual(night["sound"], {"track_id": "track_1", "volume": 20, "timer_minutes": 30})
        self.assertNotIn("alarm", night)
        day = {patch.device_id: patch.values for patch in self._plan("woke_up")}
        self.assertEqual(day, {
            "light": {"brightness": 100}, "curtains": {"position": 100},
            "dnd": {"enabled": False}, "alarm": {"at": None},
        })

    def test_past_settings_select_the_latest_value_on_requested_hotel_day(self):
        hints = preference_hints("user_1").model_copy(update={
            "settings": [
                observation("climate", "temperature_c", 17, "2026-11-19T18:00:00+03:00"),
                observation("climate", "temperature_c", 18, "2026-11-18T22:00:00+00:00"),
                observation("climate", "temperature_c", 19, "2026-11-19T20:00:00+03:00"),
                observation("climate", "temperature_c", 23, "2026-11-20T03:00:00+03:00"),
                observation("light", "brightness", 15, None),
                observation("curtains", "position", 0, "2026-11-19T21:00:00+03:00"),
                observation("curtains", "position", 100, "2026-11-19T21:00:00+03:00"),
            ],
        })
        self.context = replace(self.context, preference_hints=hints)
        self.assertEqual(
            self._plan("like_yesterday"), (DevicePatch("climate", {"temperature_c": 19}),),
        )
        self.assertEqual(
            self._plan("like_yesterday", days_ago=0),
            (DevicePatch("climate", {"temperature_c": 23}),),
        )
        with self.assertRaisesRegex(ActionPlanError, "^missing_history$"):
            self._plan("like_yesterday", days_ago=4)

    def test_yesterday_resolves_direct_confirmation_on_its_hotel_day_without_profile_hints(self):
        # 01:00 in the source zone is 21:00 on the previous day in the hotel.
        message = {"user_id": "user_1", "message_id": "ok", "role": "agent",
                   "at": "2026-11-20T01:00:00+07:00", "text": "Установил 18 °C."}
        self.context = replace(self.context, profile=replace(self.context.profile, chat_history=[message]))
        setting = {"message_id": "ok", "device_id": "climate", "field_id": "temperature_c", "value": 18}
        self.assertEqual(self._plan("like_yesterday", confirmed_settings=[setting]),
                         (DevicePatch("climate", {"temperature_c": 18}),))
        with self.assertRaisesRegex(ActionPlanError, "missing_history"):
            self._plan("like_yesterday", days_ago=0, confirmed_settings=[setting])
        with self.assertRaisesRegex(ActionPlanError, "invalid_value"):
            self._plan("like_yesterday", confirmed_settings=[setting | {"value": 80}])

    def test_yesterday_replays_only_confirmed_sound_and_climate_settings(self):
        observed_at = "2026-11-19T23:05:30+03:00"
        self.context = replace(self.context, preference_hints=preference_hints("user_1").model_copy(
            update={"settings": [
                observation("sound", "track_id", "track_1", observed_at),
                observation("sound", "volume", 20, observed_at),
                observation("sound", "timer_minutes", 40, observed_at),
                observation("climate", "temperature_c", 20, observed_at),
            ]},
        ))

        self.assertEqual(self._plan("like_yesterday"), (
            DevicePatch("sound", {"track_id": "track_1", "volume": 20, "timer_minutes": 40}),
            DevicePatch("climate", {"temperature_c": 20}),
        ))

    def test_home_settings_keep_profile_priority_and_do_not_replay_old_alarm_times(self):
        hints = preference_hints("user_1").model_copy(update={
            "settings": [
                observation("light", "brightness", 15, None, scope="home"),
                observation("climate", "temperature_c", 23, None, scope="home"),
                observation("alarm", "at", "2026-11-19T07:00:00+03:00", None, scope="home"),
            ],
        })
        profile = replace(self.context.profile, user={
            "user_id": "user_1", "sleep_preferences": {"temperature": "cool"},
        })
        self.context = replace(self.context, preference_hints=hints, profile=profile)
        self.assertEqual(self._plan("like_home"), (
            DevicePatch("light", {"brightness": 15}), DevicePatch("climate", {"temperature_c": 17.5}),
        ))

    def test_wake_routine_applies_light_without_replaying_alarm_settings(self):
        hints = preference_hints("user_1").model_copy(update={"settings": [
            observation("light", "brightness", 50, None, scope="wake_up"),
            observation("light", "color_temperature_k", 3000, None, scope="wake_up"),
            observation("curtains", "position", 100, None, scope="bedtime"),
            observation("alarm", "mode", "unsupported_mode", None, scope="wake_up"),
            observation("dnd", "enabled", True, None, scope="wake_up"),
            observation("alarm", "window_minutes", 15, None, scope="wake_up"),
        ]})
        self.context = replace(self.context, preference_hints=hints)

        self.assertEqual(
            {item.device_id: item.values for item in self._plan("woke_up")},
            {"alarm": {"at": None}, "dnd": {"enabled": False},
             "light": {"brightness": 50, "color_temperature_k": 3000}},
        )

    def test_bedtime_routine_with_explicit_silence_preserves_track_timer_and_alarm(self):
        hints = preference_hints("user_1").model_copy(update={"settings": [
            observation("sound", "volume", 0, None, scope="bedtime"),
            observation("climate", "temperature_c", 21.5, None, scope="bedtime"),
            observation("alarm", "at", None, None, scope="bedtime"),
        ]})
        self.context = replace(self.context, preference_hints=hints)
        self.request = replace(self.request, initial_state={
            **STATE, "sound": {**STATE["sound"], "volume": 20},
        })

        patches = {item.device_id: item.values for item in self._plan("going_to_bed")}

        self.assertEqual(patches["sound"], {"volume": 0})
        self.assertEqual(patches["climate"], {"temperature_c": 21.5})
        self.assertNotIn("alarm", patches)

    def test_bedtime_routine_uses_confirmed_audio_values(self):
        hints = preference_hints("user_1").model_copy(update={"settings": [
            observation("sound", "track_id", "track_2", None, scope="bedtime"),
            observation("sound", "volume", 10, None, scope="bedtime"),
            observation("sound", "timer_minutes", 20, None, scope="bedtime"),
        ]})
        self.context = replace(self.context, preference_hints=hints)

        sound = next(item for item in self._plan("going_to_bed") if item.device_id == "sound")

        self.assertEqual(sound.values, {"track_id": "track_2", "volume": 10, "timer_minutes": 20})

    def test_sleep_and_wake_use_routine_values_separately_from_stay_history(self):
        previous = "2026-11-18T22:00:00+03:00"
        latest = "2026-11-19T22:00:00+03:00"
        hints = preference_hints("user_1").model_copy(update={"settings": [
            observation("climate", "temperature_c", 22, previous, scope="bedtime"),
            observation("climate", "temperature_c", 19.5, latest, scope="bedtime"),
            observation("sound", "timer_minutes", 20, latest, scope="bedtime"),
            observation("climate", "temperature_c", 21.5, latest),
            observation("sound", "timer_minutes", 45, latest),
            observation("light", "brightness", 100, None, scope="wake_up"),
            observation("light", "color_temperature_k", 5000, None, scope="wake_up"),
            observation("curtains", "position", 100, None, scope="wake_up"),
        ]})
        self.context = replace(self.context, preference_hints=hints)

        night = {patch.device_id: patch.values for patch in self._plan("going_to_bed")}
        self.assertEqual(night["climate"], {"temperature_c": 19.5})
        self.assertEqual(night["sound"]["timer_minutes"], 20)
        self.assertNotIn("alarm", night)

        wake = {patch.device_id: patch.values for patch in self._plan("woke_up")}
        self.assertEqual(wake, {
            "alarm": {"at": None}, "dnd": {"enabled": False}, "curtains": {"position": 100},
            "light": {"brightness": 100, "color_temperature_k": 5000},
        })

        yesterday = {patch.device_id: patch.values for patch in self._plan("like_yesterday")}
        self.assertEqual(yesterday, {
            "climate": {"temperature_c": 21.5}, "sound": {"timer_minutes": 45},
        })


class ExtendedRepliesTests(unittest.IsolatedAsyncioTestCase):
    async def test_wake_catalog_rejection_logs_exact_field_without_actuating_room(self):
        request = Request(
            "request_1", "user_1", "stay_1", AT, STATE, (Utterance(AT, "private-utterance"),)
        )
        context = RequestContext(
            profile=ProfileContext(user={"user_id": "user_1"}, events=(), chat_history=(), devices=(), tracks=()),
            request={"record_id": "request_1"}, stay={"hotel_timezone": "Europe/Moscow"},
            intents=(), conversation=(),
            preference_hints=preference_hints("user_1").model_copy(update={"settings": [
                observation("light", "brightness", 100, None, scope="wake_up"),
                observation("light", "color_temperature_k", 7000, None, scope="wake_up"),
            ]}),
        )
        room = AsyncMock(spec=RoomApi)
        room.create_session.return_value = SimpleNamespace(session_id=SESSION_ID)
        agent = AsyncMock(spec=AiAgent)
        agent.interpret_request.return_value = interpretation("woke_up")

        with self.assertLogs("concierge_service.service", level="DEBUG") as logs:
            answer = await RequestHandler(room, agent).handle(
                request, context, catalog(), preferences=PREFERENCES, token="private-token",
                deadline=asyncio.get_running_loop().time() + 5,
            )

        events = {event["event"]: event for event in (
            json.loads(record.getMessage()) for record in logs.records
        )}
        failure = events["action_plan_rejected"]
        self.assertEqual(failure["device"], "light")
        self.assertEqual(failure["field"], "color_temperature_k")
        self.assertEqual(failure["rule"], "maximum")
        self.assertEqual(failure["limit"], 6500)
        self.assertEqual(failure["values"], {"color_temperature_k": 7000})
        self.assertEqual(events["history_selection"]["selected_fields"], 2)
        self.assertNotIn("private-", "\n".join(logs.output))
        self.assertEqual(answer.replies[0].response_class, "refused")
        room.apply_action.assert_not_awaited()

    async def test_refusals_preserve_all_fields_and_explain_the_reason(self):
        request = Request(
            "request_1", "user_1", "stay_1", AT, STATE, (Utterance(AT, "текст гостя"),)
        )
        context = RequestContext(
            profile=ProfileContext(user={"user_id": "user_1"}, events=(), chat_history=(), devices=(), tracks=()),
            request={"record_id": "request_1"}, stay={}, intents=(), conversation=(), preference_hints=None,
        )
        room = AsyncMock(spec=RoomApi)
        room.create_session.return_value = SimpleNamespace(session_id=SESSION_ID)
        agent = AsyncMock(spec=AiAgent)
        handler = RequestHandler(room, agent)
        cases = [
            (interpretation("medical"), "медицинский вопрос"),
            (interpretation("out_of_scope"), "партнёрском отеле сна"),
            (interpretation(
                "invalid_value", subject="неповторяемый текст", requested_value=35, reason="out_of_range"
            ), "допустимый диапазон"),
        ]
        for result, reason in cases:
            with self.subTest(intent=result.intent):
                agent.interpret_request.return_value = result
                answer = await handler.handle(
                    request, context, catalog(), preferences=PREFERENCES, token="synthetic",
                    deadline=asyncio.get_running_loop().time() + 5,
                )
                reply, = answer.replies
                self.assertEqual(reply.response_class, "refused")
                self.assertIn(reason, reply.text)
                self.assertNotIn("неповторяемый текст", reply.text)
        room.apply_action.assert_not_awaited()


class ConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_proxy_is_used_and_key_is_hidden_from_repr(self):
        with patch.dict("os.environ", {
            "HACK_API_URL": "https://hack.example",
            "LLM_BASE_URL": "https://proxy.example/v1",
            "LLM_API_KEY": "synthetic-key",
            "LLM_MODEL": "test-model",
        }, clear=True):
            settings = Settings.from_env()
        self.assertNotIn("synthetic-key", repr(settings))
        with patch("concierge_service.di.container.LlmAgent", wraps=LlmAgent) as constructor:
            container = build_container(settings)
            await container.aclose()
        constructor.assert_called_once()
