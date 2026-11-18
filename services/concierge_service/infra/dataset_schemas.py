"""Wire types for the Hack API Case2Dataset response."""

from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    InstanceOf,
    JsonValue,
    TypeAdapter,
)

from concierge_service.domain.dataset import JSONScalar

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Text = Annotated[str, Field(min_length=1)]
ClockTime = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
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


def _parse_timezone(value: object) -> ZoneInfo:
    if not isinstance(value, str):
        raise ValueError("expected an IANA timezone")
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("expected an IANA timezone") from None


def _unique_values(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("duplicate values")
    return values


def _validate_date(value: str) -> str:
    date.fromisoformat(value)
    return value


Timestamp = Annotated[AwareDatetime, BeforeValidator(_parse_timestamp)]
Timezone = Annotated[InstanceOf[ZoneInfo], BeforeValidator(_parse_timezone)]
DateText = Annotated[
    str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"), AfterValidator(_validate_date)
]
UniqueIdentifiers = Annotated[list[Identifier], AfterValidator(_unique_values)]
Nonnegative = Annotated[float, Field(ge=0)]
Percentage = Annotated[float, Field(ge=0, le=100)]
StepCount = Annotated[int, Field(ge=0)]
ScreenMinutes = Annotated[float, Field(ge=0, le=60)]
Bpm = Annotated[float, Field(gt=0)]
SleepStage = Literal["awake", "light", "deep", "rem"]
WidgetId = Literal[
    "sleep_card", "day_plan", "breathing_timer", "focus_block", "steps_ring",
    "workout_suggest", "meal_log", "habit_check", "diary_prompt", "wind_down",
    "weekly_trend", "restart_card",
]


class WireModel(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )


class MetaPayload(WireModel):
    schema_version: Literal["0.3.0"]
    dataset_id: Identifier
    case_id: Literal[2]
    phase: Literal["dev", "online", "final"]
    version: Text
    generated_at: Timestamp
    history_through: Literal["2026-11-11"]


class GoalPayload(WireModel):
    kind: Literal["sleep", "weight", "energy"]
    description: Text


class SleepPreferencesPayload(WireModel):
    temperature: Literal["cool", "normal", "warm"] | None
    light_sensitive: bool | None
    favorite_track_ids: UniqueIdentifiers | None
    dislikes: list[Text] | None


class UserPayload(WireModel):
    user_id: Identifier
    birth_date: DateText | None
    timezone: Timezone | None
    goal: GoalPayload | None
    chronotype: Literal["early", "intermediate", "late"] | None
    weekday_bedtime: ClockTime | None
    weekday_wake_time: ClockTime | None
    average_sleep_hours: Annotated[float, Field(ge=0, le=24)] | None
    social_jetlag: bool | None
    activity_level: Literal["low", "medium", "high"] | None
    sedentary_hours: Literal["lt6", "6to9", "gt9"] | None
    training_days: Literal["0", "1to2", "3plus"] | None
    works_from_home: bool | None
    phone_before_sleep: bool | None
    movement_index: Literal["lt50", "50to70", "70to85", "85plus"] | None
    sleep_preferences: SleepPreferencesPayload | None
    disabled_widget_ids: Annotated[list[WidgetId], AfterValidator(_unique_values)] | None


class NotePayload(WireModel):
    text: Text


class MealPayload(WireModel):
    kcal: Nonnegative
    protein_g: Nonnegative
    fat_g: Nonnegative
    carbs_g: Nonnegative
    ingredients: list[Text]


class SleepPayload(WireModel):
    duration_minutes: Nonnegative
    quality: Percentage | None
    stage: SleepStage | None


class StepsPayload(WireModel):
    count: StepCount


class HeartRatePayload(WireModel):
    bpm: Bpm


class ConversationPayload(WireModel):
    message_ids: Annotated[UniqueIdentifiers, Field(min_length=1)]


class PhoneScreenPayload(WireModel):
    screen_minutes: ScreenMinutes
    charging: bool


class PresencePayload(WireModel):
    room_id: Identifier
    present: bool
    movement: bool


class WearableSamplePayload(WireModel):
    bpm: Bpm | None
    steps: StepCount | None
    sleep_stage: SleepStage | None


class PhoneSamplePayload(WireModel):
    steps: StepCount | None
    screen_minutes: ScreenMinutes | None
    charging: bool | None


class EventPayload(WireModel):
    event_id: Identifier
    user_id: Identifier
    started_at: Timestamp
    ended_at: Timestamp | None


class NoteEvent(EventPayload):
    type: Literal["note"]
    source: Literal["app", "hotel"]
    payload: NotePayload


class MealEvent(EventPayload):
    type: Literal["meal"]
    source: Literal["app"]
    payload: MealPayload


class SleepEvent(EventPayload):
    type: Literal["sleep"]
    source: Literal["app", "wearable"]
    payload: SleepPayload


class StepsEvent(EventPayload):
    type: Literal["steps"]
    source: Literal["app", "wearable", "phone"]
    payload: StepsPayload


class HeartRateEvent(EventPayload):
    type: Literal["heart_rate"]
    source: Literal["wearable"]
    payload: HeartRatePayload


class CalendarEvent(EventPayload):
    type: Literal["calendar"]
    source: Literal["app"]
    payload: NotePayload


class ConversationEvent(EventPayload):
    type: Literal["conversation"]
    source: Literal["app"]
    payload: ConversationPayload


class PhoneScreenEvent(EventPayload):
    type: Literal["phone_screen"]
    source: Literal["phone"]
    payload: PhoneScreenPayload


class PresenceEvent(EventPayload):
    type: Literal["presence"]
    source: Literal["smart_home"]
    payload: PresencePayload


class WearableSampleEvent(EventPayload):
    type: Literal["wearable_sample"]
    source: Literal["wearable"]
    payload: WearableSamplePayload


class PhoneSampleEvent(EventPayload):
    type: Literal["phone_sample"]
    source: Literal["phone"]
    payload: PhoneSamplePayload


DatasetEvent = Annotated[
    NoteEvent | MealEvent | SleepEvent | StepsEvent | HeartRateEvent | CalendarEvent
    | ConversationEvent | PhoneScreenEvent | PresenceEvent | WearableSampleEvent
    | PhoneSampleEvent,
    Field(discriminator="type"),
]


class ChatMessagePayload(WireModel):
    message_id: Identifier
    user_id: Identifier
    role: Literal["user", "agent"]
    at: Timestamp
    text: Text


class StayPayload(WireModel):
    record_id: Identifier
    user_id: Identifier
    check_in_at: Timestamp
    target_wake_at: Timestamp
    hotel_timezone: Timezone


class UtterancePayload(WireModel):
    role: Literal["user"]
    at: Timestamp
    text: Text


DeviceState = Annotated[dict[Identifier, JSONScalar], Field(min_length=1)]


class RequestPayload(WireModel):
    record_id: Identifier
    user_id: Identifier
    stay_id: Identifier
    at: Timestamp
    initial_state: Annotated[dict[Identifier, DeviceState], Field(min_length=1)]
    utterances: Annotated[list[UtterancePayload], Field(min_length=1, max_length=2)]


class DeviceFieldPayload(WireModel):
    field_id: Identifier
    description: Text
    value_schema: dict[str, JsonValue]
    default_value: JSONScalar
    unit: Text | None


class DevicePayload(WireModel):
    device_id: Identifier
    title: Text
    fields: Annotated[list[DeviceFieldPayload], Field(min_length=1)]


class TrackPayload(WireModel):
    track_id: Identifier
    title: Text


class IntentPayload(WireModel):
    intent_id: Identifier
    description: Text


class SleepRulePayload(WireModel):
    rule_id: Identifier
    version: Text
    description: Text


class DatasetPayload(WireModel):
    meta: MetaPayload
    users: Annotated[list[UserPayload], Field(min_length=1)]
    events: list[DatasetEvent]
    chat_history: list[ChatMessagePayload]
    stays: Annotated[list[StayPayload], Field(min_length=1)]
    requests: list[RequestPayload]
    devices: Annotated[list[DevicePayload], Field(min_length=1)]
    tracks: Annotated[list[TrackPayload], Field(min_length=12, max_length=12)]
    intents: Annotated[list[IntentPayload], Field(min_length=19, max_length=19)]
    sleep_rules: Annotated[list[SleepRulePayload], Field(min_length=8, max_length=8)]


class GetDataPayload(WireModel):
    dataset: InstanceOf[dict]
    reference: InstanceOf[dict] | None
