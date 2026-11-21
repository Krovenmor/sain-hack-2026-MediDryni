"""One interactive show per process, with independent simulator rooms."""

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from concierge_service.domain.dataset import DatasetIndex, Stay, build_dataset_index
from concierge_service.domain.scenario import ScenarioStep
from concierge_service.domain.sleep import ResolvedPreferences, SleepPlanner, resolve_bedtime
from concierge_service.ports.ai import AiAgent, AiAgentError, PreferenceHints, ProfileContext
from concierge_service.ports.dataset import ParsedDataset
from concierge_service.ports.hack_api import DemoHackApi, DemoRun, HackApiError
from concierge_service.ports.room import RoomApi, RoomApiError, RoomSession
from concierge_service.service.preference_resolver import resolve_preferences


@dataclass
class DemoPlan:
    stay: Stay
    steps: tuple[ScenarioStep, ...]
    view: dict


@dataclass
class DemoClient:
    dataset_code: str
    plan: DemoPlan
    run: DemoRun
    room: RoomSession | None = None
    published: bool = False

    def ready(self):
        return {"status": "ready", "stay": self.plan.view}


class DemoService:
    def __init__(self, hack: DemoHackApi, rooms: RoomApi, ai: AiAgent, team_token: str):
        self.hack = hack
        self.rooms = rooms
        self.ai = ai
        self.token = team_token
        self.dev_run: DemoRun | None = None
        self.expired_keys: set[str] = set()
        self.clients: dict[str, DemoClient] = {}
        self.datasets: dict[str, tuple[ParsedDataset, DatasetIndex]] = {}
        self.preferences: dict[tuple[str, str], ResolvedPreferences] = {}
        self.plans: dict[tuple[str, str], DemoPlan] = {}
        self.sessions: dict[str, RoomSession] = {}
        # Serialise the cold bootstrap, including reservation and publication. A ready
        # client never takes this lock. Failed publications can reuse the same room.
        self.start_lock = asyncio.Lock()

    def check_expiry(self, key: str):
        if key in self.expired_keys:
            raise HackApiError(410, "show_ended")
        client = self.clients.get(key)
        if client and datetime.now(UTC) >= client.run.expires_at:
            self.end_client(key)

    def end_client(self, key: str):
        client = self.clients.pop(key, None)
        self.expired_keys.add(key)
        # Static-file requests also check expiry while another login may be using the shared plan.
        if client and not self.start_lock.locked() and not any(
            c.dataset_code == client.dataset_code for c in self.clients.values()
        ):
            self.datasets.pop(client.dataset_code, None)
            for cache in (self.plans, self.preferences):
                for entry in [item for item in cache if item[0] == client.dataset_code]:
                    del cache[entry]
        raise HackApiError(410, "show_ended")

    async def dataset(self, code: str) -> tuple[ParsedDataset, DatasetIndex]:
        if code not in self.datasets:
            parsed = await self.hack.get_case2_data(dataset_code=code, token=self.token)
            self.datasets[code] = parsed, build_dataset_index(parsed.dataset)
        return self.datasets[code]

    def existing(self, key: str, code: str, record_id: str | None) -> DemoClient | None:
        client = self.clients.get(key)
        if client and (client.dataset_code != code or record_id not in (None, client.plan.stay.record_id)):
            raise HackApiError(409, "stay_conflict")
        return client

    async def start(self, key: str, code: str, record_id: str | None):
        self.check_expiry(key)
        client = self.existing(key, code, record_id)
        if client and client.published:
            return client.ready()
        async with self.start_lock:
            self.check_expiry(key)
            client = self.existing(key, code, record_id)
            if client is None:
                if not self.token:
                    raise HackApiError(503, "demo_unavailable")
                parsed, index = await self.dataset(code)
                phase = parsed.dataset.meta.phase
                if phase not in ("dev", "final"):
                    raise HackApiError(422, "unsupported_demo_phase")
                stays = index.stays_by_id
                if record_id is None and len(stays) > 1:
                    return {"status": "choose_stay", "stays": [
                        {"stay_record_id": s.record_id, "user_id": s.user_id,
                         "check_in_at": s.check_in_at.astimezone(s.hotel_timezone).isoformat()}
                        for s in stays.values()
                    ]}
                stay = stays.get(record_id) if record_id is not None else next(iter(stays.values()), None)
                if stay is None:
                    raise HackApiError(404, "stay_not_found")
                client = await self.allocate(code, stay, parsed, index)
                self.clients[key] = client
            if not client.published:
                try:
                    await self.publish(key, client)
                except RoomApiError as exc:
                    if exc.http_status == 410:
                        self.end_client(key)
                    self.check_expiry(key)
                    raise
            return client.ready()

    async def allocate(self, code: str, stay: Stay, parsed: ParsedDataset, index: DatasetIndex) -> DemoClient:
        if parsed.dataset.meta.phase == "final":
            plan = await self.plan_for_stay(code, stay, parsed, index)
            run = await self.hack.create_demo_run(stage="final", token=self.token)
            return DemoClient(code, plan, run)

        if self.dev_run is None:
            self.dev_run = await self.hack.create_demo_run(stage="dev", token=self.token)
        if datetime.now(UTC) >= self.dev_run.expires_at:
            raise HackApiError(410, "show_ended")
        _, dev_index = await self.dataset(self.dev_run.dataset_id)
        room = await self.free_room(stay, dev_index)
        plan = await self.plan_for_stay(code, stay, parsed, index)
        return DemoClient(code, plan, self.dev_run, room)

    async def publish(self, key: str, client: DemoClient):
        self.check_expiry(key)
        steps = client.plan.steps
        if client.run.stage == "dev":
            assert client.room is not None
            steps = shifted_steps(client.plan, client.room)
        else:
            parsed, _ = self.datasets[client.dataset_code]
            if client.run.dataset_id != parsed.dataset.meta.dataset_id:
                raise HackApiError(502, "run_dataset_mismatch")
            if client.room is None:
                client.room = await self.rooms.create_session(
                    record_id=client.plan.stay.record_id, kind="stay", token=self.token, run_id=client.run.run_id,
                )
        self.check_expiry(key)
        await self.rooms.load_scenario(session_id=client.room.session_id, steps=steps, token=self.token)
        self.check_expiry(key)
        client.published = True

    async def free_room(self, stay: Stay, index: DatasetIndex) -> RoomSession:
        used = {c.room.record_id for c in self.clients.values() if c.run.stage == "dev" and c.room}
        duration = stay.target_wake_at - stay.check_in_at
        for dev in index.stays_by_id.values():
            if dev.record_id in used:
                continue
            if dev.record_id not in self.sessions:
                self.sessions[dev.record_id] = await self.rooms.create_session(
                    record_id=dev.record_id, kind="stay", token=self.token, run_id=self.dev_run.run_id,
                )
            room = self.sessions[dev.record_id]
            if room.until - room.starts_at >= duration:
                return room
        raise HackApiError(409, "demo_full")

    async def plan_for_stay(self, code: str, stay: Stay, parsed: ParsedDataset, index: DatasetIndex) -> DemoPlan:
        plan_key = code, stay.record_id
        if plan_key in self.plans:
            return self.plans[plan_key]
        user = index.users_by_id[stay.user_id]
        pref_key = code, user.user_id
        if pref_key not in self.preferences:
            source = parsed.source
            profile = ProfileContext(
                user=source.users_by_id[user.user_id],
                events=tuple(source.events_by_user_and_id[user.user_id, e.event_id]
                             for e in index.events_by_user[user.user_id]),
                chat_history=tuple(source.messages_by_user_and_id[user.user_id, m.message_id]
                                   for m in index.chat_history_by_user[user.user_id]),
                devices=source.devices, tracks=source.tracks,
            )
            hints = None
            try:
                results = await self.ai.extract_preference_hints([profile], timeout_s=60)
                if len(results) == 1 and results[0].user_id == user.user_id and isinstance(results[0], PreferenceHints):
                    hints = results[0]
            except (AiAgentError, TimeoutError):
                pass
            self.preferences[pref_key] = resolve_preferences(user, index.tracks_by_id, hints)
        preferences = self.preferences[pref_key]
        steps = SleepPlanner().plan(stay, preferences, index.device_fields_by_device)
        view = stay_view(stay, steps, index, resolve_bedtime(stay, preferences))
        self.plans[plan_key] = DemoPlan(stay, steps, view)
        return self.plans[plan_key]


