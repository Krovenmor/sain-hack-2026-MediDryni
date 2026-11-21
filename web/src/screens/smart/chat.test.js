import assert from "node:assert/strict";
import test from "node:test";
import { demoStay } from "../../data/demoStay.js";
import { composeDemoTurn, quickPhrases } from "./chat.js";

test("empty messages do not create chat turns", () => {
  assert.equal(composeDemoTurn("  \n ", demoStay), null);
});

test("typed messages and all shortcuts produce a local reply", () => {
  for (const text of [...quickPhrases, "Можно потише?"]) {
    const turn = composeDemoTurn(" " + text + " ", demoStay);
    assert.equal(turn.text, text);
    assert.ok(turn.reply.length);
  }
});

test("the night reply reads the current stay", () => {
  const stay = { periods: [{ name: "Ночь", temperature: 18, light: { brightness: 5 } }] };
  assert.match(composeDemoTurn("Я ложусь!", stay).reply, /18 °C.*5 %/);
});
