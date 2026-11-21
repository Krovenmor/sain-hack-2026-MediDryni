import assert from "node:assert/strict";
import test from "node:test";
import { composeDemoVoiceTurn, turnsFromMessages } from "./chat.js";

test("history keeps turns in server order", () => {
  const turn = (id, text, reply) => [
    { client_message_id: id, role: "user", text },
    { client_message_id: id, role: "agent", text: reply },
  ];
  assert.deepEqual(turnsFromMessages([...turn("a", "Теплее", "Готово"), ...turn("b", "Потише", "Убавил")]), [
    { id: "a", text: "Теплее", reply: "Готово" },
    { id: "b", text: "Потише", reply: "Убавил" },
  ]);
});

test("voice placeholder requires a recording", () => {
  assert.equal(composeDemoVoiceTurn(new Blob()), null);
  assert.match(composeDemoVoiceTurn(new Blob(["voice"])).reply, /Распознавание речи пока недоступно/);
});
