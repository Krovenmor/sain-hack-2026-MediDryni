import json
import re
from collections.abc import Callable, Sequence
from typing import cast

from concierge_service.ports.ai import JsonObject


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bounded_history(
    records: Sequence[JsonObject], *, budget: int, query: str = "",
    recent: int = 0, max_items: int,
    priority: Callable[[JsonObject], int] | None = None,
    pair_replies: bool = False,
) -> list[JsonObject]:
    query_terms = _terms(query)
    serialized = [compact_json(record) for record in records]
    order = list(reversed(range(len(records))))
    latest = order[:recent]
    remaining = sorted(
        order[recent:],
        key=lambda index: (
            priority(records[index]) if priority else 0,
            len(_terms(record_text(records[index])) & query_terms),
        ),
        reverse=True,
    )
    selected: set[int] = set()
    for index in latest + remaining:
        if index in selected:
            continue
        if pair_replies:
            neighbor = index - 1 if records[index]["role"] == "agent" else index + 1
            if (0 <= neighbor < len(records) and neighbor not in selected
                    and records[neighbor]["role"] != records[index]["role"]
                    and len(selected) + 2 <= max_items
                    and len(serialized[index]) + len(serialized[neighbor]) + 2 <= budget):
                selected.add(neighbor)
                budget -= len(serialized[neighbor]) + 1
        size = len(serialized[index]) + 1
        if size <= budget:
            selected.add(index)
            budget -= size
            if len(selected) >= max_items:
                break
    return [records[index] for index in sorted(selected)]


def _terms(text: str) -> set[str]:
    return {word[:5] for word in re.findall(r"[^\W\d_]{4,}", text.casefold())}


def record_text(record: JsonObject) -> str:
    payload = record.get("payload")
    text = payload.get("text") if isinstance(payload, dict) else record.get("text")
    return text if isinstance(text, str) else ""


_ROOM_CUES = re.compile(
    r"будиль|буди|разбуд|подъ[её]м|засып|засн|отбо|вста[юе]|сплю|спат|сон\b|"
    r"температур|прохлад|прохол|градус|°\s*[cс]|мерз|душно|одеял|"
    r"штор|яркост|свет|ночник|громкост|трек|белый шум|тишин|звук|прибой|дожд|"
    r"климат|кондиц|обогре|"
    r"не беспоко|заезд|в номере|дома|домашн",
    re.IGNORECASE,
)
_PREFERENCE_CUES = re.compile(
    r"люблю|нравитс|предпочит|привык|обычно|всегда|не переношу|"
    r"мешает|мешают|пугает|режет глаза|под шум|под дожд|в следующий раз",
    re.IGNORECASE,
)


def preference_evidence(record: JsonObject) -> int:
    text = record_text(record)
    if not text:
        return 0
    if record.get("source") == "hotel":
        return 4
    if _ROOM_CUES.search(text):
        return 3 if _PREFERENCE_CUES.search(text) else 2
    return int(bool(_PREFERENCE_CUES.search(text)))


def device_catalog(devices: Sequence[JsonObject], *, include_limits: bool) -> list[JsonObject]:
    return [
        {
            **{key: device[key] for key in ("device_id", "title") if key in device},
            "fields": [
                {
                    key: field[key]
                    for key in (("field_id", "unit", "value_schema") if include_limits else ("field_id",))
                    if key in field and field[key] is not None
                }
                for field in cast(list[JsonObject], device["fields"])
            ],
        }
        for device in devices
    ]


def track_catalog(tracks: Sequence[JsonObject]) -> list[JsonObject]:
    return [{key: track[key] for key in ("track_id", "title") if key in track} for track in tracks]
