"""Local AI stand-in until the real adapter is available."""

from collections.abc import Sequence
from typing import cast

from concierge_service.ports.ai import (
    AiAgentError,
    PreferenceHints,
    ProfileContext,
    RequestContext,
    RequestInterpretation,
)


class MockAiAgent:
    async def extract_preference_hints(
        self, items: Sequence[ProfileContext], *, timeout_s: float
    ) -> Sequence[PreferenceHints]:
        return [
            PreferenceHints(
                user_id=cast(str, item.user["user_id"]),
                temperature=None,
                light_sensitive=None,
                favorite_track_ids=None,
                gentle_alarm=None,
                weekday_bedtime=None,
                weekday_wake_time=None,
                home_timezone=None,
                settings=[],
            )
            for item in items
        ]

    async def interpret_request(
        self, item: RequestContext, *, timeout_s: float
    ) -> RequestInterpretation:
        raise AiAgentError("unavailable")
