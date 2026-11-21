"""ASGI application module for uvicorn."""

from __future__ import annotations

import logging
import os

from concierge_service.di.config import Settings
from concierge_service.di.container import build_container
from concierge_service.transport.http.app import create_app
from concierge_service.transport.http.demo import install_demo

logging.basicConfig(level=logging.INFO)
_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
for _logger_name in ("concierge_service", "ai_service"):
    logging.getLogger(_logger_name).setLevel(_log_level)

_settings = Settings.from_env()
_container = build_container(_settings)

app = create_app(_container.run_case, on_shutdown=_container.aclose)
install_demo(app, _container.demo, _settings.demo_access_code)
