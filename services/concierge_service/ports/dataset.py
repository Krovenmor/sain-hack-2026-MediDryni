"""Parsed dataset DTOs returned by the Hack API adapter.

Original JSON objects are kept alongside domain records so the AI port receives
the external representation unchanged. Treat source records as read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from concierge_service.domain.dataset import Case2Dataset

JSONObject = dict[str, object]


@dataclass(frozen=True)
class DatasetSource:
    users_by_id: Mapping[str, JSONObject]
    events_by_user_and_id: Mapping[tuple[str, str], JSONObject]
    messages_by_user_and_id: Mapping[tuple[str, str], JSONObject]
    stays_by_id: Mapping[str, JSONObject]
    requests_by_id: Mapping[str, JSONObject]
    devices: tuple[JSONObject, ...]
    tracks: tuple[JSONObject, ...]
    intents: tuple[JSONObject, ...]


@dataclass(frozen=True)
class ParsedDataset:
    dataset: Case2Dataset
    source: DatasetSource
