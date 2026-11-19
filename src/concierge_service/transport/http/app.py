"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException

from concierge_service.domain.answers import RunResult
from concierge_service.domain.dataset import DatasetError
from concierge_service.ports.hack_api import HackApiError
from concierge_service.service.run_case import RunCase, RunDeadlineExceeded, UnsupportedCase
from concierge_service.transport.http.schemas import (
    Case2Answers,
    RequestAnswer,
    ReplyAnswer,
    RunRequest,
    StayAnswer,
)


def create_app(
    run_case: RunCase,
    *,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if on_shutdown is not None:
            await on_shutdown()

    app = FastAPI(title="MediDryni case 2 API", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/run", response_model=Case2Answers)
    async def run(
        payload: RunRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Case2Answers:
        token = _bearer_token(authorization)
        try:
            result = await run_case.run(
                case=payload.case,
                dataset_code=payload.dataset_code,
                token=token,
            )
        except UnsupportedCase:
            raise HTTPException(
                status_code=400, detail={"code": "unsupported_case"}
            ) from None
        except DatasetError:
            raise HTTPException(
                status_code=502, detail={"code": "invalid_dataset"}
            ) from None
        except HackApiError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code}
            ) from None
        except RunDeadlineExceeded:
            raise HTTPException(
                status_code=504, detail={"code": "run_deadline_exceeded"}
            ) from None
        return _to_answers(result)

    return app


def _bearer_token(header: str | None) -> str:
    if header is None:
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})
    scheme, separator, token = header.partition(" ")
    token = token.strip()
    if not separator or scheme.lower() != "bearer" or len(token.split()) != 1:
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})
    return token


def _to_answers(result: RunResult) -> Case2Answers:
    return Case2Answers(
        stays=[
            StayAnswer(record_id=stay.record_id, session_id=stay.session_id)
            for stay in result.stays
        ],
        requests=[
            RequestAnswer(
                record_id=request.record_id,
                session_id=request.session_id,
                replies=[
                    ReplyAnswer(
                        role=reply.role,
                        at=reply.at,
                        text=reply.text,
                        response_class=reply.response_class,
                    )
                    for reply in request.replies
                ],
            )
            for request in result.requests
        ],
    )
