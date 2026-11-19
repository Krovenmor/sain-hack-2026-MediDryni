"""Run the server on dev and compare simulator journals with the local case 2 reference."""

import argparse
import asyncio
import json
import os
import socket
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import UUID

import httpx

from concierge_service.infra.room import RoomApiAdapter
from concierge_service.ports.room import RoomApiError

parse = datetime.fromisoformat


def flatten(state):
    return {(device, field): value for device, fields in state.items() for field, value in fields.items()}


def request_matches(expected, replies, room_log):
    initial = flatten(room_log.session.initial_state)
    final = flatten(room_log.final_state)
    failed = []
    if [reply["response_class"] for reply in replies] != expected["expected_reply_classes"]:
        failed.append("reply_class")
    checked = set()
    for rule in expected["expectations"]:
        key = rule["device_id"], rule["field_id"]
        checked.add(key)
        value, target = final.get(key), rule.get("value")
        match rule["comparison"]:
            case "unchanged":
                passed = value == initial.get(key)
            case "exact":
                passed = value == target
            case "increase":
                passed = value is not None and value > initial[key]
            case "decrease":
                passed = value is not None and value < initial[key]
            case "within":
                if rule["unit"] == "minutes":
                    passed = isinstance(value, str) and abs(parse(value) - parse(target)) <= timedelta(minutes=rule["tolerance"])
                else:
                    passed = isinstance(value, (int, float)) and abs(value - target) <= rule["tolerance"]
            case _:
                raise ValueError(f"Unknown comparison: {rule['comparison']}")
        if not passed:
            failed.append(".".join(key))
    if expected["preserve_other_fields"] and any(
        initial.get(key) != final.get(key) for key in (initial.keys() | final.keys()) - checked
    ):
        failed.append("unchanged_fields")
    return failed


def replay(log, at):
    state = {device: dict(fields) for device, fields in log.session.initial_state.items()}
    entries = [entry for entry in log.entries if entry.kind == "scenario"]
    for step in entries[-1].steps:
        if step.at <= at:
            state[step.device_id].update(step.values)
    return state


def sleep_matches(expected, log):
    bedtime = parse(expected["resolved_bedtime_at"])
    wake = log.session.until
    check_in = log.session.starts_at
    prefs = expected["resolved_preferences"]
    steps = [entry for entry in log.entries if entry.kind == "scenario"][-1].steps

    def interval(start, end, predicate):
        return all(predicate(replay(log, at)) for at in (
            start, *(step.at for step in steps if start < step.at <= end)
        )) if start <= end else True

    r1 = interval(max(check_in, bedtime - timedelta(hours=1)), bedtime - timedelta(minutes=5),
                  lambda state: 10 <= state["light"]["brightness"] <= 30 and
                  state["light"]["color_temperature_k"] <= 2700)
    night = max(check_in, bedtime + timedelta(minutes=5))
    dawn = wake
    rises = 0
    curtain_start = None
    for at in sorted({step.at for step in steps if bedtime < step.at <= wake}):
        previous = replay(log, at - timedelta(microseconds=1))
        current = replay(log, at)
        brighter = current["light"]["brightness"] > previous["light"]["brightness"]
        opened = current["curtains"]["position"] > previous["curtains"]["position"]
        if brighter or opened:
            if curtain_start is None:
                dawn = at
                curtain_start = previous["curtains"]["position"]
            rises += int(brighter)
    r2 = interval(night, dawn - timedelta(microseconds=1), lambda state:
                  state["light"]["brightness"] == 0 and state["curtains"]["position"] <= 10)
    low, high = {"cool": (17, 18), "normal": (19, 20), "warm": (21, 22)}[prefs["temperature"]]
    r3 = interval(night, wake, lambda state: low <= state["climate"]["temperature_c"] <= high)
    sound = replay(log, night)["sound"]
    tracks = prefs["favorite_track_ids"]
    r4 = (
        sound["track_id"] in tracks and 1 <= sound["volume"] <= 30 and
        1 <= sound["timer_minutes"] <= 45
    ) if tracks else sound["volume"] == 0
    alarm = replay(log, wake - timedelta(minutes=31))["alarm"]
    r5 = (
        isinstance(alarm["at"], str) and abs(parse(alarm["at"]) - wake) <= timedelta(minutes=5)
        and (not prefs["gentle_alarm"] or alarm["mode"] == "light")
    )
    opened_by = replay(log, wake)["curtains"]["position"] - curtain_start if curtain_start is not None else 0
    r6 = timedelta(minutes=15) <= wake - dawn <= timedelta(minutes=30) and (rises >= 3 or opened_by >= 50)
    r7 = interval(night, wake, lambda state: state["dnd"]["enabled"] is True)
    return (r1, r2, r3, r4, r5, r6, r7)


