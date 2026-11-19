"""Check field-level evidence in a direct confirmation or an adjacent request and receipt."""

import re
from collections.abc import Sequence
from datetime import datetime

from concierge_service.ports.ai import JsonObject, ObservedSetting, Scalar

_RECEIPT = re.compile(r"(?:готово|сделано|выполнено|установлено|настроено)[.! ]*", re.I)
_ACTION = re.compile(
    r"\b(?:готово|сделано|выполнено|(?:поставил|установил|выставил|выбрал|настроил|"
    r"изменил|включил|выключил|отключил|открыл|закрыл|приглушил|переключил|снизил|поднял)"
    r"[аи]?|(?:установлен|включен|выключен|открыт|закрыт)[аоы]?)\b"
)
_UNCERTAIN = re.compile(
    r"\b(?:не|если|пока|попробую|могу|могли|обещаю|планирую|раньше|вчера|завтра|"
    r"удалось бы|хотели|просили|тогда|бы)\b|[?]"
)
_REQUEST = re.compile(
    r"\b(?:поставь|установи|настрой|сделай|включи|выключи|отключи|открой|закрой|"
    r"приглуши|выставь|выбери|добавь|переключи|убавь)\b"
)
_SENTENCES = re.compile(r"(?<!\d)\.|\.(?!\d)|[!;\n]")
_CLAUSES = re.compile(r"(?<!\d),|,(?!\d)|\s+(?:а|но|зато)\s+")
_NUMBER = r"(-?\d+(?:[.,]\d+)?)"
_NUMERIC = {
    ("climate", "temperature_c"): rf"{_NUMBER}\s*(?:°|градус)|температур\w*\s*(?:на|до)?\s*{_NUMBER}",
    ("sound", "volume"): rf"громкост\w*\s*(?:на|до)?\s*{_NUMBER}",
    ("sound", "timer_minutes"): rf"таймер\w*\s*(?:на\s*)?{_NUMBER}\s*(?:мин|$)",
    ("light", "brightness"): rf"(?:яркост\w*|свет)\s*(?:на|до)?\s*{_NUMBER}",
    ("light", "color_temperature_k"): rf"{_NUMBER}\s*(?:[kк]\b|кельвин)",
    ("curtains", "position"): rf"штор\w*\s*(?:на|до)?\s*{_NUMBER}\s*%",
    ("alarm", "window_minutes"): rf"окн\w*\s*(?:на\s*)?{_NUMBER}\s*(?:мин|$)",
}


def _has_value(
    text: str, device: str, field: str, value: Scalar, tracks: Sequence[JsonObject],
) -> bool:
    pattern = _NUMERIC.get((device, field))
    if pattern and type(value) in (float, int):
        for match in re.finditer(pattern, text):
            number = next(group for group in match.groups() if group is not None)
            if float(number.replace(",", ".")) == value:
                return True
    if (device, field) == ("sound", "track_id"):
        return any(
            track["track_id"] == value and
            re.search(r"(?<!\w)" + re.escape(str(track["title"]).casefold()) + r"(?!\w)", text)
            for track in tracks
        )
    if (device, field) == ("curtains", "position") and value in (0, 100) and not re.search(r"\d", text):
        direction = "закр" if value == 0 else "откр"
        return "штор" in text and direction in text
    if (device, field) == ("dnd", "enabled") and type(value) is bool:
        verb = "включ" if value else "(?:выключ|отключ)"
        return "не беспокоить" in text and bool(re.search(verb, text))
    if (device, field) == ("alarm", "mode"):
        modes = {"light": "свет", "sound": "звук", "both": "свет и звук"}
        return value in modes and "будильник" in text and modes[value] in text and (
            value == "both" or not ("свет" in text and "звук" in text)
        )
    return False


def confirmed_value(
    response: str, quote: str, device: str, field: str, value: Scalar,
    tracks: Sequence[JsonObject], *, request: str | None = None,
) -> bool:
    source = request if request is not None else response
    quote = quote.strip()
    if not quote or quote not in source:
        return False
    if request is not None and not _RECEIPT.fullmatch(response.strip()):
        return False
    text = source.casefold().replace("ё", "е")
    snippet = quote.casefold().replace("ё", "е")
    for sentence in _SENTENCES.split(text):
        if re.search(r"\b(?:если|когда-нибудь)\b", sentence):
            continue
        # A leading receipt applies to all the fields in its colon-separated list.
        listed = bool(re.match(r"\s*(?:готово|сделано|выполнено)\s*:", sentence))
        for clause in _CLAUSES.split(sentence):
            if re.search(r"\b(?:с|от)\s+-?\d", clause):
                continue
            if not clause.strip() or _UNCERTAIN.search(clause.replace("не беспокоить", "dnd")):
                continue
            overlap = snippet if snippet in clause else clause.strip()
            if overlap not in snippet:
                continue
            action = _REQUEST if request is not None else _ACTION
            if (listed or action.search(clause)) and _has_value(overlap, device, field, value, tracks):
                return True
    return False


class ConfirmationHistory:
    def __init__(self, history: Sequence[JsonObject], selected: Sequence[JsonObject], user_id: str):
        self.visible = {row["message_id"]: row for row in selected if row["user_id"] == user_id}
        own = [row for row in history if row["user_id"] == user_id]
        self.previous = {
            reply["message_id"]: ask["message_id"]
            for ask, reply in zip(own, own[1:])
            if ask.get("role") == "user" and reply.get("role") == "agent"
        }

    def supports(
        self, message_id: str, request_id: str | None, quote: str, device: str, field: str,
        value: Scalar, tracks: Sequence[JsonObject],
    ) -> bool:
        reply = self.visible.get(message_id)
        if reply is None or reply["role"] != "agent":
            return False
        request = None
        if request_id is not None:
            if self.previous.get(message_id) != request_id or request_id not in self.visible:
                return False
            request = str(self.visible[request_id]["text"])
        return confirmed_value(str(reply["text"]), quote, device, field, value, tracks, request=request)

    def supports_observation(
        self, source_id: str, source_ids: set[str], setting: ObservedSetting,
        tracks: Sequence[JsonObject],
    ) -> bool:
        reply = self.visible.get(source_id)
        if reply is None or reply.get("role") != "agent" or setting.observed_at is None:
            return False
        if datetime.fromisoformat(setting.observed_at) != datetime.fromisoformat(str(reply["at"])):
            return False
        args = setting.device_id, setting.field_id, setting.value, tracks
        if self.supports(source_id, None, str(reply["text"]), *args):
            return True
        request_id = self.previous.get(source_id)
        if request_id not in source_ids or request_id not in self.visible:
            return False
        return self.supports(source_id, request_id, str(self.visible[request_id]["text"]), *args)
