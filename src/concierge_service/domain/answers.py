"""Domain-level output of a case run before HTTP serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from concierge_service.domain.dataset import JSONScalar


@dataclass(frozen=True)
class StayAnswer:
    record_id: str
    session_id: str


@dataclass(frozen=True)
class ReplyAnswer:
    at: str
    text: str
    response_class: Literal["done", "refused"]
    role: Literal["agent"] = "agent"


@dataclass(frozen=True)
class RequestAnswer:
    record_id: str
    session_id: str
    replies: tuple[ReplyAnswer, ...]
    applied: dict[str, dict[str, JSONScalar]] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    stays: tuple[StayAnswer, ...] = field(default_factory=tuple)
    requests: tuple[RequestAnswer, ...] = field(default_factory=tuple)
