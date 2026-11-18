"""Validate room values against the simulator catalog."""

from collections.abc import Mapping
from math import isfinite

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import Unresolvable

from concierge_service.domain.dataset import DeviceField, JSONScalar

DeviceFields = Mapping[str, Mapping[str, DeviceField]]
RoomState = Mapping[str, Mapping[str, JSONScalar]]

_format_checker = FormatChecker()
_local_schema_registry = Registry()


class DeviceValueError(ValueError):
    """A device, field or value is unsupported by the catalog."""


class DeviceCatalog:
    def __init__(self, fields: DeviceFields) -> None:
        self._fields = fields
        self._validators: dict[tuple[str, str], Draft202012Validator] = {}

    def field(self, device_id: str, field_id: str) -> DeviceField:
        field = self._fields.get(device_id, {}).get(field_id)
        if field is None:
            raise DeviceValueError("unknown device or field")
        return field

    def validate(self, device_id: str, values: Mapping[str, JSONScalar]) -> None:
        for field_id, value in values.items():
            if isinstance(value, float) and not isfinite(value):
                raise DeviceValueError("value outside device field schema")
            key = device_id, field_id
            try:
                if key not in self._validators:
                    schema = dict(self.field(device_id, field_id).value_schema)
                    Draft202012Validator.check_schema(schema)
                    self._validators[key] = Draft202012Validator(
                        schema, format_checker=_format_checker, registry=_local_schema_registry
                    )
                valid = self._validators[key].is_valid(value)
            except (SchemaError, Unresolvable):
                raise DeviceValueError("invalid device field schema") from None
            if not valid:
                raise DeviceValueError("value outside device field schema")
