"""Environment-backed settings for application assembly."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LlmSettings:
    base_url: str
    api_key: str = field(repr=False)
    model: str


@dataclass(frozen=True)
class Settings:
    hack_api_url: str
    hack_api_timeout_s: float = 120.0
    llm: LlmSettings | None = None

    demo_access_code: str = field(default="", repr=False)
    team_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("HACK_API_URL", "").strip()
        if not url:
            raise RuntimeError("HACK_API_URL is not set")
        names = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
        llm_url, api_key, model = (os.environ.get(name, "").strip() for name in names)
        values = (llm_url, api_key, model)
        if any(values) and not all(values):
            missing = ", ".join(name for name, value in zip(names, values, strict=True) if not value)
            raise RuntimeError(f"Missing LLM settings: {missing}")
        llm = LlmSettings(llm_url, api_key, model) if all(values) else None
        return cls(
            hack_api_url=url.rstrip("/"), llm=llm,
            demo_access_code=os.getenv("DEMO_ACCESS_CODE", "").strip(),
            team_token=os.getenv("HACK_TEAM_TOKEN", "").strip(),
        )
