"""ASGI application module for uvicorn."""

from __future__ import annotations

from concierge_service.di.config import Settings
from concierge_service.di.container import build_container
from concierge_service.transport.http.app import create_app

_container = build_container(Settings.from_env())

app = create_app(_container.run_case, on_shutdown=_container.aclose)
