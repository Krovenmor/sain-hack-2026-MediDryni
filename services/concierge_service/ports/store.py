"""Conversation history belonging to a single run."""

from collections.abc import Sequence
from typing import Protocol

from concierge_service.ports.ai import DialogueMessage


class ConversationStore(Protocol):
    def history(self, user_id: str) -> tuple[DialogueMessage, ...]: ...

    def append(self, user_id: str, messages: Sequence[DialogueMessage]) -> None: ...
