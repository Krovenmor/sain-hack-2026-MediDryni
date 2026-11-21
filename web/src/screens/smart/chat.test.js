import assert from "node:assert/strict";
import test from "node:test";
import { recordingUpload, turnsFromMessages } from "./chat.js";

test("history keeps turns in server order", () => {
  const turn = (id, text, reply) => [
    { client_message_id: id, role: "user", text },
    { id: `${id}.reply`, client_message_id: id, role: "agent", text: reply },
  ];
  assert.deepEqual(turnsFromMessages([...turn("a", "Теплее", "Готово"), ...turn("b", "Потише", "Убавил")]), [
    { id: "a", text: "Теплее", reply: "Готово", replyId: "a.reply" },
    { id: "b", text: "Потише", reply: "Убавил", replyId: "b.reply" },
  ]);
});

test("voice upload identifies Safari M4A and Chrome WebM", () => {
  for (const [type, filename] of [
    ["audio/mp4;codecs=mp4a.40.2", "recording.m4a"],
    ["audio/webm;codecs=opus", "recording.webm"],
  ]) {
    const audio = new Blob(["voice"], { type });
    const file = recordingUpload(audio).get("file");
    assert.equal(file.name, filename);
    assert.equal(file.size, audio.size);
  }
});

test("voice upload rejects empty, oversized and unsupported recordings", () => {
  assert.throws(() => recordingUpload(new Blob()), { code: "invalid_audio" });
  assert.throws(() => recordingUpload(new Blob([new Uint8Array(5_000_001)], { type: "audio/mp4" })),
    { code: "audio_too_large" });
  assert.throws(() => recordingUpload(new Blob(["voice"], { type: "image/png" })), { code: "unsupported_audio" });
});
