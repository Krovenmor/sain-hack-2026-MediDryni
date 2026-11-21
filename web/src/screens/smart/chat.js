export const quickPhrases = ["Не беспокоить", "Я проснулся", "Как вчера", "Я ложусь", "Как дома"];

const recordingExtensions = {
  "audio/mp4": "m4a", "video/mp4": "m4a", "audio/x-m4a": "m4a",
  "audio/webm": "webm", "video/webm": "webm", "audio/ogg": "ogg", "audio/mpeg": "mp3",
  "audio/wav": "wav", "audio/x-wav": "wav", "audio/flac": "flac",
};

export function recordingUpload(recording) {
  const type = recording.type.split(";", 1)[0].trim().toLowerCase();
  const extension = recordingExtensions[type];
  if (!extension || !recording.size || recording.size > 5_000_000) {
    const error = new Error("Invalid recording");
    error.code = !recording.size ? "invalid_audio"
      : recording.size > 5_000_000 ? "audio_too_large" : "unsupported_audio";
    throw error;
  }
  const body = new FormData();
  body.append("file", recording, `recording.${extension}`);
  return body;
}

export function turnsFromMessages(items) {
  const turns = new Map();
  for (const item of items) {
    const id = item.client_message_id;
    const turn = turns.get(id) ?? { id };
    turns.set(id, item.role === "user" ? { ...turn, text: item.text } : { ...turn, reply: item.text, replyId: item.id });
  }
  return [...turns.values()].filter(turn => turn.text && turn.reply);
}
