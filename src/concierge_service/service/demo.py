"""One interactive show per process, with independent simulator rooms."""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from concierge_service.domain.action_planner import ActionPlanError, DevicePatch, patch_matches_state
from concierge_service.domain.dataset import DatasetIndex, Request, Stay, Utterance, build_dataset_index
from concierge_service.domain.device_catalog import DeviceCatalog
from concierge_service.domain.scenario import ScenarioStep
from concierge_service.domain.sleep import ResolvedPreferences, SleepPlanner, resolve_bedtime
from concierge_service.ports.ai import AiAgent, AiAgentError, PreferenceHints, ProfileContext, RequestContext
from concierge_service.ports.dataset import ParsedDataset
from concierge_service.ports.hack_api import DemoHackApi, DemoRun, HackApiError
from concierge_service.ports.room import RoomApi, RoomApiError, RoomSession
from concierge_service.service.demo_messages import DemoMessages, requested_period
from concierge_service.service.demo_settings import (
    PERIODS, DemoClock, alarm_steps, combine_period_changes, merge_steps, period_devices, update_periods,
)
from concierge_service.service.preference_resolver import resolve_preferences
from concierge_service.service.request_handler import RequestHandler


@dataclass
class DemoPlan:
    stay: Stay
    steps: tuple[ScenarioStep, ...]
    view: dict
    boundaries: tuple[datetime, ...]
    samples: tuple[datetime, ...]


@dataclass
class DemoClient:
    dataset_code: str
    plan: DemoPlan
    run: DemoRun
    room: RoomSession | None = None
    published: bool = False
    revision: int = 0
    clock: DemoClock = field(init=False)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    period_changes: dict = field(default_factory=dict)
    alarm_changes: dict = field(default_factory=dict)
    messages: DemoMessages = field(default_factory=DemoMessages)
    live_steps: tuple[ScenarioStep, ...] = ()
    personal_steps: tuple[ScenarioStep, ...] | None = None
    personal_view: dict | None = None

    def current(self):
        return (self.personal_view or self.plan.view) | {"clock": self.clock.view(), "revision": self.revision}

    def ready(self):
        return {"status": "ready", "stay": self.current()}


