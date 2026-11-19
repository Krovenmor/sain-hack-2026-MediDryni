"""Check field-level evidence in a direct confirmation or an adjacent request and receipt."""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from math import isfinite

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from concierge_service.ports.ai import JsonObject, ObservedSetting, Scalar, SourceRef

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


# Only a labelled list recorded by the agent is sufficiently unambiguous to fill
# gaps in the timeline of language-model observations. Conversation and hotel notes stay with the model.
_HOME_SUMMARY = re.compile(
    r"(?:(?:записал[аи]?|запомнил[аи]?|обновил[аи]?|сохранил[аи]?)\s+)?"
    r"домашние\s+(?:вечерние\s+)?настройки\s*:\s*(.+)", re.I,
)
_APPROX = r"(?:(?:около|примерно)\s+)?"
_HOME_FIELDS = {
    ("light", "brightness"): rf"(?:яркость(?: света)?|свет|ночник)\s+{_APPROX}{_NUMBER}\s*%",
    ("light", "color_temperature_k"): rf"(?:(?:цветовая температура|т[её]плый свет|свет)\s+)?{_APPROX}{_NUMBER}\s*[kк]",
    ("climate", "temperature_c"): rf"(?:(?:температура|климат)\s+)?{_APPROX}{_NUMBER}\s*°\s*[cс]",
}


def home_summary_settings(
    messages: Sequence[JsonObject], known: Sequence[ObservedSetting],
    fields: Mapping[tuple[str, str], JsonValue],
) -> list[ObservedSetting]:
    undated_fields = set()
    recorded = set()
    for setting in known:
        if setting.context != "home":
            continue
        key = setting.device_id, setting.field_id
        if setting.observed_at is None:
            undated_fields.add(key)
        else:
            recorded.add((key, datetime.fromisoformat(setting.observed_at), setting.value))

    validators = {}
    for key, pattern in _HOME_FIELDS.items():
        schema = fields.get(key)
        if key in undated_fields or not isinstance(schema, dict) or schema.get("type") not in ("number", "integer"):
            continue
        if schema.keys() <= {"type", "minimum", "maximum"} and all(
            type(schema[k]) in (int, float) for k in schema.keys() - {"type"}
        ):
            validators[key] = (pattern, Draft202012Validator(schema))
    if not validators:
        return []
    additions = []
    for row in messages:
        if row.get("role") != "agent":
            continue
        match = _HOME_SUMMARY.fullmatch(str(row["text"]).strip().rstrip("."))
        if not match or _UNCERTAIN.search(match[1].casefold()) or re.search(r"\b(?:или|либо)\b", match[1], re.I):
            continue
        for part in re.split(r";|(?<!\d),|,(?!\d)", match[1]):
            for key, (pattern, validator) in validators.items():
                value = re.fullmatch(pattern, part.strip(), re.I)
                if value:
                    number = float(value[1].replace(",", "."))
                    if not isfinite(number) or not validator.is_valid(number):
                        continue
                    fact = (key, datetime.fromisoformat(row["at"]), number)
                    if fact in recorded:
                        continue
                    recorded.add(fact)
                    device, field = key
                    additions.append(ObservedSetting(
                        context="home", observed_at=row["at"], device_id=device, field_id=field,
                        value=int(number) if number.is_integer() else number,
                        sources=[SourceRef(kind="message", source_id=row["message_id"], pointer="/text")],
                    ))
    return additions
