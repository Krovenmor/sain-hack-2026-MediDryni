"""Resolve interpreted intents into catalog-validated room changes."""

import logging
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from concierge_service.domain.action_planner import (
    ActionPlanError,
    DevicePatch,
    plan_actions,
    validate_patches,
)
from concierge_service.domain.dataset import JSONScalar, Request
from concierge_service.domain.device_catalog import DeviceCatalog, DeviceFields, DeviceValueError
from concierge_service.domain.sleep import ResolvedPreferences, night_settings, wake_settings
from concierge_service.ports.ai import (
    AlarmSetInterpretation,
    AlarmShiftBy,
    AlarmShiftInterpretation,
    ConfirmedMessageSetting,
    CurtainsInterpretation,
    DoNotDisturbInterpretation,
    NumericInterpretation,
    ObservedSetting,
    PastSettingsInterpretation,
    RelativeAmount,
    RequestContext,
    RequestInterpretation,
    SettingContext,
    SourceRef,
    SoundInterpretation,
)
from concierge_service.service.diagnostics import setting_values
from run_telemetry import log_event

logger = logging.getLogger(__name__)


def plan_request(
    request: Request,
    interpreted: RequestInterpretation,
    context: RequestContext,
    preferences: ResolvedPreferences,
    catalog: DeviceFields,
) -> tuple[DevicePatch, ...]:
    intent = interpreted.intent
    if isinstance(interpreted, NumericInterpretation):
        slots = interpreted.slots
        if isinstance(slots, RelativeAmount):
            return plan_actions(intent, request.initial_state, catalog, amount=slots.amount)
        return plan_actions(intent, request.initial_state, catalog, target=slots.value)
    if isinstance(interpreted, (CurtainsInterpretation, DoNotDisturbInterpretation)):
        target = (
            interpreted.slots.position
            if isinstance(interpreted, CurtainsInterpretation)
            else interpreted.slots.enabled
        )
        return plan_actions(intent, request.initial_state, catalog, target=target)

    devices = DeviceCatalog(catalog)
    if isinstance(interpreted, (AlarmSetInterpretation, AlarmShiftInterpretation)):
        changes = {"alarm": _alarm_values(request, interpreted, _hotel_zone(context))}
    elif intent == "alarm_cancel":
        changes = {"alarm": {"at": None}}
    elif isinstance(interpreted, SoundInterpretation):
        try:
            sound = _sound_values(request, interpreted, context, preferences, devices)
        except DeviceValueError as exc:
            raise ActionPlanError("invalid_value") from exc
        changes = {"sound": sound}
    elif intent == "going_to_bed":
        changes = night_settings(preferences)
        observed = _history_values(request, context, scope="bedtime")
        if observed.get("sound", {}).get("volume") == 0:
            changes["sound"] = {"volume": 0}
        for device, values in observed.items():
            changes.setdefault(device, {}).update(values)
        if _has_profile_temperature(context):
            changes["climate"]["temperature_c"] = preferences.night_temperature
    elif intent == "woke_up":
        observed = _history_values(request, context, scope="wake_up")
        changes = (
            {"alarm": {"at": None}, "dnd": {"enabled": False}} if observed else wake_settings()
        )
        for device, values in observed.items():
            changes.setdefault(device, {}).update(values)
    elif intent == "like_home":
        changes = _history_values(request, context, scope="home")
        if _has_profile_temperature(context):
            changes.setdefault("climate", {})["temperature_c"] = preferences.night_temperature
        if not changes:
            raise ActionPlanError("missing_history")
    elif isinstance(interpreted, PastSettingsInterpretation):
        days_ago = interpreted.slots.days_ago
        changes = _history_values(
            request, context, scope="past_stay", days_ago=1 if days_ago is None else days_ago,
            confirmations=interpreted.slots.confirmed_settings,
        )
        if not changes:
            raise ActionPlanError("missing_history")
    else:
        return plan_actions(intent, request.initial_state, catalog)

    patches = tuple(DevicePatch(device, values) for device, values in changes.items())
    return validate_patches(patches, request.initial_state, devices)


def _hotel_zone(context: RequestContext) -> ZoneInfo:
    return ZoneInfo(str(context.stay["hotel_timezone"]))


def _has_profile_temperature(context: RequestContext) -> bool:
    profile = context.profile.user.get("sleep_preferences")
    return isinstance(profile, dict) and profile.get("temperature") is not None


def _alarm_values(
    request: Request,
    interpreted: AlarmSetInterpretation | AlarmShiftInterpretation,
    zone: ZoneInfo,
) -> dict[str, JSONScalar]:
    slots = interpreted.slots
    if isinstance(slots, AlarmShiftBy):
        existing = request.initial_state.get("alarm", {}).get("at")
        if not isinstance(existing, str):
            raise ActionPlanError("missing_state")
        try:
            initial = datetime.fromisoformat(existing)
            if initial.utcoffset() is None:
                raise ValueError("missing offset")
            target = initial.astimezone(UTC) + timedelta(minutes=slots.minutes)
        except ValueError:
            raise ActionPlanError("missing_state") from None
        except OverflowError:
            raise ActionPlanError("invalid_value") from None
    else:
        target = datetime.fromisoformat(slots.target_at)

    if target <= request.at:
        raise ActionPlanError("invalid_value")

    values: dict[str, JSONScalar] = {"at": target.astimezone(zone).isoformat()}
    if slots.alarm_mode is not None:
        values["mode"] = slots.alarm_mode
    if slots.window_minutes is not None:
        values["window_minutes"] = slots.window_minutes
    return values


