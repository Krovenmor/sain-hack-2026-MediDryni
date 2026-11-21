"""Speech operations used by the demo."""

from typing import Protocol


class VoiceError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code)


class VoiceApi(Protocol):
    async def transcribe(self, audio: bytes, filename: str, content_type: str) -> str: ...

    async def synthesize(self, text: str) -> bytes: ...
