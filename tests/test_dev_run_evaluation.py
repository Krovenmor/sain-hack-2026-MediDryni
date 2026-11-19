import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from tools.evaluate_dev_run import check_inputs, sleep_matches


class DevRuleEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dataset, reference = check_inputs()
        cls.stays = {stay["record_id"]: stay for stay in dataset["stays"]}
        cls.expected = reference["stays"]
        cls.defaults = {
            device["device_id"]: {
                field["field_id"]: field["default_value"] for field in device["fields"]
            } for device in dataset["devices"]
        }

    def room_log(self, expected):
        stay = self.stays[expected["record_id"]]
        steps = [
            SimpleNamespace(**(step | {"at": datetime.fromisoformat(step["at"])}))
            for step in expected["example_scenario"]
        ]
        return SimpleNamespace(
            session=SimpleNamespace(
                initial_state=self.defaults,
                starts_at=datetime.fromisoformat(stay["check_in_at"]),
                until=datetime.fromisoformat(stay["target_wake_at"]),
            ),
            entries=[SimpleNamespace(kind="scenario", steps=steps)],
        )

    def test_reference_stay_scenarios_satisfy_all_seven_rules(self):
        for expected in self.expected:
            with self.subTest(stay=expected["record_id"]):
                self.assertEqual(sleep_matches(expected, self.room_log(expected)), (True,) * 7)

    def test_single_partial_curtain_opening_must_reach_fifty_points(self):
        expected = self.expected[0]
        log = self.room_log(expected)
        bedtime = datetime.fromisoformat(expected["resolved_bedtime_at"])
        steps = log.entries[0].steps
        steps[:] = [step for step in steps if step.at <= bedtime]
        rise = SimpleNamespace(at=log.session.until - timedelta(minutes=30),
                               device_id="curtains", values={"position": 49})
        steps.append(rise)
        self.assertFalse(sleep_matches(expected, log)[5])
        rise.values["position"] = 50
        self.assertTrue(sleep_matches(expected, log)[5])
        rise.at -= timedelta(seconds=1)
        self.assertFalse(sleep_matches(expected, log)[5])
