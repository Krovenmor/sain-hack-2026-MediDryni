import assert from "node:assert/strict";
import test from "node:test";
import { demoStay } from "./demoStay.js";
import { alarmMode, periodSummary, toggleLight, updatePeriod } from "./staySettings.js";
import { composeDemoTurn } from "../screens/smart/chat.js";

test("changing a period updates its card and future replies, preserving the other periods", () => {
  const night = updatePeriod(demoStay, 1, { temperature: 18, doNotDisturb: false, curtains: 15 });
  assert.deepEqual(periodSummary(night.periods[1], night.wakeUp),
    ["Свет: выключен", "Температура: 18 °C", "Шторы: 15 %", "Не беспокоить: выкл."]);
  assert.match(composeDemoTurn("Я ложусь", night).reply, /18 °C/);
  assert.doesNotMatch(composeDemoTurn("Не беспокоить", night).reply, /Ночь/);
  assert.equal(night.periods[0], demoStay.periods[0]);
  assert.equal(demoStay.periods[1].temperature, 19);
});

test("switching the light off and on restores brightness, including initially dark periods", () => {
  const light = demoStay.periods[2].light;
  const off = toggleLight(light, false);
  assert.equal(off.brightness, 0);
  assert.equal(toggleLight(off, true).brightness, light.brightness);
  assert.ok(toggleLight(demoStay.periods[1].light, true).brightness > 0);
});

test("morning summary uses the shared alarm time and its enabled channels", () => {
  const morning = demoStay.periods[2];
  const alarm = { ...demoStay.wakeUp, time: "00:10", soundOn: false };
  assert.deepEqual(periodSummary(morning, alarm),
    ["Рассвет: с 23:40", "Будильник: 00:10", "Режим: свет"]);
  assert.deepEqual(periodSummary(morning, { ...alarm, soundOn: true, alarmLight: false }),
    ["Будильник: 00:10", "Режим: звук"]);
  assert.equal(periodSummary(morning, { ...alarm, enabled: false })[0], "Будильник: выключен");
  assert.equal(alarmMode({ soundOn: false, alarmLight: false }), "без света и звука");
});