def shifted_steps(plan: DemoPlan, room: RoomSession) -> list[ScenarioStep]:
    delta = room.starts_at.astimezone(UTC) - plan.stay.check_in_at.astimezone(UTC)

    def shift(at: datetime) -> datetime:
        return (at.astimezone(UTC) + delta).astimezone(room.starts_at.tzinfo)

    steps = []
    for step in plan.steps:
        values = step.values
        if step.device_id == "alarm" and values.get("at"):
            values = dict(values, at=shift(datetime.fromisoformat(values["at"])).isoformat())
        steps.append(replace(step, at=shift(step.at), values=values))
    return steps


def stay_view(stay: Stay, steps: tuple[ScenarioStep, ...], index: DatasetIndex, bedtime: datetime) -> dict:
    state = {device: {field: f.default_value for field, f in fields.items()}
             for device, fields in index.device_fields_by_device.items()}
    # Each card samples the published scenario. Include the evening light change
    # when it exists, and show the last scheduled state on the daytime card.
    evening = next((s.at for s in steps if s.device_id == "light" and s.at < bedtime), stay.check_in_at)
    dawn = next(s.at for s in steps if s.device_id == "curtains" and s.values.get("position") == 100)
    periods = []
    for name, at in zip(("Вечер", "Ночь", "Утро", "День"),
                        (evening, max(bedtime, stay.check_in_at), dawn, stay.target_wake_at), strict=True):
        for step in steps:
            if step.at <= at:
                state[step.device_id].update(step.values)
        periods.append({
            "name": name, "light": {"kelvin": state["light"]["color_temperature_k"],
                                     "brightness": state["light"]["brightness"]},
            "temperature": state["climate"]["temperature_c"], "curtains": state["curtains"]["position"],
            "doNotDisturb": state["dnd"]["enabled"],
        })
    alarm = state["alarm"]
    wake = stay.target_wake_at.astimezone(stay.hotel_timezone)
    track = index.tracks_by_id.get(state["sound"]["track_id"])
    return {
        "stay_record_id": stay.record_id, "guestName": stay.user_id,
        "checkInTime": stay.check_in_at.astimezone(stay.hotel_timezone).strftime("%H:%M"),
        "periods": periods,
        "wakeUp": {"enabled": True, "time": wake.strftime("%H:%M"), "track": track.title if track else "",
                   "volume": state["sound"]["volume"], "soundOn": alarm["mode"] in ("sound", "both"),
                   "alarmLight": alarm["mode"] in ("light", "both"),
                   "dawnMinutes": int((wake.timestamp() - dawn.timestamp()) / 60)},
    }
