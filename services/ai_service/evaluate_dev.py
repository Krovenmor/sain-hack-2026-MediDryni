import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from time import monotonic

import httpx

from ai_service.infra.llm_client import LlmClient
from ai_service.service.agent import LlmAgent
from concierge_service.ports.ai import (
    AiAgent,
    AiAgentError,
    DialogueMessage,
    PreferenceHints,
    ProfileContext,
    RequestContext,
)


async def evaluate(
    http: httpx.AsyncClient, max_users: int, max_requests: int, *,
    dataset_file: Path | None = None,
) -> dict:
    if dataset_file is None:
        response = await http.get(
            f"{os.environ['HACK_API_URL'].rstrip('/')}/case/2/getData",
            params={"dataset_code": "case2-dev-v1"},
            headers={"Authorization": f"Bearer {os.environ['HACK_TEAM_TOKEN']}"},
            timeout=30,
        )
        response.raise_for_status()
        bundle = response.json()
    else:
        bundle = json.loads(dataset_file.read_text(encoding="utf-8"))
    dataset = bundle["dataset"]
    reference = bundle["reference"]
    if dataset["meta"]["phase"] != "dev" or reference is None:
        raise ValueError("dev reference required")
    expected = {
        item["record_id"]: item["intent_id"] for item in reference["requests"]
    }
    requests = dataset["requests"][:max_requests]
    user_ids = list(dict.fromkeys(item["user_id"] for item in requests))[:max_users]
    if not user_ids:
        user_ids = [user["user_id"] for user in dataset["users"][:max_users]]
    selected_ids = set(user_ids)
    users = {user["user_id"]: user for user in dataset["users"]}
    stays = {stay["record_id"]: stay for stay in dataset["stays"]}
    profiles = {
        user_id: ProfileContext(
            user=users[user_id],
            events=[entry for entry in dataset["events"] if entry["user_id"] == user_id],
            chat_history=[
                entry for entry in dataset["chat_history"] if entry["user_id"] == user_id
            ],
            devices=dataset["devices"],
            tracks=dataset["tracks"],
        )
        for user_id in user_ids
    }

    agent = LlmAgent(LlmClient.from_env(http))
    port: AiAgent = agent
    started = monotonic()
    preferences = await port.extract_preference_hints(list(profiles.values()), timeout_s=120)
    hints = {item.user_id: item for item in preferences if isinstance(item, PreferenceHints)}
    preference_seconds = monotonic() - started
    conversations: dict[str, list[DialogueMessage]] = {user_id: [] for user_id in user_ids}
    scores: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    for item in requests:
        user_id = item["user_id"]
        if user_id not in selected_ids:
            continue
        context = RequestContext(
            profile=profiles[user_id],
            request=item,
            stay=stays[item["stay_id"]],
            intents=dataset["intents"],
            conversation=conversations[user_id],
            preference_hints=hints.get(user_id),
        )
        scores["requests"] += 1
        try:
            result = await port.interpret_request(context, timeout_s=45)
        except AiAgentError as error:
            errors[error.code] += 1
        else:
            scores["intent_matches"] += result.intent == expected[item["record_id"]]
        conversations[user_id].extend(
            DialogueMessage.model_validate(utterance) for utterance in item["utterances"]
        )

    return {
        "preference_profiles": len(profiles),
        "preference_successes": len(hints),
        "preference_seconds": round(preference_seconds, 2),
        "requests": scores["requests"],
        "intent_matches": scores["intent_matches"],
        "request_errors": dict(errors),
        "elapsed_seconds": round(monotonic() - started, 2),
        **agent.usage(),
    }


async def _run(max_users: int, max_requests: int, dataset_file: Path | None) -> dict:
    async with httpx.AsyncClient() as http:
        return await evaluate(http, max_users, max_requests, dataset_file=dataset_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate AI intent classification on dev.")
    parser.add_argument("--max-users", type=int, default=2)
    parser.add_argument("--max-requests", type=int, default=8)
    parser.add_argument("--dataset-file", type=Path, help="Read a dev bundle from local JSON.")
    args = parser.parse_args()
    if args.max_users < 1 or args.max_requests < 0:
        parser.error("expected --max-users >= 1 and --max-requests >= 0")
    required = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
    if args.dataset_file is None:
        required += ("HACK_API_URL", "HACK_TEAM_TOKEN")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        parser.error(f"missing environment variables: {', '.join(missing)}")
    try:
        report = asyncio.run(_run(args.max_users, args.max_requests, args.dataset_file))
    except OSError:
        parser.exit(1, "Cannot read the local dev dataset\n")
    except httpx.HTTPStatusError as error:
        parser.exit(1, f"Dev dataset API failed: HTTP {error.response.status_code}\n")
    except (httpx.HTTPError, httpx.InvalidURL):
        parser.exit(1, "Dev dataset API is unavailable\n")
    except (KeyError, ValueError, TypeError):
        parser.exit(1, "Invalid dev dataset or reference\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
