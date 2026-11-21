export const periodIds = ["evening", "night", "morning", "day"];

function editedFields(draft, saved) {
  return Object.fromEntries(Object.entries(draft).filter(([key, value]) => Object.hasOwn(saved, key) && value !== saved[key]));
}

export function settingsPatch(stay, periodDrafts, alarmDraft) {
  const periods = {};
  for (const [id, draft] of Object.entries(periodDrafts)) {
    const period = stay.periods[periodIds.indexOf(id)];
    const values = editedFields(draft, { ...period, kelvin: period.light.kelvin, brightness: period.light.brightness });
    if (Object.keys(values).length) periods[id] = values;
  }
  return { revision: stay.revision, periods, alarm: editedFields(alarmDraft, stay.wakeUp) };
}

export function applySettingsChanges(stay, { periods, alarm }) {
  return {
    ...stay,
    periods: stay.periods.map((period, index) => {
      const changes = periods[periodIds[index]];
      if (!changes) return period;
      const { kelvin, brightness, previousBrightness, ...fields } = changes;
      return {
        ...period,
        ...fields,
        light: {
          ...period.light,
          kelvin: kelvin ?? period.light.kelvin,
          brightness: brightness ?? period.light.brightness,
          previousBrightness: previousBrightness ?? period.light.previousBrightness,
        },
      };
    }),
    wakeUp: { ...stay.wakeUp, ...alarm },
  };
}

export function updatePeriod(stay, index, changes) {
  return {
    ...stay,
    periods: stay.periods.map((period, i) => i === index ? { ...period, ...changes } : period),
  };
}

export function toggleLight(light, enabled) {
  if (enabled) return { brightness: light.previousBrightness || 30 };
  return { previousBrightness: light.brightness, brightness: 0 };
}

export function formatTime(hour, minute) {
  return [hour, minute].map(value => String(value).padStart(2, "0")).join(":");
}

export function alarmMode({ soundOn, alarmLight }) {
  if (soundOn && alarmLight) return "свет и звук";
  if (soundOn) return "звук";
  if (alarmLight) return "свет";
  return "без света и звука";
}

export function periodSummary(period, wakeUp) {
  const { light, temperature, curtains, doNotDisturb } = period;
  const lightStatus = light.brightness ? `Свет: ${light.kelvin} К` : "Свет: выключен";
  const climate = `Температура: ${temperature} °C`;
  if (period.name === "Ночь") {
    return [lightStatus, climate, `Шторы: ${curtains ? curtains + " %" : "закрыты"}`,
      `Не беспокоить: ${doNotDisturb ? "вкл." : "выкл."}`];
  }
  if (period.name === "Утро") {
    if (!wakeUp.enabled) return ["Будильник: выключен", lightStatus, climate];
    const summary = [`Будильник: ${wakeUp.time}`, `Режим: ${alarmMode(wakeUp)}`];
    if (wakeUp.alarmLight && wakeUp.dawnMinutes) {
      const [hour, minute] = wakeUp.time.split(":").map(Number);
      const dawn = (hour * 60 + minute - wakeUp.dawnMinutes + 1440) % 1440;
      summary.unshift(`Рассвет: с ${formatTime(Math.floor(dawn / 60), dawn % 60)}`);
    }
    return summary;
  }
  return [lightStatus, `Яркость: ${light.brightness} %`, climate];
}
