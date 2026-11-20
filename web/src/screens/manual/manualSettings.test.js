import assert from "node:assert/strict";
import test from "node:test";
import { initialManualSettings } from "./manualSettings.js";

test("each period starts with its own planned temperature", () => {
  const periods = [23, 19, 21].map(temperature => ({
    temperature, light: { kelvin: 2700, brightness: 30 },
  }));
  const settings = initialManualSettings({ periods, wakeUp: { time: "05:30" } });
  assert.deepEqual(settings.map(period => period.climate), [23, 19, 21]);
});