def _sound_values(
    request: Request,
    interpreted: SoundInterpretation,
    context: RequestContext,
    preferences: ResolvedPreferences,
    catalog: DeviceCatalog,
) -> dict[str, JSONScalar]:
    slots = interpreted.slots
    current = request.initial_state.get("sound", {})
    track = slots.track_id
    if track is None:
        if preferences.favorite_track_ids:
            track = preferences.favorite_track_ids[0]
        else:
            bedtime_sound = _history_values(request, context, scope="bedtime").get("sound", {})
            bedtime_track = bedtime_sound.get("track_id")
            if bedtime_sound.get("volume") != 0 and any(
                track["track_id"] == bedtime_track for track in context.profile.tracks
            ):
                track = bedtime_track
            else:
                track = current.get("track_id") or catalog.field("sound", "track_id").default_value
    volume = slots.volume
    if volume is None:
        current_volume = current.get("volume")
        volume = current_volume if type(current_volume) in (int, float) and current_volume > 0 else 20
    values = {"track_id": track, "volume": volume}
    if slots.timer_minutes is not None:
        values["timer_minutes"] = slots.timer_minutes
    return values


def _history_values(
    request: Request,
    context: RequestContext,
    *,
    scope: SettingContext,
    days_ago: int | None = None,
    confirmations: Sequence[ConfirmedMessageSetting] = (),
) -> dict[str, dict[str, JSONScalar]]:
    hints = context.preference_hints
    settings = list(hints.settings) if hints and hints.user_id == request.user_id else []
    if confirmations:
        timestamps = {
            message["message_id"]: message["at"] for message in context.profile.chat_history
            if message["user_id"] == request.user_id and message["role"] == "agent"
        }
        for fact in confirmations:
            at = timestamps.get(fact.message_id)
            if isinstance(at, str):
                settings.append(ObservedSetting(
                    context="past_stay", observed_at=at, device_id=fact.device_id,
                    field_id=fact.field_id, value=fact.value,
                    sources=[SourceRef(kind="message", source_id=fact.message_id)],
                ))

    zone = _hotel_zone(context)
    request_day = request.at.astimezone(zone).date()
    try:
        target_day = request_day - timedelta(days=days_ago) if days_ago is not None else None
    except OverflowError:
        return {}

    selected: dict[tuple[str, str], tuple[datetime, set[JSONScalar]]] = {}
    skipped: Counter[str] = Counter()
    observations = dated_observations = eligible_observations = 0
    for setting in settings:
        if setting.context != scope:
            continue
        observations += 1
        dated_observations += setting.observed_at is not None
        if (setting.device_id, setting.field_id) == ("alarm", "at") and setting.value is not None:
            skipped["alarm_at"] += 1
            continue
        if scope in ("bedtime", "wake_up") and setting.device_id == "alarm":
            skipped[f"{scope}_alarm"] += 1
            continue
        if (
            scope == "wake_up" and (setting.device_id, setting.field_id) == ("dnd", "enabled")
            and setting.value is not False
        ):
            skipped["wake_up_dnd"] += 1
            continue

        if setting.observed_at is None:
            if target_day is not None:
                skipped["undated"] += 1
                continue
            observed_at = datetime.min.replace(tzinfo=UTC)
        else:
            observed_at = datetime.fromisoformat(setting.observed_at).astimezone(UTC)
            if observed_at > request.at:
                skipped["future_observation"] += 1
                continue
            if target_day is not None and observed_at.astimezone(zone).date() != target_day:
                skipped["different_day"] += 1
                continue

        eligible_observations += 1
        key = setting.device_id, setting.field_id
        previous = selected.get(key)
        if previous is None or observed_at > previous[0]:
            selected[key] = (observed_at, {setting.value})
        elif observed_at == previous[0]:
            previous[1].add(setting.value)

    changes: dict[str, dict[str, JSONScalar]] = {}
    for (device, field), (_, values) in selected.items():
        if len(values) == 1:
            changes.setdefault(device, {})[field] = next(iter(values))
        else:
            skipped["conflicting_values"] += 1
    log_event(
        logger, "history_selection", level=logging.DEBUG, scope=scope, days_ago=days_ago,
        observations=observations, dated_observations=dated_observations,
        eligible_observations=eligible_observations,
        selected_fields=sum(map(len, changes.values())),
        skipped=dict(skipped),
        selected={device: setting_values(values) for device, values in changes.items()},
    )
    return changes
