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

    def __init__(
        self, message: str, *, device_id: str, field_id: str, rule: str,
        value: JSONScalar = None, alternatives: tuple[str, ...] = (),
        limit: int | float | None = None,
    ) -> None:
        super().__init__(message)
        self.device_id = device_id
        self.field_id = field_id
        self.rule = rule
        self.value = value
        self.alternatives = alternatives
        self.limit = limit


class DeviceCatalog:
    def __init__(self, fields: DeviceFields) -> None:
        self._fields = fields
        self._validators: dict[tuple[str, str], Draft202012Validator] = {}

    def field(self, device_id: str, field_id: str) -> DeviceField:
        field = self._fields.get(device_id, {}).get(field_id)
        if field is None:
            raise DeviceValueError(
                "unknown device or field", device_id=device_id, field_id=field_id, rule="unknown_field"
            )
        return field

    def validate(self, device_id: str, values: Mapping[str, JSONScalar]) -> None:
        for field_id, value in values.items():
            if isinstance(value, float) and not isfinite(value):
                raise DeviceValueError(
                    "value outside device field schema", device_id=device_id, field_id=field_id,
                    rule="nonfinite", value=value,
                )
            key = device_id, field_id
            try:
                if key not in self._validators:
                    schema = dict(self.field(device_id, field_id).value_schema)
                    Draft202012Validator.check_schema(schema)
                    self._validators[key] = Draft202012Validator(
                        schema, format_checker=_format_checker, registry=_local_schema_registry
                    )
                failure = next(self._validators[key].iter_errors(value), None)
            except (SchemaError, Unresolvable):
                raise DeviceValueError(
                    "invalid device field schema", device_id=device_id, field_id=field_id,
                    rule="invalid_schema", value=value,
                ) from None
            if failure is not None:
                rule = failure.validator or "schema"
                raise DeviceValueError(
                    "value outside device field schema", device_id=device_id, field_id=field_id,
                    rule=rule, value=value,
                    alternatives=tuple(sorted({error.validator or "schema" for error in failure.context})),
                    limit=(
                        failure.validator_value
                        if rule in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
                        else None
                    ),
                )
