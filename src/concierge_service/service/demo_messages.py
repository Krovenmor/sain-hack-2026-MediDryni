"""Chat history in the interactive demo."""

from dataclasses import dataclass, field
from datetime import datetime

from concierge_service.ports.ai import DialogueMessage
from concierge_service.ports.hack_api import HackApiError


@dataclass
class DemoMessages:
    messages: list[dict] = field(default_factory=list)
    responses: dict[str, tuple[str, dict]] = field(default_factory=dict)

    def page(self, limit: int, before: str | None) -> dict:
        end = len(self.messages)
        if before is not None:
            for index, message in enumerate(self.messages):
                if message["id"] == before:
                    end = index
                    break
            else:
                raise HackApiError(404, "message_not_found")
        start = max(0, end - limit)
        items = self.messages[start:end]
        return {"items": items, "next_before": items[0]["id"] if start else None}

    def cached(self, message_id: str, text: str) -> dict | None:
        if message_id not in self.responses:
            return None
        sent, result = self.responses[message_id]
        if sent != text:
            raise HackApiError(409, "message_conflict")
        return result

    def reply(self, reply_id: str) -> str:
        for message in self.messages:
            if message["id"] == reply_id and message["role"] == "agent":
                return message["text"]
        raise HackApiError(404, "message_not_found")

    def context(self) -> tuple[DialogueMessage, ...]:
        return tuple(DialogueMessage(role=m["role"], at=m["at"], text=m["text"], applied=m.get("applied"))
                     for m in self.messages)

    def append(self, message_id: str, text: str, reply: str, at: datetime, applied: dict, revision: int):
        timestamp = at.isoformat()
        guest = {"id": f"{message_id}.user", "client_message_id": message_id,
                 "role": "user", "at": timestamp, "text": text}
        agent = {"id": f"{message_id}.reply", "client_message_id": message_id,
                 "role": "agent", "at": timestamp, "text": reply, "applied": applied or None}
        self.messages.extend((guest, agent))
        result = {"guest": guest, "reply": agent, "changes": applied, "revision": revision}
        self.responses[message_id] = text, result
        return result
