"""Translate supported room intents into changes to individual devices."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from concierge_service.domain.dataset import JSONScalar
from concierge_service.domain.device_catalog import (
    DeviceCatalog,
    DeviceFields,
    DeviceValueError,
    RoomState,
)

ActionRejection = Literal["unsupported", "invalid_value", "missing_state"]

_RELATIVE_SETTINGS = {
    "brighter": ("light", "brightness", 15),
    "dimmer": ("light", "brightness", -15),
    "warmer": ("climate", "temperature_c", 1),
    "cooler": ("climate", "temperature_c", -1),
    "quieter": ("sound", "volume", -15),
}
_ABSOLUTE_SETTINGS = {
    "curtains": ("curtains", "position"),
    "do_not_disturb": ("dnd", "enabled"),
}


class ActionPlanError(ValueError):
    def __init__(self, reason: ActionRejection) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class DevicePatch:
    device_id: str
    values: Mapping[str, JSONScalar]


def plan_actions(
    intent: str,
    state: RoomState,
    catalog: DeviceFields,
    *,
    target: JSONScalar = None,
    amount: float | None = None,
) -> tuple[DevicePatch, ...]:
    devices = DeviceCatalog(catalog)
    try:
        if intent in _RELATIVE_SETTINGS:
            device, field, step = _RELATIVE_SETTINGS[intent]
            value = target
            if value is None:
                value = _relative_target(devices, state, device, field, step, amount)
            patches = (DevicePatch(device, {field: value}),)
        elif intent in _ABSOLUTE_SETTINGS:
            device, field = _ABSOLUTE_SETTINGS[intent]
            patches = (DevicePatch(device, {field: target}),)
        elif intent == "all_off":
            patches = (
                DevicePatch("light", {"brightness": 0}),
                DevicePatch("sound", {"volume": 0}),
            )
        else:
            raise ActionPlanError("unsupported")

        for patch in patches:
            devices.validate(patch.device_id, patch.values)
    except DeviceValueError:
        raise ActionPlanError("invalid_value") from None

    return tuple(patch for patch in patches if not patch_matches_state(patch, state))


def patch_matches_state(patch: DevicePatch, state: RoomState) -> bool:
    fields = state.get(patch.device_id, {})
    return all(
        field_id in fields and _same_value(fields[field_id], value)
        for field_id, value in patch.values.items()
    )


def _same_value(actual: JSONScalar, expected: JSONScalar) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    return actual == expected


def _relative_target(
    devices: DeviceCatalog,
    state: RoomState,
    device: str,
    field: str,
    step: int,
    amount: float | None,
) -> int | float:
    initial = state.get(device, {}).get(field)
    if not isinstance(initial, (int, float)) or isinstance(initial, bool):
        raise ActionPlanError("missing_state")
    devices.validate(device, {field: initial})
    offset = step if amount is None else (amount if step > 0 else -amount)
    target = initial + offset
    if amount is None:
        schema = devices.field(device, field).value_schema
        boundary = schema.get("maximum" if step > 0 else "minimum")
        if isinstance(boundary, (int, float)) and not isinstance(boundary, bool):
            target = min(target, boundary) if step > 0 else max(target, boundary)

    moved_in_direction = target > initial if step > 0 else target < initial
    if not moved_in_direction:
        raise ActionPlanError("invalid_value")
    return target
