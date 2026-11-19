"""Domain representation of a scheduled room setting change."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from concierge_service.domain.dataset import JSONScalar


@dataclass(frozen=True)
class ScenarioStep:
    at: datetime
    device_id: str
    values: Mapping[str, JSONScalar]
