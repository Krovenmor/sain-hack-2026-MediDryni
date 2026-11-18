"""Replies are based on the confirmed result of a request."""

from typing import Literal

from concierge_service.domain.action_planner import ActionRejection
from concierge_service.domain.answers import ReplyAnswer
from concierge_service.domain.dataset import Request

RequestOutcome = ActionRejection | Literal[
    "applied", "unchanged", "unrecognized", "room_error", "partial"
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
}
_OUTCOME_TEXT = {
    "unchanged": "Нужная настройка уже установлена.",
    "unrecognized": "Не удалось понять, какую настройку номера нужно изменить.",
    "unsupported": "Пока не могу выполнить такую просьбу.",
    "invalid_value": "Запрошенное значение для этой настройки недоступно.",
    "missing_state": "Не удалось определить текущее значение настройки.",
    "room_error": "Не удалось подтвердить изменение настроек номера.",
    "partial": "Часть изменений применил. Остальные изменения подтвердить не удалось.",
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
    acknowledgment = "Готово." if response_class == "done" else "Не удалось выполнить просьбу полностью."
    texts = (acknowledgment, text) if len(request.utterances) == 2 else (text,)
    return tuple(
        ReplyAnswer(at=utterance.at.isoformat(), text=reply_text, response_class=response_class)
        for utterance, reply_text in zip(request.utterances, texts, strict=True)
    )
