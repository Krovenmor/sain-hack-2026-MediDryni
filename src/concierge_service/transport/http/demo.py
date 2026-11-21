"""Public entry form, client admission and cookie-protected Vite assets."""

import re
import secrets
from pathlib import Path
from time import monotonic
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from concierge_service.domain.dataset import DatasetError
from concierge_service.domain.sleep import SleepPlanError
from concierge_service.infra.wire import Identifier
from concierge_service.ports.hack_api import HackApiError
from concierge_service.ports.room import RoomApiError
from concierge_service.service.demo import DemoService


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
            response = JSONResponse(result, headers=_PRIVATE)
        except HackApiError as exc:
            response = JSONResponse({"detail": {"code": exc.code}}, exc.status_code, headers=_PRIVATE)
        except (RoomApiError, DatasetError, SleepPlanError):
            response = JSONResponse({"detail": {"code": "invalid_room_or_plan"}}, 502, headers=_PRIVATE)
        response.set_cookie(_COOKIE, ticket, httponly=True, secure=True, samesite="strict", path="/demo")
        return response

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
