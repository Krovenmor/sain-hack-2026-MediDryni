"""Speech adapter for the team's model proxy."""

import httpx

from concierge_service.ports.voice import VoiceError


class VoiceProxy:
    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}

    async def transcribe(self, audio: bytes, filename: str, content_type: str) -> str:
        response = await self._post(
            "/audio/transcriptions",
            data={"model": "gpt-transcribe", "language": "ru", "response_format": "json"},
            files={"file": (filename, audio, content_type)},
        )
        try:
            result = response.json()
        except ValueError:
            raise VoiceError(502, "voice_unavailable") from None
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str):
            raise VoiceError(502, "voice_unavailable")
        if not text.strip():
            raise VoiceError(422, "no_speech")
        return text.strip()

    async def synthesize(self, text: str) -> bytes:
        response = await self._post(
            "/audio/speech",
            json={"model": "tts-1", "voice": "alloy", "input": text, "response_format": "mp3"},
        )
        if not response.content or response.headers.get("content-type", "").startswith("application/json"):
            raise VoiceError(502, "voice_unavailable")
        return response.content

    async def _post(self, path: str, **payload) -> httpx.Response:
        try:
            response = await self.client.post(self.base_url + path, headers=self.headers, timeout=60, **payload)
        except httpx.HTTPError:
            raise VoiceError(502, "voice_unavailable") from None
        if response.is_success:
            return response
        codes = {402: "voice_budget", 413: "audio_too_large", 429: "voice_rate_limit"}
        if response.status_code in codes:
            raise VoiceError(response.status_code, codes[response.status_code])
        raise VoiceError(502, "voice_unavailable")
