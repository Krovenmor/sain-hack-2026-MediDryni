"""Public entry form, client admission and cookie-protected Vite assets."""

import re
import secrets
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictFloat, StrictInt, model_validator

from concierge_service.domain.dataset import DatasetError
from concierge_service.domain.device_catalog import DeviceValueError
from concierge_service.domain.sleep import SleepPlanError
from concierge_service.infra.wire import Identifier
from concierge_service.ports.hack_api import HackApiError
from concierge_service.ports.room import RoomApiError
from concierge_service.ports.voice import VoiceError
from concierge_service.service.demo import DemoClient, DemoService
from concierge_service.transport.http.audio_upload import read_audio


_WEB = Path(__file__).resolve().parents[4] / "web"
_COOKIE = "demo_access"
_PRIVATE = {"Cache-Control": "private, no-store"}


def client_key(authorization: Annotated[str | None, Header()] = None) -> str:
    match = re.fullmatch(r"Bearer ([a-f0-9]{64})", authorization or "", re.IGNORECASE)
    if not match:
        raise HTTPException(401, detail={"code": "unauthorized"})
    return match[1].lower()


class StartClient(BaseModel):
    dataset_code: Identifier
    stay_record_id: Identifier | None = None
    access_code: SecretStr | None = Field(default=None, max_length=256)


class AlarmPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")] = "00:00"
    enabled: Annotated[bool, Field(strict=True)] = False
    soundOn: Annotated[bool, Field(strict=True)] = False
    alarmLight: Annotated[bool, Field(strict=True)] = False
    track: Annotated[str, Field(min_length=1)] = ""
    volume: Annotated[int, Field(strict=True, ge=0, le=100)] = 0


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: Annotated[int, Field(strict=True, ge=0)]
    periods: dict[
        Literal["evening", "night", "morning", "day"],
        dict[Literal["kelvin", "brightness", "temperature", "curtains", "doNotDisturb"], StrictInt | StrictFloat | StrictBool],
    ] = Field(default_factory=dict)
    alarm: AlarmPatch = Field(default_factory=AlarmPatch)


class ClockPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: AwareDatetime | None = None
    paused: Annotated[bool, Field(strict=True)] | None = None


class MessagePost(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_message_id: Annotated[Identifier, Field(max_length=120)]
    text: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    recording_id: Identifier | None = None

    @model_validator(mode="after")
    def exactly_one_source(self):
        if (self.text is None) == (self.recording_id is None):
            raise ValueError("Send exactly one of text and recording_id")
        return self


@contextmanager
def demo_errors():
    try:
        yield
    except HackApiError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code}) from None
    except VoiceError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None
    except DeviceValueError:
        raise HTTPException(422, detail={"code": "invalid_device_settings"}) from None
    except RoomApiError:
        raise HTTPException(502, detail={"code": "room_unavailable"}) from None


class DemoAccess:
    def __init__(self, code: str):
        self.code = code
        self.cookies: dict[str, str] = {}
        self.tickets: dict[str, str] = {}
        self.failures: dict[str, tuple[int, float]] = {}

    def admit(self, key, code, ip):
        if key in self.cookies:
            return self.cookies[key]
        now = monotonic()
        count, end = self.failures.get(ip, (0, now + 60))
        if now >= end:
            count, end = 0, now + 60
        if count >= 5:
            raise HTTPException(429, detail={"code": "too_many_attempts"},
                                headers={"Retry-After": str(max(1, int(end - now) + 1))})
        if not self.code or not secrets.compare_digest(self.code.encode(), code.encode()):
            self.failures = {k: v for k, v in self.failures.items() if v[1] > now}
            self.failures[ip] = (count + 1, end)
            raise HTTPException(403, detail={"code": "access_denied"})
        ticket = secrets.token_urlsafe(32)
        self.cookies[key] = ticket
        self.tickets[ticket] = key
        return ticket


