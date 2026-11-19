"""Wire schemas for /run and Case2Answers."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_OFFSET_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-](?:[01]\d|2[0-3]):[0-5]\d$"
)

Identifier = Annotated[str, Field(pattern=_IDENTIFIER.pattern)]


def _valid_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must include offset")
    return value


OffsetTimestamp = Annotated[
    str,
    Field(pattern=_OFFSET_TIMESTAMP.pattern),
    AfterValidator(_valid_timestamp),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunRequest(StrictModel):
    case: int
    dataset_code: str | None = None


class StayAnswer(StrictModel):
    record_id: Identifier
    session_id: Identifier


class ReplyAnswer(StrictModel):
    role: Literal["agent"]
    at: OffsetTimestamp
    text: Annotated[str, Field(min_length=1)]
    response_class: Literal["done", "refused"]


class RequestAnswer(StrictModel):
    record_id: Identifier
    session_id: Identifier
    replies: Annotated[list[ReplyAnswer], Field(min_length=1, max_length=2)]


class Case2Answers(StrictModel):
    stays: list[StayAnswer]
    requests: list[RequestAnswer]
