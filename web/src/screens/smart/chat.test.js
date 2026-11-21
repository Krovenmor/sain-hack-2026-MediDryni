import assert from "node:assert/strict";
import test from "node:test";
import { recordingUpload, turnsFromMessages } from "./chat.js";
import { createVoiceActivity } from "./voiceActivity.js";

test("silence and a short noise spike never trigger a voice upload", () => {
  const activity = createVoiceActivity();
  assert.equal(activity.update(0, 0), false);
  assert.equal(activity.update(0.2, 4000), false);
  assert.equal(activity.update(0, 4030), false);
  assert.equal(activity.update(0, 10000), false);
  assert.equal(activity.heardSpeech, false);
});

test("voice auto-send waits for three seconds after the last speech", () => {
  const activity = createVoiceActivity();
  activity.update(0.1, 0);
  activity.update(0.1, 100);
  assert.equal(activity.heardSpeech, true);
  assert.equal(activity.update(0, 3000), false);
  activity.update(0.1, 3050);
  assert.equal(activity.update(0.1, 3150), false);
  assert.equal(activity.update(0, 6149), false);
  assert.equal(activity.update(0, 6150), true);
});

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
