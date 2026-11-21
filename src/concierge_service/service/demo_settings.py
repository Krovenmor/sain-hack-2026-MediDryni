"""Virtual time and schedule edits for an interactive stay."""

from datetime import UTC, datetime, time, timedelta
from time import monotonic

from concierge_service.domain.dataset import JSONScalar
from concierge_service.domain.device_catalog import DeviceCatalog
from concierge_service.domain.scenario import ScenarioStep
from concierge_service.ports.hack_api import HackApiError


PERIODS = ("evening", "night", "morning", "day")
PERIOD_FIELDS = {
    "temperature": ("climate", "temperature_c"),
    "curtains": ("curtains", "position"),
    "doNotDisturb": ("dnd", "enabled"),
    "kelvin": ("light", "color_temperature_k"),
    "brightness": ("light", "brightness"),
}


class DemoClock:
    def __init__(self, boundaries: tuple[datetime, ...]):
        self.boundaries = boundaries
        self.start, self.end = boundaries[0], boundaries[-1]
        self.anchor = self.start.astimezone(UTC)
        self.started = monotonic()
        self.paused = False

    def now(self) -> datetime:
        elapsed = 0 if self.paused else monotonic() - self.started
        return min(self.anchor + timedelta(seconds=elapsed), self.end.astimezone(UTC)).astimezone(self.start.tzinfo)

    def period_index(self, at: datetime) -> int:
        return max(i for i, start in enumerate(self.boundaries) if start.timestamp() <= at.timestamp())

    def view(self) -> dict:
        now = self.now()
        return {"now": now, "min": self.start, "max": self.end, "period": PERIODS[self.period_index(now)],
                "paused": self.paused or now.timestamp() >= self.end.timestamp()}

    def update(self, at: datetime | None, paused: bool | None) -> dict:
        current = self.view()
        target = at if at is not None else current["now"]
        self.validate_time(target)
        self.anchor = target.astimezone(UTC)
        self.started = monotonic()
        self.paused = target.timestamp() >= self.end.timestamp() or (paused if paused is not None else current["paused"])
        return self.view()

    def validate_time(self, at: datetime):
        if not self.start.timestamp() <= at.timestamp() <= self.end.timestamp():
            raise HackApiError(422, "time_outside_stay")


def period_devices(fields):
    devices: dict[str, dict[str, JSONScalar]] = {}
    for name, value in fields.items():
        device, field = PERIOD_FIELDS[name]
        devices.setdefault(device, {})[field] = value
    return devices


def combine_period_changes(previous, changes):
    result = dict(previous)
    for period, devices in changes.items():
        old = result.get(period, {})
        result[period] = old | {device: old.get(device, {}) | values for device, values in devices.items()}
    return result


def update_periods(steps, boundaries, changes, catalog):
    """Replace only the edited fields within the selected period."""
    original = steps
    for index, period in enumerate(PERIODS):
        devices = changes.get(period, {})
        if not devices:
            continue
        start = boundaries[index]
        end = boundaries[index + 1] if index + 1 < len(boundaries) else None
        start_at = start.timestamp()
        end_at = end.timestamp() if end is not None else float("inf")
        if start_at == end_at:
            raise HackApiError(422, "empty_period")
        for device, values in devices.items():
            catalog.validate(device, values)
        updated = []
        for step in steps:
            if start_at <= step.at.timestamp() < end_at and step.device_id in devices:
                values = {name: value for name, value in step.values.items() if name not in devices[step.device_id]}
                if values:
                    updated.append(ScenarioStep(step.at, step.device_id, values))
            else:
                updated.append(step)
        updated.extend(ScenarioStep(start, device, values) for device, values in devices.items())
        if end is not None:
            for device, fields in devices.items():
                values = {field: catalog.field(device, field).default_value for field in fields}
                for step in original:
                    if step.device_id == device and step.at.timestamp() <= end_at:
                        values.update({field: value for field, value in step.values.items() if field in fields})
                updated.append(ScenarioStep(end, device, values))
        steps = tuple(sorted(updated, key=lambda step: step.at.timestamp()))
    return steps


def alarm_steps(stay, wake_up, alarm_changes):
    wake_up = wake_up | alarm_changes
    local_start = stay.check_in_at.astimezone(stay.hotel_timezone)
    local_end = stay.target_wake_at.astimezone(stay.hotel_timezone)
    at = local_end
    if "time" in alarm_changes:
        hour_minute = time.fromisoformat(wake_up["time"])
        dates = (local_end.date(), local_end.date() - timedelta(days=1))  # A matching wall time repeats daily.
        candidates = []
        for day in dates:
            for fold in (0, 1):
                candidate = datetime.combine(day, hour_minute, stay.hotel_timezone).replace(fold=fold)
                instant = candidate.astimezone(UTC)
                if (local_start.timestamp() <= instant.timestamp() <= local_end.timestamp()
                        and instant.astimezone(stay.hotel_timezone).time() == hour_minute):
                    candidates.append(instant)
        if not candidates:
            raise HackApiError(422, "alarm_outside_stay")
        at = max(candidates).astimezone(stay.hotel_timezone)
    sound, light = wake_up["soundOn"], wake_up["alarmLight"]
    mode = "both" if sound and light else "sound" if sound else "light"
    values = {}
    if "time" in alarm_changes or "enabled" in alarm_changes:
        values["at"] = at.isoformat() if wake_up["enabled"] else None
    if "soundOn" in alarm_changes or "alarmLight" in alarm_changes:
        values["mode"] = mode
    steps = [ScenarioStep(local_start, "alarm", values)] if values else []
    return wake_up, steps


def merge_steps(steps, overrides, catalog: DeviceCatalog):
    # One step per device and instant. Later fields take precedence.
    merged: dict[tuple[datetime, str], ScenarioStep] = {}
    for step in (*steps, *overrides):
        key = (step.at.astimezone(UTC), step.device_id)
        previous = merged.get(key)
        values = previous.values | step.values if previous else step.values
        merged[key] = ScenarioStep(step.at, step.device_id, values)
    result = tuple(merged[key] for key in sorted(merged))
    for step in result:
        catalog.validate(step.device_id, step.values)
    return result
