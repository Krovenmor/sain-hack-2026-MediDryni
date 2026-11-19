"""Wire types shared by the Hack API and Room API adapters."""

from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter

from concierge_service.domain.dataset import JSONScalar

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
OffsetText = Annotated[
    str,
    Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(\.[0-9]+)?[+-]([01][0-9]|2[0-3]):[0-5][0-9]$"
    ),
]
_offset_text = TypeAdapter(OffsetText)
_aware_datetime = TypeAdapter(AwareDatetime)


def _parse_timestamp(value: object) -> datetime:
    return _aware_datetime.validate_python(_offset_text.validate_python(value, strict=True))


Timestamp = Annotated[AwareDatetime, BeforeValidator(_parse_timestamp)]
DeviceState = Annotated[dict[Identifier, JSONScalar], Field(min_length=1)]
RoomState = Annotated[dict[Identifier, DeviceState], Field(min_length=1)]


class WireModel(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )
