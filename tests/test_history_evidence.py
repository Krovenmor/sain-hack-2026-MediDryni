import json
import unittest
from dataclasses import replace

from ai_service.service.context import compact_json
from ai_service.service.preferences import PreferenceInput
from ai_service.service.request_interpretation import request_payload, validate_interpretation
from test_preference_extraction import hints
from test_request_interpretation import request_context


def message(identifier, role, text, day=19):
    return dict(user_id="guest", message_id=identifier, role=role, text=text,
                at=f"2026-11-{day}T21:00:00+03:00")


def context(query, history):
    item = request_context(query)
    return replace(item, profile=replace(item.profile, chat_history=history))


def setting(identifier, quote, value, **extra):
    return dict(message_id=identifier, quote=quote, device_id="climate", field_id="temperature_c",
                value=value) | extra


def interpret(item, settings):
    text = json.dumps(dict(record_id="request_1", intent="like_yesterday",
                           slots={"confirmed_settings": settings}))
    return validate_interpretation(text, item, request_payload(item)).slots.confirmed_settings


class HistoryEvidenceTests(unittest.TestCase):
    def test_source_quote_cannot_turn_denial_promise_or_previous_value_into_a_fact(self):
        for text in ("Не установил температуру 20 °C.", "Завтра поставлю температуру 20 °C.",
                     "Снизил температуру с 20 °C до 18 °C."):
            with self.subTest(text=text):
                item = context("Как вчера", [message("m", "agent", text)])
                self.assertEqual(interpret(item, [setting("m", "20 °C", 20)]), [])

    def test_confirmation_list_supports_each_quoted_value(self):
        response = "Готово: «Белый шум» на громкости 20, таймер 40 минут, температура 20,5 °C."
        item = context("Как вчера", [message("m", "agent", response)])
        item = replace(item, profile=replace(item.profile, tracks=[{"track_id": "white", "title": "Белый шум"}]))
        settings = [
            setting("m", "«Белый шум»", "white", device_id="sound", field_id="track_id"),
            setting("m", "громкости 20", 20, device_id="sound", field_id="volume"),
            setting("m", "таймер 40 минут", 40, device_id="sound", field_id="timer_minutes"),
            setting("m", "температура 20,5 °C", 20.5),
        ]
        self.assertEqual([s.value for s in interpret(item, settings)], ["white", 20, 40, 20.5])

    def test_pair_survives_paraphrase_and_noise(self):
        pair = [message("ask", "user", "Поставь температуру 20 градусов."),
                message("ack", "agent", "Готово.")]
        noise = [message(str(i), "agent", "Пожалуйста.") for i in range(10)]
        item = context("Верни настройки прошлого визита", pair + noise)
        sent = request_payload(item)["chat_history"]
        self.assertEqual(sent, pair)
        self.assertLessEqual(len(compact_json(sent)), 420)
        fact = setting("ack", "температуру 20 градусов", 20, request_message_id="ask")
        self.assertEqual([s.value for s in interpret(item, [fact])], [20])

    def test_receipt_only_confirms_adjacent_request_in_original_history(self):
        ask = message("ask", "user", "Поставь температуру 20 градусов.")
        ack = message("ack", "agent", "Готово.")
        fact = setting("ack", "20 градусов", 20, request_message_id="ask")
        for history in ([ask, message("long", "user", "Спасибо " * 200), ack],
                        [ask, ack | {"text": "Хорошо."}], [ask, ack | {"user_id": "other"}],
                        [ask, ack | {"text": "Постараюсь."}]):
            with self.subTest(history=history[-1]["text"]):
                self.assertEqual(interpret(context("Как вчера", history), [fact]), [])

    def test_pozavchera_wins_over_more_recent_confirmations(self):
        old = message("old", "agent", "Поставил температуру 18 °C.", day=18)
        newer = [message(str(i), "agent", "Поставил температуру 23 °C.") for i in range(5)]
        for query in ("Как позавчера", "Как два дня назад"):
            self.assertIn(old, request_payload(context(query, [old, *newer]))["chat_history"])

    def test_partial_refusal_does_not_discard_supported_sibling(self):
        row = message("m", "agent", "Установил яркость 30%, а температуру 23 °C выставить не удалось.")
        item = context("Как вчера", [row])
        fields = [
            setting("m", "23 °C", 23),
            setting("m", "яркость 30%", 30, device_id="light", field_id="brightness"),
        ]
        self.assertEqual([s.field_id for s in interpret(item, fields)], ["brightness"])

    def test_value_must_appear_in_matching_field_of_confirmed_clause(self):
        row = message("m", "agent", "Готово: громкость 23, температура 20 °C.")
        item = context("Как вчера", [row])
        correct = setting("m", "температура 20 °C", 20)
        wrong = [setting("m", row["text"], 23), setting("m", "температура 22 °C", 22),
                 setting("m", "20 °C", 20, device_id="sound", field_id="volume")]
        self.assertEqual([s.value for s in interpret(item, [correct, *wrong])], [20])

    def test_preference_extraction_cannot_reintroduce_refused_chat_setting(self):
        row = message("m", "agent", "Не удалось установить температуру 20 °C.")
        item = context("Как вчера", [row])
        field = dict(field_id="temperature_c", value_schema={"type": "number"})
        profile = replace(item.profile, devices=[dict(device_id="climate", fields=[field])])
        fact = dict(context="past_stay", observed_at=row["at"], device_id="climate",
                    field_id="temperature_c", value=20,
                    sources=[dict(kind="message", source_id="m", pointer="/text")])
        payload = hints(settings=[fact])
        prepared = PreferenceInput(profile, "guest", profile_number=1)
        self.assertEqual(prepared.parse(json.dumps(payload)).settings, [])
        profile = replace(profile, chat_history=[row | {"text": "Поставил температуру 20 °C."}])
        prepared = PreferenceInput(profile, "guest", profile_number=1)
        self.assertEqual(prepared.parse(json.dumps(payload)).settings[0].value, 20)
