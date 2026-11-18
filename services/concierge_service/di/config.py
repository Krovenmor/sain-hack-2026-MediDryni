"""Environment-backed settings for application assembly."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    hack_api_url: str
    hack_api_timeout_s: float = 120.0

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("HACK_API_URL", "").strip()
        if not url:
            raise RuntimeError("HACK_API_URL is not set")
        return cls(hack_api_url=url.rstrip("/"))
