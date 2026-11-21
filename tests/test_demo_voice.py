import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import httpx

from concierge_service.infra.voice import VoiceProxy
from concierge_service.ports.ai import NumericInterpretation, RelativeAmount
from concierge_service.ports.voice import VoiceError
from concierge_service.transport.http.audio_upload import MAX_AUDIO_BYTES

from test_demo_start import DemoFixture


class DemoVoiceTests(DemoFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.voice = SimpleNamespace(
            transcribe=AsyncMock(return_value="сделай на градус прохладнее"),
            synthesize=AsyncMock(return_value=b"ID3fake"),
        )
        self.service.voice = self.voice
        self.service.ai.interpret_request = AsyncMock(return_value=NumericInterpretation(
            record_id="voice-one", intent="cooler", slots=RelativeAmount(mode="relative", amount=1),
        ))
        self.final_dataset()
        for number in (1, 2):
            response = await self.start(number, access_code="show-code", dataset_code="final-code")
            self.assertEqual(response.status_code, 200, response.text)

    async def upload(self, content=b"audio", filename="speech.m4a", number=1):
        return await self.room_request("POST", "/voice/recordings", number,
                                       files={"file": (filename, content, "audio/mp4")})

    async def test_voice_recording_message_history_and_speech_are_isolated(self):
        recording = await self.upload()
        self.assertEqual(recording.status_code, 200, recording.text)
        self.voice.transcribe.assert_awaited_once_with(b"audio", "recording.m4a", "audio/mp4")
        result = recording.json()
        client = self.service.clients[f"{1:064x}"]
        self.assertEqual(client.recordings, {result["recording_id"]: result["text"]})
        payload = {"client_message_id": "voice-one", "recording_id": result["recording_id"]}
        self.assertEqual((await self.room_request("POST", "/messages", 2, json=payload)).status_code, 404)
        responses = await asyncio.gather(*(self.room_request("POST", "/messages", json=payload) for _ in range(2)))
        self.assertEqual(responses[0].status_code, 200, responses[0].text)
        turn = responses[0].json()
        self.assertEqual(responses[1].json(), turn)
        self.assertEqual(turn["guest"]["text"], result["text"])
        self.assertEqual(turn["changes"], {"climate": {"temperature_c": 22}})
        self.assertEqual(self.calls.count(f"/room/{client.room.session_id}/action"), 1)

        self.voice.synthesize.side_effect = VoiceError(429, "voice_rate_limit")
        endpoint = f"/messages/{turn['reply']['id']}/speech"
        denied = await self.room_request("GET", endpoint, 2)
        self.assertEqual(denied.status_code, 404)
        self.voice.synthesize.assert_not_awaited()
        failed = await self.room_request("GET", endpoint)
        self.assertEqual(failed.status_code, 429)
        self.assertEqual((await self.room_request("GET", "/messages")).json()["items"],
                         [turn["guest"], turn["reply"]])

        self.voice.synthesize.reset_mock(side_effect=True)
        pair = await asyncio.gather(*(self.room_request("GET", endpoint) for _ in range(2)))
        self.assertEqual([r.content for r in pair], [b"ID3fake", b"ID3fake"])
        self.assertEqual(pair[0].headers["content-type"], "audio/mpeg")
        self.voice.synthesize.assert_awaited_once_with(turn["reply"]["text"])
        self.assertEqual(self.calls.count(f"/room/{client.room.session_id}/action"), 1)

    async def test_voice_checks_upload_limit_and_message_source(self):
        over = await self.upload(b"a" * (MAX_AUDIO_BYTES + 1))
        self.assertEqual(over.status_code, 413)
        streamed_over = await self.upload(b"a" * (MAX_AUDIO_BYTES + 10_000))
        self.assertEqual(streamed_over.status_code, 413)
        self.assertEqual((await self.upload(filename="speech.txt")).status_code, 415)
        self.assertEqual((await self.upload(b"")).status_code, 422)
        self.assertEqual((await self.room_request("POST", "/voice/recordings", content=b"raw")).status_code, 415)
        self.voice.transcribe.assert_not_awaited()
        exactly = await self.upload(b"a" * MAX_AUDIO_BYTES, filename="speech.webm")
        self.assertEqual(exactly.status_code, 200, exactly.text)
        self.assertEqual(self.voice.transcribe.await_args.args[1:], ("recording.webm", "audio/webm"))
        for fields in ({}, {"text": "test", "recording_id": exactly.json()["recording_id"]}):
            self.assertEqual((await self.room_request("POST", "/messages",
                                                     json={"client_message_id": "voice-one", **fields})).status_code, 422)


class VoiceProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_uses_team_models_and_multipart_filename(self):
        def proxy(request):
            self.assertEqual(request.headers["authorization"], "Bearer test-key")
            if request.url.path == "/v1/audio/transcriptions":
                self.assertIn("multipart/form-data", request.headers["content-type"])
                self.assertIn(b'filename="recording.m4a"', request.content)
                self.assertIn(b'gpt-transcribe', request.content)
                return httpx.Response(200, json={"text": "  Погаси свет.  "})
            self.assertEqual(request.url.path, "/v1/audio/speech")
            self.assertIn(b'"model":"tts-1"', request.content)
            self.assertIn(b'"response_format":"mp3"', request.content)
            return httpx.Response(200, content=b"ID3demo", headers={"content-type": "audio/mpeg"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(proxy)) as client:
            voice = VoiceProxy(client, "https://proxy.test/v1/", "test-key")
            self.assertEqual(await voice.transcribe(b"audio", "recording.m4a", "audio/mp4"), "Погаси свет.")
            self.assertEqual(await voice.synthesize("Готово"), b"ID3demo")
