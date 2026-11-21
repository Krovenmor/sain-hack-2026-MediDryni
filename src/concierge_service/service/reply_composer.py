"""Replies are based on the confirmed result of a request."""

from typing import Literal

from concierge_service.domain.action_planner import ActionRejection
from concierge_service.domain.answers import ReplyAnswer
from concierge_service.domain.dataset import Request

RequestOutcome = ActionRejection | Literal[
    "applied", "unchanged", "unrecognized", "room_error", "partial", "clarify_time", "past_period",
    "medical", "out_of_scope", "out_of_range", "unknown_track", "unsupported_mode", "invalid_time", "unavailable_time",
]

_SUCCESS = {
    "brighter": "Изменил яркость света.",
    "dimmer": "Изменил яркость света.",
    "warmer": "Изменил температуру в номере.",
    "cooler": "Изменил температуру в номере.",
    "quieter": "Изменил громкость звука.",
    "curtains": "Изменил положение штор.",
    "do_not_disturb": "Изменил режим «Не беспокоить».",
    "all_off": "Выключил свет и звук.",
    "sound_on": "Включил звук.",
    "alarm_set": "Установил будильник.",
    "alarm_shift": "Перенёс будильник.",
    "alarm_cancel": "Отменил будильник.",
    "going_to_bed": "Подготовил номер ко сну.",
    "woke_up": "Настроил номер для утра.",
    "like_home": "Применил сохранённые домашние настройки.",
    "like_yesterday": "Применил сохранённые настройки за указанный день.",
}
_OUTCOME_TEXT = {
    "clarify_time": "Когда изменить настройку? Можно указать время, задержку или период заезда.",
    "unavailable_time": "В это время изменить настройку не получится. Выберите время от текущего момента до конца заезда.",
    "past_period": "Этот период уже прошёл. Уточните, когда изменить настройку.",
    "unchanged": "Нужная настройка уже установлена.",
    "unrecognized": "Не удалось понять, какую настройку номера нужно изменить.",
    "unsupported": "Пока не могу выполнить такую просьбу.",
    "invalid_value": "Запрошенное значение для этой настройки недоступно.",
    "missing_state": "Не удалось определить текущее значение настройки.",
    "room_error": "Не удалось подтвердить изменение настроек номера.",
    "partial": "Часть изменений применил. Остальные изменения подтвердить не удалось.",
    "missing_history": "В истории не нашёл нужные настройки. Уточните, что изменить.",
    "medical": "Не могу ответить на медицинский вопрос.",
    "out_of_scope": "Могу помочь с настройками номера в партнёрском отеле сна.",
    "out_of_range": "Запрошенное значение выходит за допустимый диапазон.",
    "unknown_track": "Такого трека нет в каталоге номера.",
    "unsupported_mode": "Такой режим недоступен.",
    "invalid_time": "Не удалось установить указанное время.",
}


def compose_replies(
    request: Request, outcome: RequestOutcome, *, intent: str | None = None
) -> tuple[ReplyAnswer, ...]:
    text = (
        _SUCCESS.get(intent or "", "Изменил настройки номера.")
        if outcome == "applied"
        else _OUTCOME_TEXT[outcome]
    )
    response_class: Literal["done", "refused"] = (
        "done" if outcome in ("applied", "unchanged") else "refused"
    )
    at = max(request.at, *(utterance.at for utterance in request.utterances))
    return (ReplyAnswer(at=at.isoformat(), text=text, response_class=response_class),)