def check_inputs():
    bundle = json.loads(Path("docs/data/dataset.json").read_text())
    dataset, reference = bundle["dataset"], bundle["reference"]
    if dataset["meta"]["dataset_id"] != reference["meta"]["dataset_id"]:
        raise SystemExit("Dataset and reference IDs differ")
    for name in ("stays", "requests"):
        records = {row["record_id"] for row in dataset[name]}
        expected = {row["record_id"] for row in reference[name]}
        if len(records) != len(dataset[name]) or len(expected) != len(reference[name]) or records != expected:
            raise SystemExit(f"Dataset and reference coverage differs: {name}")
    if dataset["meta"]["phase"] != "dev":
        raise SystemExit("Expected a dev dataset")
    return dataset, reference


def read_telemetry(path):
    events = []
    for line in path.read_text().splitlines():
        if ":{" not in line:
            continue
        try:
            events.append(json.loads(line[line.index(":{") + 1:]))
        except ValueError:
            continue
    attempts = [event for event in events if event.get("event") == "llm_attempt"]
    tokens_by_operation = Counter()
    for event in attempts:
        if type(event.get("total_tokens")) is int:
            tokens_by_operation[event["operation"]] += event["total_tokens"]
    return {
        "attempts": len(attempts),
        "responses_with_usage": sum(type(event.get("total_tokens")) is int for event in attempts),
        "total_tokens": sum(event.get("total_tokens") or 0 for event in attempts),
        "tokens_by_operation": dict(tokens_by_operation),
        "outcomes": dict(Counter(event["outcome"] for event in attempts)),
        "finish_reasons": dict(Counter(event["finish_reason"] for event in attempts)),
    }, {
        event["record_id"]: event.get("intent")
        for event in events if event.get("event") == "request_finished"
    }, dict(Counter(
        event["status"] for event in events if event.get("event") == "preference_profile"
    ))


def write_report(directory, report):
    (directory / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))


async def wait_for_start(process, local):
    for _ in range(100):
        if process.returncode is not None:
            break
        try:
            if (await local.get("/health", timeout=0.5)).status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.1)
    raise SystemExit("start.sh health check failed")


