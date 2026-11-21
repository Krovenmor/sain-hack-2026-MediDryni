const SPEECH_LEVEL = 0.018;
const MIN_SPEECH_MS = 80;
const SILENCE_MS = 3000;

export function createVoiceActivity() {
  let speechStart = null;
  let lastSpeech = null;

  return {
    get heardSpeech() {
      return lastSpeech !== null;
    },
    update(level, now) {
      if (level >= SPEECH_LEVEL) {
        speechStart ??= now;
        if (now - speechStart >= MIN_SPEECH_MS) lastSpeech = now;
      } else {
        speechStart = null;
      }
      return lastSpeech !== null && speechStart === null && now - lastSpeech >= SILENCE_MS;
    },
  };
}