class DemoService:
    def __init__(self, hack: DemoHackApi, rooms: RoomApi, ai: AiAgent, team_token: str):
        self.hack = hack
        self.rooms = rooms
        self.ai = ai
        self.request_handler = RequestHandler(rooms, ai)
        self.hints: dict[tuple[str, str], PreferenceHints | None] = {}
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
            for cache in (self.plans, self.preferences, self.hints):
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
        stay = client.plan.stay
        client.clock = DemoClock(
            stay.check_in_at.astimezone(stay.hotel_timezone),
            stay.target_wake_at.astimezone(stay.hotel_timezone),
        )
        client.published = True

    def get_client_session(self, key: str) -> DemoClient:
        self.check_expiry(key)
        client = self.clients.get(key)
        if client is None or not client.published:
            raise HackApiError(401, "unauthorized")
        return client

    async def update_settings(self, key: str, client: DemoClient, revision: int, periods: dict, alarm: dict):
        async with client.write_lock:
            self.check_expiry(key)
            if revision != client.revision:
                raise HackApiError(409, "revision_conflict")
            if not (periods or alarm):
                return client.current()

            period_changes = combine_period_changes(client.period_changes, {
                period: period_devices(fields) for period, fields in periods.items()
            })
            alarm_changes = client.alarm_changes | alarm
            # A subsequent explicit edit replaces earlier live actions for the same field in that period.
            live_steps = []
            for step in client.live_steps:
                period = PERIODS[self.period_index(client.plan, step.at)]
                replaced = period_devices(periods.get(period, {})).get(step.device_id, {})
                values = {field: value for field, value in step.values.items()
                          if field not in replaced and not (alarm and step.device_id == "alarm")}
                if values:
                    live_steps.append(replace(step, values=values))
            await self.save_schedule(client, period_changes, alarm_changes, tuple(live_steps))
            self.check_expiry(key)
            return client.current()

    def schedule(self, client, period_changes, alarm_changes, live_steps):
        _, index = self.datasets[client.dataset_code]
        plan = client.plan
        catalog = DeviceCatalog(index.device_fields_by_device)
        steps = update_periods(plan.steps, plan.boundaries, period_changes, catalog)
        wake_up = plan.view["wakeUp"]
        overrides = []
        if alarm_changes:
            wake_up, overrides = alarm_steps(plan.stay, wake_up, alarm_changes, index.tracks_by_id)
        steps = merge_steps(steps, (*overrides, *live_steps), catalog)
        samples = list(plan.samples)
        for step in live_steps:
            period = self.period_index(plan, step.at)
            samples[period] = max(samples[period], step.at, key=datetime.timestamp)
        view = stay_view(plan.stay, steps, index, tuple(samples))
        alarm = {}
        for step in steps:
            if step.device_id == "alarm":
                alarm.update(step.values)
        alarm_at = alarm.get("at")
        at = datetime.fromisoformat(alarm_at).astimezone(plan.stay.hotel_timezone) if alarm_at else None
        view["wakeUp"] = wake_up | {
            "enabled": at is not None,
            "time": at.strftime("%H:%M") if at else wake_up["time"],
            "dawnMinutes": max(0, int((at.timestamp() - plan.samples[2].timestamp()) / 60)) if at else 0,
        }
        if alarm.get("mode") and (at or any(step.device_id == "alarm" for step in live_steps)):
            view["wakeUp"].update(soundOn=alarm["mode"] in ("sound", "both"), alarmLight=alarm["mode"] in ("light", "both"))
        return steps, view

    async def save_schedule(self, client, periods, alarm, live_steps):
        steps, view = self.schedule(client, periods, alarm, live_steps)
        scenario = shifted_steps(replace(client.plan, steps=steps), client.room) if client.run.stage == "dev" else steps
        await self.rooms.load_scenario(session_id=client.room.session_id, steps=scenario, token=self.token)
        client.period_changes = periods
        client.alarm_changes = alarm
        client.live_steps = live_steps
        client.personal_steps = steps
        client.personal_view = view
        client.revision += 1

    @staticmethod
    def period_index(plan, at):
        return max(i for i, start in enumerate(plan.boundaries) if start.timestamp() <= at.timestamp())

    async def send_message(self, key, client, message_id, text):
        received_at = client.clock.now()
        async with client.write_lock:
            self.check_expiry(key)
            if cached := client.messages.cached(message_id, text):
                return cached

            plan = client.plan
            period = requested_period(text)
            current = self.period_index(plan, received_at)
            selected = PERIODS.index(period) if period else current
            at = plan.boundaries[selected] if selected > current else received_at
            state = (await self.room_state(client, at))["state"]
            parsed, index = self.datasets[client.dataset_code]
            stay = plan.stay
            source = parsed.source
            record = Request(message_id, stay.user_id, stay.record_id, at, state, (Utterance(received_at, text),))
            context = RequestContext(
                profile=self.profile(parsed, index, stay.user_id),
                request={"record_id": message_id, "user_id": stay.user_id, "stay_id": stay.record_id,
                         "at": at.isoformat(), "initial_state": state,
                         "utterances": [{"role": "user", "at": received_at.isoformat(), "text": text}]},
                stay=source.stays_by_id[stay.record_id], intents=source.intents,
                conversation=client.messages.context(), preference_hints=self.hints[client.dataset_code, stay.user_id],
            )

            async def execute(patches):
                self.check_expiry(key)
                if selected < current:
                    raise ActionPlanError("invalid_value")
                for patch in patches:
                    alarm_at = patch.values.get("at") if patch.device_id == "alarm" else None
                    if alarm_at and datetime.fromisoformat(alarm_at).timestamp() > client.clock.end.timestamp():
                        raise ActionPlanError("invalid_value")
                if selected > current:
                    applied = {patch.device_id: dict(patch.values) for patch in patches}
                    periods = combine_period_changes(client.period_changes, {period: applied})
                    try:
                        await self.save_schedule(client, periods, client.alarm_changes, client.live_steps)
                    except RoomApiError:
                        return "room_error", {}
                    except HackApiError as exc:
                        if exc.code == "empty_period":
                            raise ActionPlanError("invalid_value") from exc
                        raise
                    return "applied", applied
                return await self.apply_live(client, at, patches)

            outcome, reply, applied = await self.request_handler.handle_interactive(
                record, context, index.device_fields_by_device,
                self.preferences[client.dataset_code, stay.user_id], execute,
            )
            if selected < current and outcome == "invalid_value":
                reply = "Этот период уже прошёл. Уточните, когда изменить настройку."
            elif selected > current and outcome == "applied":
                label = {"evening": "вечер", "night": "ночь", "morning": "утро", "day": "день"}[period]
                reply = f"Обновил настройки на {label}."
            return client.messages.append(message_id, text, reply, received_at, applied, client.revision)

    async def apply_live(self, client: DemoClient, at: datetime, patches):
        applied = {}
        stand_at = self.room_time(client, at)
        complete = False
        for patch in patches:
            values = dict(patch.values)
            if patch.device_id == "alarm" and values.get("at"):
                values["at"] = self.room_time(client, datetime.fromisoformat(values["at"])).isoformat()
            room_patch = DevicePatch(patch.device_id, values)
            try:
                snapshot = await self.rooms.apply_action(
                    session_id=client.room.session_id, patch=room_patch, at=stand_at, token=self.token,
                )
                if snapshot.at != stand_at:
                    break
            except RoomApiError:
                break
            confirmed = {field: patch.values[field] for field, value in values.items()
                         if patch_matches_state(DevicePatch(patch.device_id, {field: value}), snapshot.state)}
            if confirmed:
                applied[patch.device_id] = confirmed
            if len(confirmed) != len(values):
                break
        else:
            complete = True

        self.record_live_changes(client, at, applied)
        return ("applied" if complete else "partial" if applied else "room_error"), applied

    def record_live_changes(self, client, at, applied):
        if applied:
            live = (*client.live_steps, *(ScenarioStep(at, device, values) for device, values in applied.items()))
            steps, view = self.schedule(client, client.period_changes, client.alarm_changes, live)
            client.live_steps, client.personal_steps, client.personal_view = live, steps, view
            client.revision += 1

    @staticmethod
    def room_time(client, at):
        if client.run.stage == "final":
            return at
        delta = client.room.starts_at.astimezone(UTC) - client.clock.start.astimezone(UTC)
        return (at.astimezone(UTC) + delta).astimezone(client.room.starts_at.tzinfo)

    async def update_clock(self, key: str, client: DemoClient, at: datetime | None, paused: bool | None):
        async with client.write_lock:
            self.check_expiry(key)
            return client.clock.update(at, paused)

    async def room_state(self, client: DemoClient, at: datetime | None):
        at = at if at is not None else client.clock.now()
        client.clock.validate_time(at)
        stand_at = self.room_time(client, at)
        snapshot = await self.rooms.get_state(session_id=client.room.session_id, at=stand_at, token=self.token)
        state = {device: dict(fields) for device, fields in snapshot.state.items()}
        if client.run.stage == "dev" and state.get("alarm", {}).get("at"):
            delta = stand_at.astimezone(UTC) - at.astimezone(UTC)
            alarm_at = datetime.fromisoformat(state["alarm"]["at"])
            state["alarm"]["at"] = (alarm_at.astimezone(UTC) - delta).astimezone(client.clock.start.tzinfo).isoformat()
        return {"at": at, "state": state}

    async def free_room(self, stay: Stay, index: DatasetIndex) -> RoomSession:
        used = {c.room.record_id for c in self.clients.values() if c.run.stage == "dev" and c.room}
        duration = stay.target_wake_at.astimezone(UTC) - stay.check_in_at.astimezone(UTC)
        for dev in index.stays_by_id.values():
            if dev.record_id in used:
                continue
            if dev.record_id not in self.sessions:
                self.sessions[dev.record_id] = await self.rooms.create_session(
                    record_id=dev.record_id, kind="stay", token=self.token, run_id=self.dev_run.run_id,
                )
            room = self.sessions[dev.record_id]
            if room.until.astimezone(UTC) - room.starts_at.astimezone(UTC) >= duration:
                return room
        raise HackApiError(409, "demo_full")

    async def plan_for_stay(self, code: str, stay: Stay, parsed: ParsedDataset, index: DatasetIndex) -> DemoPlan:
        plan_key = code, stay.record_id
        if plan_key in self.plans:
            return self.plans[plan_key]
        user = index.users_by_id[stay.user_id]
        pref_key = code, user.user_id
        if pref_key not in self.preferences:
            profile = self.profile(parsed, index, user.user_id)
            hints = None
            try:
                results = await self.ai.extract_preference_hints([profile], timeout_s=60)
                if len(results) == 1 and results[0].user_id == user.user_id and isinstance(results[0], PreferenceHints):
                    hints = results[0]
            except (AiAgentError, TimeoutError):
                pass
            self.hints[pref_key] = hints
            self.preferences[pref_key] = resolve_preferences(user, index.tracks_by_id, hints)
        preferences = self.preferences[pref_key]
        steps = SleepPlanner().plan(stay, preferences, index.device_fields_by_device)
        bedtime = resolve_bedtime(stay, preferences)
        dawn = next(s.at for s in steps if s.device_id == "curtains" and s.values.get("position") == 100)
        start = stay.check_in_at.astimezone(stay.hotel_timezone)
        end = stay.target_wake_at.astimezone(stay.hotel_timezone)
        night = max(bedtime, start).astimezone(stay.hotel_timezone)
        evening = next((s.at for s in steps if s.device_id == "light" and s.at < night), start)
        boundaries = (start, night, dawn, end)
        samples = (evening, night, dawn, end)
        view = stay_view(stay, steps, index, samples)
        self.plans[plan_key] = DemoPlan(stay, steps, view, boundaries, samples)
        return self.plans[plan_key]

    @staticmethod
    def profile(parsed, index, user_id):
        source = parsed.source
        return ProfileContext(
            user=source.users_by_id[user_id],
            events=tuple(source.events_by_user_and_id[user_id, e.event_id] for e in index.events_by_user[user_id]),
            chat_history=tuple(source.messages_by_user_and_id[user_id, m.message_id]
                               for m in index.chat_history_by_user[user_id]),
            devices=source.devices, tracks=source.tracks,
        )


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


def stay_view(stay: Stay, steps: tuple[ScenarioStep, ...], index: DatasetIndex, samples: tuple[datetime, ...]) -> dict:
    state = {device: {field: f.default_value for field, f in fields.items()}
             for device, fields in index.device_fields_by_device.items()}
    periods = []
    for name, at in zip(("Вечер", "Ночь", "Утро", "День"), samples, strict=True):
        for step in steps:
            if step.at.timestamp() <= at.timestamp():
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
        "periods": periods, "tracks": [entry.title for entry in index.tracks_by_id.values()],
        "wakeUp": {"enabled": True, "time": wake.strftime("%H:%M"), "track": track.title if track else "",
                   "volume": state["sound"]["volume"], "soundOn": alarm["mode"] in ("sound", "both"),
                   "alarmLight": alarm["mode"] in ("light", "both"),
                   "dawnMinutes": int((wake.timestamp() - samples[2].timestamp()) / 60)},
    }
