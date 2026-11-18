import asyncio
import json
import sys

import httpx

from ai_service.infra.llm_client import LlmClient, LlmClientError


async def check(http: httpx.AsyncClient) -> dict[str, object]:
    result = await LlmClient.from_env(http).complete(
        [{"role": "user", "content": "Ответь одним словом: ОК."}],
        max_tokens=1100,
        timeout_s=30,
    )
    usage = result.usage or {}
    return {
        "status": "ok",
        **{
            name: usage[name]
            for name in ("prompt_tokens", "completion_tokens", "total_tokens")
            if type(usage.get(name)) is int
        },
    }


async def _run() -> dict[str, object]:
    async with httpx.AsyncClient() as http:
        return await check(http)


def main() -> int:
    try:
        report = asyncio.run(_run())
    except KeyError as error:
        print(f"Missing environment variable: {error.args[0]}", file=sys.stderr)
        return 1
    except LlmClientError as error:
        report = {"status": "error", "code": error.code, "http_status": error.http_status}
        print(json.dumps(report), file=sys.stderr)
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
