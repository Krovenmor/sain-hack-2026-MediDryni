"""Consumer contract checks; no LLM, network or simulator required."""

import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from concierge_service.ports.ai import (
    PreferenceHints,
    REQUEST_INTERPRETATION_ADAPTER,
)


EXAMPLES = {
    "brighter": {"mode": "relative", "amount": None},
    "dimmer": {"mode": "absolute", "value": 20},
    "warmer": {"mode": "relative", "amount": 2},
    "cooler": {"mode": "absolute", "value": 19.5},
    "quieter": {"mode": "relative", "amount": 10},
    "sound_on": {"track_id": "rain", "volume": None, "timer_minutes": 30},
    "curtains": {"position": 0},
    "alarm_set": {"target_at": "2026-11-21T07:00:00+03:00"},
    "alarm_shift": {"mode": "relative", "minutes": -30},
    "do_not_disturb": {"enabled": True},
    "invalid_value": {
        "subject": "temperature", "requested_value": 35, "reason": "out_of_range",
    },
    **{intent: {} for intent in (
        "all_off", "alarm_cancel", "going_to_bed", "woke_up",
        "like_yesterday", "like_home", "medical", "out_of_scope",
    )},
}


def parse(intent, slots, **extra):
    return REQUEST_INTERPRETATION_ADAPTER.validate_json(json.dumps({
        "record_id": "request_1", "intent": intent, "slots": slots, **extra,
    }))


class AiContractTests(unittest.TestCase):
    def test_covers_exactly_external_intents_and_round_trips(self):
        path = Path(__file__).resolve().parents[1] / "docs/contracts/openapi.json"
        schema = json.loads(path.read_text())["components"]["schemas"]
        intents = schema["Case2Dataset"]["properties"]["intents"]
        external = {v["const"] for v in intents["items"]["properties"]["intent_id"]["anyOf"]}
        generated = REQUEST_INTERPRETATION_ADAPTER.json_schema()
        self.assertEqual(set(EXAMPLES), external)
        self.assertEqual(set(generated["discriminator"]["mapping"]), external)
        for intent, slots in EXAMPLES.items():
            with self.subTest(intent=intent):
                result = parse(intent, slots)
                self.assertEqual(result.intent, intent)
                self.assertEqual(result.model_dump(exclude_unset=True)["slots"], slots)
                self.assertEqual(parse(intent, slots).record_id, "request_1")

    def test_rejects_wrong_slots_and_executable_output(self):
        cases = [
            ("unknown", {}),
            ("medical", {"advice": "text"}),
            ("like_home", {"device_id": "climate", "values": {"temperature": 20}}),
            ("warmer", {"mode": "relative", "amount": "2"}),
            ("warmer", {"mode": "relative", "amount": True}),
            ("cooler", {"mode": "relative", "amount": -2}),
            ("quieter", {"mode": "absolute", "value": float("inf")}),
            ("alarm_shift", {"mode": "relative", "minutes": 10, "target_at": "extra"}),
            ("do_not_disturb", {"enabled": "true"}),
        ]
        for intent, slots in cases:
            with self.subTest(intent=intent, slots=slots):
                with self.assertRaises(ValidationError):
                    parse(intent, slots)
        with self.assertRaises(ValidationError):
            parse("all_off", {}, response_class="done")

    def test_timestamp_requires_offset_and_valid_date(self):
        for target in (
            "2026-11-21T07:00:00Z", "2026-11-21T07:00:00",
            "2026-02-30T07:00:00+03:00",
        ):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                parse("alarm_set", {"target_at": target})
        result = parse("alarm_shift", {
            "mode": "absolute", "target_at": "2026-11-21T07:00:00+00:00",
        })
        self.assertEqual(result.slots.target_at, "2026-11-21T07:00:00+00:00")

    def test_alarm_qualifiers_are_preserved_without_inventing_defaults(self):
        implicit = parse("alarm_set", EXAMPLES["alarm_set"])
        self.assertIsNone(implicit.slots.alarm_mode)
        self.assertIsNone(implicit.slots.window_minutes)
        explicit = parse("alarm_set", {
            **EXAMPLES["alarm_set"], "alarm_mode": "light", "window_minutes": 15,
        })
        self.assertEqual(explicit.slots.alarm_mode, "light")
        self.assertEqual(explicit.slots.window_minutes, 15)

    def test_unknown_preferences_differ_from_explicit_false_or_empty(self):
        payload = {
            "user_id": "user_1", "temperature": None, "light_sensitive": None,
            "favorite_track_ids": None, "gentle_alarm": None,
            "weekday_bedtime": None, "weekday_wake_time": None,
            "home_timezone": None, "settings": [],
        }
        unknown = PreferenceHints.model_validate_json(json.dumps(payload))
        self.assertIsNone(unknown.gentle_alarm)
        source = [{"kind": "message", "source_id": "message_1"}]
        payload["gentle_alarm"] = {"value": False, "sources": source}
        payload["favorite_track_ids"] = {"value": [], "sources": source}
        explicit = PreferenceHints.model_validate_json(json.dumps(payload))
        self.assertIs(explicit.gentle_alarm.value, False)
        self.assertEqual(explicit.favorite_track_ids.value, [])
        payload["gentle_alarm"]["sources"] = []
        with self.assertRaises(ValidationError):
            PreferenceHints.model_validate_json(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
