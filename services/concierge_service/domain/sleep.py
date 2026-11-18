"""Deterministic room scenarios for sleep rules R1–R8."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import Unresolvable

from concierge_service.domain.dataset import DeviceField, JSONScalar, Stay
from concierge_service.domain.scenario import ScenarioStep


class SleepPlanError(ValueError):
    """The stay or catalog cannot support the generated scenario."""


@dataclass(frozen=True)
class ResolvedPreferences:
    home_timezone: ZoneInfo | None
    bedtime: time
    night_temperature: float
    light_sensitive: bool
    favorite_track_ids: tuple[str, ...]
    gentle_alarm: bool


class SleepPlanner:
    def plan(
        self,
        stay: Stay,
        preferences: ResolvedPreferences,
        catalog: Mapping[str, Mapping[str, DeviceField]],
    ) -> tuple[ScenarioStep, ...]:
        check_in = stay.check_in_at.astimezone(UTC)
        wake = stay.target_wake_at.astimezone(UTC)
        bedtime = resolve_bedtime(stay, preferences)
        night_start = max(check_in, bedtime)
        dawn = max(night_start, wake - timedelta(minutes=25))
        steps: list[ScenarioStep] = []

        def add_step(when: datetime, device: str, **values: JSONScalar) -> None:
            steps.append(ScenarioStep(when.astimezone(stay.hotel_timezone), device, values))

        if check_in < bedtime:
            add_step(
                max(check_in, bedtime - timedelta(hours=1)),
                "light",
                brightness=10 if preferences.light_sensitive else 20,
                color_temperature_k=2700,
            )

        add_step(night_start, "light", brightness=0)
        if night_start < dawn:
            add_step(night_start, "curtains", position=0)
        add_step(dawn, "curtains", position=100)
        add_step(night_start, "climate", temperature_c=preferences.night_temperature)
        add_step(night_start, "dnd", enabled=True)

        add_step(
            check_in,
            "alarm",
            at=wake.astimezone(stay.hotel_timezone).isoformat(),
            mode="light" if preferences.gentle_alarm else "both",
            window_minutes=0,
        )

        sound_end = min(wake, bedtime + timedelta(minutes=30))
        if check_in < night_start:
            add_step(check_in, "sound", volume=0)
        if preferences.favorite_track_ids and night_start < sound_end:
            remaining = (sound_end - night_start).total_seconds() / 60
            add_step(
                night_start,
                "sound",
                track_id=preferences.favorite_track_ids[0],
                volume=20,
                timer_minutes=max(1, int(remaining)),
            )
            # The simulator stores the timer value without scheduling a volume change.
            add_step(sound_end, "sound", volume=0)
        elif check_in == night_start:
            add_step(night_start, "sound", volume=0)

        steps.sort(key=lambda step: step.at.astimezone(UTC))
        scenario = tuple(steps)
        _validate_steps(scenario, check_in, wake, catalog)
        return scenario


def resolve_bedtime(stay: Stay, preferences: ResolvedPreferences) -> datetime:
    wake = stay.target_wake_at.astimezone(UTC)
    hotel_zone = stay.hotel_timezone
    home_zone = preferences.home_timezone or hotel_zone
    wake_date = wake.astimezone(home_zone).date()
    # Anchor the habitual wall time to its home date before converting to the hotel zone.
    candidates = (
        _adjust_bedtime(
            datetime.combine(wake_date + timedelta(days=days), preferences.bedtime, tzinfo=home_zone),
            hotel_zone,
        )
        for days in (-2, -1, 0, 1)
    )
    return max(candidate for candidate in candidates if candidate < wake)


def _adjust_bedtime(home_bedtime: datetime, hotel_zone: ZoneInfo) -> datetime:
    instant = home_bedtime.astimezone(UTC)
    delta = instant.astimezone(hotel_zone).utcoffset() - home_bedtime.utcoffset()
    if abs(delta) >= timedelta(hours=3):
        instant += timedelta(hours=-1 if delta > timedelta(0) else 1)
    return instant


_format_checker = FormatChecker()
_local_schema_registry = Registry()


def _validate_steps(
    steps: tuple[ScenarioStep, ...],
    start: datetime,
    end: datetime,
    catalog: Mapping[str, Mapping[str, DeviceField]],
) -> None:
    validators: dict[tuple[str, str], Draft202012Validator] = {}
    for step in steps:
        if not start <= step.at.astimezone(UTC) <= end:
            raise SleepPlanError("scenario step outside stay interval")
        for field_id, value in step.values.items():
            key = step.device_id, field_id
            field = catalog.get(step.device_id, {}).get(field_id)
            if field is None:
                raise SleepPlanError("scenario uses an unknown device or field")
            try:
                if key not in validators:
                    schema = dict(field.value_schema)
                    Draft202012Validator.check_schema(schema)
                    validators[key] = Draft202012Validator(
                        schema, format_checker=_format_checker, registry=_local_schema_registry
                    )
                valid = validators[key].is_valid(value)
            except (SchemaError, Unresolvable):
                raise SleepPlanError("invalid device field schema") from None
            if not valid:
                raise SleepPlanError("scenario value outside device field schema")