def install_demo(app: FastAPI, service: DemoService, code: str, web: Path = _WEB):
    access = DemoAccess(code)

    @app.get("/", include_in_schema=False)
    async def entry(request: Request):
        if request.url.scheme == "http" and request.url.hostname == "0.0.0.0":
            port = request.url.port
            return RedirectResponse(f"http://localhost{f':{port}' if port else ''}/", headers=_PRIVATE)
        return FileResponse(web / "index.html", headers=_PRIVATE)

    @app.get("/login.js", include_in_schema=False)
    async def login_script():
        return FileResponse(web / "public/login.js", media_type="text/javascript", headers=_PRIVATE)

    @app.post("/api/v1/run_client")
    async def start(payload: StartClient, request: Request, key: Annotated[str, Depends(client_key)]):
        ticket = access.admit(key, payload.access_code.get_secret_value() if payload.access_code else "",
                              request.client.host if request.client else "local")
        try:
            result = await service.start(key, payload.dataset_code, payload.stay_record_id)
            response = JSONResponse(jsonable_encoder(result), headers=_PRIVATE)
        except HackApiError as exc:
            response = JSONResponse({"detail": {"code": exc.code}}, exc.status_code, headers=_PRIVATE)
        except (RoomApiError, DatasetError, SleepPlanError):
            response = JSONResponse({"detail": {"code": "invalid_room_or_plan"}}, 502, headers=_PRIVATE)
        response.set_cookie(_COOKIE, ticket, httponly=True, secure=True, samesite="strict", path="/demo")
        return response

    async def get_client_session(key: Annotated[str, Depends(client_key)]) -> DemoClient:
        with demo_errors():
            return service.get_client_session(key)

    @app.get("/api/v1/stay/current")
    async def current(client: Annotated[DemoClient, Depends(get_client_session)]):
        return client.current()

    @app.patch("/api/v1/stay/current")
    async def save_settings(payload: SettingsPatch, key: Annotated[str, Depends(client_key)],
                            client: Annotated[DemoClient, Depends(get_client_session)]):
        with demo_errors():
            return await service.update_settings(
                key, client, payload.revision, payload.periods, payload.alarm.model_dump(exclude_unset=True),
            )

    @app.get("/api/v1/demo/clock")
    async def read_clock(client: Annotated[DemoClient, Depends(get_client_session)]):
        return client.clock.view()

    @app.patch("/api/v1/demo/clock")
    async def set_clock(payload: ClockPatch, key: Annotated[str, Depends(client_key)],
                        client: Annotated[DemoClient, Depends(get_client_session)]):
        with demo_errors():
            return await service.update_clock(key, client, payload.at, payload.paused)

    @app.get("/api/v1/room/state")
    async def room_state(client: Annotated[DemoClient, Depends(get_client_session)],
                         at: Annotated[AwareDatetime | None, Query()] = None):
        with demo_errors():
            return await service.room_state(client, at)

    @app.get("/api/v1/messages")
    async def read_messages(client: Annotated[DemoClient, Depends(get_client_session)],
                            limit: Annotated[int, Query(ge=1, le=100)] = 30,
                            before: Annotated[Identifier | None, Query()] = None):
        with demo_errors():
            return client.messages.page(limit, before)

    @app.post("/api/v1/messages")
    async def send_message(payload: MessagePost, key: Annotated[str, Depends(client_key)],
                           client: Annotated[DemoClient, Depends(get_client_session)]):
        with demo_errors():
            text = payload.text
            if payload.recording_id is not None:
                text = client.recordings.get(payload.recording_id)
                if text is None:
                    raise HackApiError(404, "recording_not_found")
            return await service.send_message(key, client, payload.client_message_id, text)

    @app.post("/api/v1/voice/recordings")
    async def record_voice(request: Request, key: Annotated[str, Depends(client_key)],
                           client: Annotated[DemoClient, Depends(get_client_session)]):
        audio = await read_audio(request)
        with demo_errors():
            return await service.record_voice(key, client, *audio)

    @app.get("/api/v1/messages/{reply_id}/speech")
    async def speech(reply_id: Identifier, key: Annotated[str, Depends(client_key)],
                     client: Annotated[DemoClient, Depends(get_client_session)]):
        with demo_errors():
            audio = await service.reply_speech(key, client, reply_id)
        return Response(audio, media_type="audio/mpeg", headers=_PRIVATE)

    class PrivateBuild(StaticFiles):
        async def __call__(self, scope, receive, send):
            cookie = Request(scope).cookies.get(_COOKIE)
            if cookie not in access.tickets:
                response = JSONResponse({"detail": "forbidden"}, 403, headers=_PRIVATE)
            else:
                try:
                    service.check_expiry(access.tickets[cookie])
                except HackApiError:
                    response = JSONResponse({"detail": "show_ended"}, 410, headers=_PRIVATE)
                else:
                    response = await self.get_response(self.get_path(scope), scope)
                    response.headers.update(_PRIVATE)
            await response(scope, receive, send)

    app.mount("/demo", PrivateBuild(directory=web / "dist", check_dir=False))