async def run_dev(dataset, reference, medcheck_report=None):
    required = ("HACK_API_URL", "HACK_TEAM_TOKEN", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit("Missing environment variables: " + ", ".join(missing))
    api_url = os.environ["HACK_API_URL"].rstrip("/")
    directory = Path(tempfile.mkdtemp(prefix="medidryni-dev-run-"))
    log_path = directory / "server.log"
    print(f"Artifacts: {directory}", flush=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = os.environ | {
        "PATH": f"{Path('.venv/bin').absolute()}:{os.environ['PATH']}",
        "PORT": str(port), "LOG_LEVEL": "DEBUG", "PYTHONDONTWRITEBYTECODE": "1",
    }
    team = {"Authorization": f"Bearer {os.environ['HACK_TEAM_TOKEN']}"}
    async with (
        httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2705) as local,
        httpx.AsyncClient(base_url=api_url, timeout=30, follow_redirects=False) as upstream,
    ):
        with log_path.open("w") as output:
            process = await asyncio.create_subprocess_exec(
                "sh", "start.sh", env=env, stdout=output, stderr=output,
            )
            try:
                await wait_for_start(process, local)
                created = await upstream.post("/runs", json={"case": 2}, headers=team)
                if created.status_code != 201:
                    raise SystemExit(f"POST /runs: HTTP {created.status_code}")
                dev_run = created.json()
                if dev_run["dataset_id"] != dataset["meta"]["dataset_id"]:
                    raise SystemExit("Dev run references another dataset")
                run_token = dev_run["run_token"]
                print("Dev run created, starting /run", flush=True)
                started = monotonic()
                response = await local.post(
                    "/run", json={"case": 2, "dataset_code": dev_run["dataset_id"]},
                    headers={"Authorization": f"Bearer {run_token}"},
                )
                elapsed = monotonic() - started
                if response.status_code != 200:
                    raise SystemExit(f"/run: HTTP {response.status_code}")
                answers = response.json()
                (directory / "answers.json").write_text(json.dumps(answers, ensure_ascii=False))
                rss_line = next(
                    line for line in Path(f"/proc/{process.pid}/status").read_text().splitlines()
                    if line.startswith("VmHWM:")
                )
                peak_rss_kib = int(rss_line.split()[1])
            finally:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()

        usage, intents, preferences = read_telemetry(log_path)
        report = {
            "dev_run_id": dev_run["run_id"],
            "users": len(dataset["users"]), "stays": len(answers["stays"]), "requests": len(answers["requests"]),
            "elapsed_seconds": round(elapsed, 2), "peak_rss_mib": round(peak_rss_kib / 1024, 1),
            "usage": usage, "preference_outcomes": preferences,
        }
        write_report(directory, report)
        validation = await upstream.post("/case/2/validate", json=answers, headers=team)
        report["validate_http_status"] = validation.status_code
        report["valid"] = validation.status_code == 200 and validation.json().get("valid") is True
        write_report(directory, report)
        print(f"/run: {report['elapsed_seconds']}s, {report['peak_rss_mib']} MiB, validate: {report['valid']}", flush=True)

        request_answers = {row["record_id"]: row for row in answers["requests"]}
        stay_answers = {row["record_id"]: row for row in answers["stays"]}
        room = RoomApiAdapter(base_url=api_url, client=upstream)
        errors = []
        request_diffs = {}
        request_ok = 0
        for expected in reference["requests"]:
            record_id = expected["record_id"]
            answer = request_answers.get(record_id)
            if answer is None:
                mismatches = ["missing"]
            else:
                try:
                    room_log = await room.get_log(session_id=UUID(answer["session_id"]), token=run_token)
                    mismatches = request_matches(expected, answer["replies"], room_log)
                except RoomApiError:
                    errors.append(record_id)
                    continue
            if mismatches:
                request_diffs[record_id] = mismatches
            else:
                request_ok += 1

        rule_hits = {f"R{number}": 0 for number in range(1, 8)}
        sleep_diffs = {}
        for expected in reference["stays"]:
            record_id = expected["record_id"]
            answer = stay_answers.get(record_id)
            if answer is None:
                sleep_diffs[record_id] = ["missing"]
                continue
            try:
                room_log = await room.get_log(session_id=UUID(answer["session_id"]), token=run_token)
                matched = sleep_matches(expected, room_log)
            except (RoomApiError, IndexError, KeyError, ValueError):
                errors.append(record_id)
                continue
            sleep_diffs[record_id] = [rule for rule, passed in zip(rule_hits, matched) if not passed]
            for rule, passed in zip(rule_hits, matched):
                rule_hits[rule] += passed

        known_replies = json.loads(medcheck_report.read_text()) if medcheck_report else {}
        texts = {reply["text"] for answer in answers["requests"] for reply in answer["replies"]}
        report.update(
            C=[request_ok, len(reference["requests"])],
            S=[sum(rule_hits.values()), len(rule_hits) * len(reference["stays"])],
            S_by_rule=rule_hits,
            intent_matches=sum(
                intents.get(row["record_id"]) == row["intent_id"] for row in reference["requests"]
            ),
            request_diffs=request_diffs, sleep_diffs=sleep_diffs, evaluation_errors=errors,
            unique_replies=len(texts), clean_replies=sum(known_replies.get(text) == "clean" for text in texts) if medcheck_report else None,
        )
        write_report(directory, report)
        print(json.dumps(report, indent=2), flush=True)
        if errors or not report["valid"] or elapsed >= 45 * 60 or peak_rss_kib >= 512 * 1024:
            raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Create one dev run using the configured LLM endpoint")
    parser.add_argument("--medcheck-report", type=Path, help="Previous mapping of reply text to medcheck verdict")
    args = parser.parse_args()
    dataset, reference = check_inputs()
    if args.live:
        asyncio.run(run_dev(dataset, reference, args.medcheck_report))
    else:
        print(f"Preflight: {len(dataset['users'])} profiles, {len(reference['stays'])} stays, "
              f"{len(reference['requests'])} requests; no network calls")
