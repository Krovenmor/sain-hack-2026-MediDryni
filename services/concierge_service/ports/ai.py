"""AI port v1. Behaviour and external input schemas: docs/contracts/ai_agent.md.

Dataset fragments are validated against the Hack API schema by the caller.
Pydantic models below validate AI output, not device actions or business rules.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Generic, Literal, Protocol, TypeAlias, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, TypeAdapter


JsonObject: TypeAlias = dict[str, JsonValue]
Scalar: TypeAlias = str | float | int | bool | None
Identifier: TypeAlias = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
]
LocalTime: TypeAlias = Annotated[
    str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
]


def _valid_timestamp(value: str) -> str:
    datetime.fromisoformat(value)
    return value


# Keep explicit offsets on the wire, as required by the external contract.
OffsetTimestamp: TypeAlias = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2}$"),
    AfterValidator(_valid_timestamp),
]
FiniteNumber: TypeAlias = Annotated[float, Field(allow_inf_nan=False)]
PositiveNumber: TypeAlias = Annotated[
    float, Field(gt=0, allow_inf_nan=False)
]
Intent: TypeAlias = Literal[
    "brighter", "dimmer", "warmer", "cooler", "all_off", "sound_on",
    "quieter", "curtains", "alarm_set", "alarm_shift", "alarm_cancel",
    "going_to_bed", "woke_up", "like_yesterday", "do_not_disturb",
    "like_home", "out_of_scope", "invalid_value", "medical",
]
AiErrorCode: TypeAlias = Literal[
    "timeout", "rate_limited", "unavailable", "invalid_response",
    "unresolved_reference",
]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SourceRef(DTO):
    kind: Literal["profile", "event", "message"]
    source_id: Identifier
    pointer: str = ""


T = TypeVar("T")


class Hint(DTO, Generic[T]):
    value: T
    sources: Annotated[list[SourceRef], Field(min_length=1)]


class ObservedSetting(DTO):
    """A setting found in history, never an instruction to change a device."""

    context: Literal["home", "past_stay"]
    observed_at: OffsetTimestamp | None
    device_id: Identifier
    field_id: Identifier
    value: Scalar
    sources: Annotated[list[SourceRef], Field(min_length=1)]


class PreferenceHints(DTO):
    user_id: Identifier
    temperature: Hint[Literal["cool", "normal", "warm"]] | None
    light_sensitive: Hint[bool] | None
    favorite_track_ids: Hint[list[Identifier]] | None
    gentle_alarm: Hint[bool] | None
    weekday_bedtime: Hint[LocalTime] | None
    weekday_wake_time: Hint[LocalTime] | None
    home_timezone: Hint[str] | None
    settings: list[ObservedSetting]


class PreferenceFailure(DTO):
    user_id: Identifier
    error: AiErrorCode


PreferenceResult: TypeAlias = PreferenceHints | PreferenceFailure


@dataclass(frozen=True, kw_only=True)
class ProfileContext:
    """Validated Case2Dataset fragments for exactly one user; no reference."""

    user: JsonObject
    events: Sequence[JsonObject]
    chat_history: Sequence[JsonObject]
    devices: Sequence[JsonObject]
    tracks: Sequence[JsonObject]


class DialogueMessage(DTO):
    role: Literal["user", "agent"]
    at: OffsetTimestamp
    text: Annotated[str, Field(min_length=1)]


@dataclass(frozen=True, kw_only=True)
class RequestContext:
    """One request, its stay and preceding conversation; no future requests."""

    profile: ProfileContext
    request: JsonObject
    stay: JsonObject
    intents: Sequence[JsonObject]
    conversation: Sequence[DialogueMessage]
    preference_hints: PreferenceHints | None


class EmptySlots(DTO):
    pass


class RelativeAmount(DTO):
    mode: Literal["relative"]
    amount: PositiveNumber | None


class AbsoluteAmount(DTO):
    mode: Literal["absolute"]
    value: FiniteNumber


NumericSlots: TypeAlias = Annotated[
    RelativeAmount | AbsoluteAmount, Field(discriminator="mode")
]


class SoundSlots(DTO):
    track_id: Identifier | None
    volume: FiniteNumber | None
    timer_minutes: FiniteNumber | None


class CurtainsSlots(DTO):
    position: FiniteNumber


class AlarmSetSlots(DTO):
    target_at: OffsetTimestamp
    alarm_mode: Literal["light", "sound", "both"] | None = None
    window_minutes: FiniteNumber | None = None


class AlarmShiftBy(DTO):
    mode: Literal["relative"]
    minutes: FiniteNumber
    alarm_mode: Literal["light", "sound", "both"] | None = None
    window_minutes: FiniteNumber | None = None


class AlarmShiftTo(DTO):
    mode: Literal["absolute"]
    target_at: OffsetTimestamp
    alarm_mode: Literal["light", "sound", "both"] | None = None
    window_minutes: FiniteNumber | None = None


AlarmShiftSlots: TypeAlias = Annotated[
    AlarmShiftBy | AlarmShiftTo, Field(discriminator="mode")
]


class DoNotDisturbSlots(DTO):
    enabled: bool


class InvalidValueSlots(DTO):
    subject: Annotated[str, Field(min_length=1)]
    requested_value: Scalar
    reason: Literal[
        "out_of_range", "unknown_track", "unsupported_mode", "invalid_time",
    ]


class InterpretationBase(DTO):
    record_id: Identifier


class NumericInterpretation(InterpretationBase):
    intent: Literal["brighter", "dimmer", "warmer", "cooler", "quieter"]
    slots: NumericSlots


class SoundInterpretation(InterpretationBase):
    intent: Literal["sound_on"]
    slots: SoundSlots


class CurtainsInterpretation(InterpretationBase):
    intent: Literal["curtains"]
    slots: CurtainsSlots


class AlarmSetInterpretation(InterpretationBase):
    intent: Literal["alarm_set"]
    slots: AlarmSetSlots


class AlarmShiftInterpretation(InterpretationBase):
    intent: Literal["alarm_shift"]
    slots: AlarmShiftSlots


class DoNotDisturbInterpretation(InterpretationBase):
    intent: Literal["do_not_disturb"]
    slots: DoNotDisturbSlots


class InvalidValueInterpretation(InterpretationBase):
    intent: Literal["invalid_value"]
    slots: InvalidValueSlots


class PastSettingsSlots(DTO):
    """Relative lookback for the single external `like_yesterday` intent.

    `None` keeps the default meaning "yesterday". Explicit N supports the
    same general intent expressed as "N days ago" or a past stay whose date
    is known. It is not a new intent.
    """

    days_ago: Annotated[int, Field(ge=0)] | None = None


class PastSettingsInterpretation(InterpretationBase):
    intent: Literal["like_yesterday"]
    slots: PastSettingsSlots


class NoSlotsInterpretation(InterpretationBase):
    intent: Literal[
        "all_off", "alarm_cancel", "going_to_bed", "woke_up",
        "like_home", "out_of_scope", "medical",
    ]
    slots: EmptySlots


RequestInterpretation: TypeAlias = Annotated[
    NumericInterpretation | SoundInterpretation | CurtainsInterpretation
    | AlarmSetInterpretation | AlarmShiftInterpretation
    | DoNotDisturbInterpretation | InvalidValueInterpretation
    | PastSettingsInterpretation | NoSlotsInterpretation,
    Field(discriminator="intent"),
]

# Reuse for JSON parsing and JSON Schema generation in the LLM adapter.
REQUEST_INTERPRETATION_ADAPTER = TypeAdapter(RequestInterpretation)


class AiAgentError(Exception):
    """Expected AI failure. Contains a safe code, never user text or secrets."""

    def __init__(self, code: AiErrorCode) -> None:
        self.code = code
        super().__init__(code)


class AiAgent(Protocol):
    """Async, stateless port. No Hack API calls or device mutations.

    timeout_s is a positive total budget for this call including queueing,
    rate limiting and retries. The caller limits it by the remaining /run time.
    """

    async def extract_preference_hints(
        self, items: Sequence[ProfileContext], *, timeout_s: float
    ) -> Sequence[PreferenceResult]:
        """One result per input user, in input order; [] for empty input.

        Missing knowledge is a successful PreferenceHints with null fields.
        Individual failures are PreferenceFailure; a whole-call failure may
        raise AiAgentError. Never substitute defaults or another user's data.
        """
        ...

    async def interpret_request(
        self, item: RequestContext, *, timeout_s: float
    ) -> RequestInterpretation:
        """One intent for the entire record, or AiAgentError.

        The adapter parses, validates and retries invalid model output once.
        Business fallbacks and final replies belong to the caller.
        """
        ...
