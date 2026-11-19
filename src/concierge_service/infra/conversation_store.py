"""In-memory conversation history for one run."""

from collections.abc import Sequence

from concierge_service.ports.ai import DialogueMessage


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._messages: dict[str, list[DialogueMessage]] = {}

    def history(self, user_id: str) -> tuple[DialogueMessage, ...]:
        return tuple(self._messages.get(user_id, ()))

    def append(self, user_id: str, messages: Sequence[DialogueMessage]) -> None:
        self._messages.setdefault(user_id, []).extend(messages)
