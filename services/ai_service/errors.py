"""Safe diagnostic reasons for rejected model output."""

import logging

from pydantic import ValidationError

from concierge_service.ports.ai import AiAgentError
from run_telemetry import log_event

logger = logging.getLogger(__name__)
_SCHEMA_FIELDS = frozenset({
    "user_id", "temperature", "light_sensitive", "favorite_track_ids", "gentle_alarm",
    "weekday_bedtime", "weekday_wake_time", "home_timezone", "settings", "context",
    "observed_at", "device_id", "field_id", "value", "sources", "kind", "source_id", "pointer",
    "record_id", "intent", "slots", "mode", "amount", "enabled", "position", "minutes",
    "target_at", "alarm_mode", "window_minutes", "track_id", "volume", "timer_minutes",
    "days_ago", "confirmed_settings", "message_id", "subject", "requested_value", "reason",
})


class InvalidModelOutput(AiAgentError):
    def __init__(self, reason: str) -> None:
        super().__init__("invalid_response")
        self.reason = reason


def validation_reason(error: ValidationError, schema_reason: str) -> str:
    issues = error.errors(include_url=False, include_context=False, include_input=False)
    reason = "model_json" if any(issue["type"] == "json_invalid" for issue in issues) else schema_reason
    if logger.isEnabledFor(logging.DEBUG):
        log_event(
            logger, "llm_schema_rejected", level=logging.DEBUG, reason=reason, issue_count=len(issues),
            issues=[
                {
                    "type": issue["type"],
                    "path": [
                        part if isinstance(part, int) or part in _SCHEMA_FIELDS else "<variant>"
                        for part in issue["loc"]
                    ],
                }
                for issue in issues[:12]
            ],
        )
    return reason
