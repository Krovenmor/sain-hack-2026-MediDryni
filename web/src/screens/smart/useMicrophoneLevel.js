import { useEffect, useEffectEvent, useRef, useState } from "react";
import { createVoiceActivity } from "./voiceActivity.js";

export function useMicrophoneLevel(enabled, orb, onSilence) {
  const [error, setError] = useState("");
  const stop = useRef(null);
  const finishAfterSilence = useEffectEvent(onSilence);

  useEffect(() => {
    setError("");
    if (!enabled) return;

    if (!window.isSecureContext) {
      setError("Для доступа к микрофону откройте страницу по HTTPS.");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder !== "function") {
      setError("Этот браузер не поддерживает голосовую запись.");
      return;
    }

    const image = orb.current;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let disposed = false;
    let stream;
    let context;
    let recorder;
    let frame;
    const activity = createVoiceActivity();

    function release() {
      cancelAnimationFrame(frame);
      image.style.removeProperty("--voice-scale");
      stop.current = null;
      if (recorder?.state === "recording") recorder.stop();
      stream?.getTracks().forEach(track => track.stop());
      stream = undefined;
      if (context && context.state !== "closed") void context.close().catch(() => {});
      context = undefined;
    }

    function microphoneEnded() {
      release();
      setError("Микрофон отключён");
    }

    async function listen() {
      try {
        const input = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (disposed) {
          input.getTracks().forEach(track => track.stop());
          return;
        }
        stream = input;
        stream.getAudioTracks().forEach(track => track.addEventListener("ended", microphoneEnded, { once: true }));
        const format = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"]
          .find(type => MediaRecorder.isTypeSupported?.(type));
        recorder = new MediaRecorder(stream, format ? { mimeType: format } : undefined);
        const chunks = [];
        const recording = new Promise(resolve => {
          recorder.addEventListener("dataavailable", event => chunks.push(event.data));
          recorder.addEventListener("stop", () => {
            resolve(disposed || !activity.heardSpeech ? null
              : new Blob(chunks, { type: recorder.mimeType || chunks[0]?.type || "" }));
          }, { once: true });
          recorder.addEventListener("error", () => {
            resolve(null);
            if (!disposed) {
              setError("Не удалось записать голосовое");
              release();
            }
          }, { once: true });
        });
        recorder.start();
        stop.current = () => {
          if (recorder.state !== "recording") return null;
          recorder.stop();
          release();
          return recording;
        };
        context = new AudioContext();
        const analyser = context.createAnalyser();
        analyser.fftSize = 1024;
        context.createMediaStreamSource(stream).connect(analyser);
        await context.resume();
        if (disposed || !stream) return;

        const samples = new Float32Array(analyser.fftSize);
        let level = 0;
        function update(now) {
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length);
          const target = Math.min(1, Math.max(0, (rms - 0.012) / 0.14));
          level += (target - level) * (target > level ? 0.3 : 0.12);
          if (!reducedMotion.matches) image.style.setProperty("--voice-scale", (1 + level * 0.19).toFixed(3));
          if (activity.update(rms, now)) {
            finishAfterSilence(stop.current?.());
            return;
          }
          frame = requestAnimationFrame(update);
        }
        frame = requestAnimationFrame(update);
      } catch (cause) {
        if (disposed) return;
        release();
        setError(cause.name === "NotAllowedError" ? "Доступ к микрофону запрещён" : "Микрофон недоступен");
      }
    }

    void listen();
    return () => {
      disposed = true;
      release();
    };
  }, [enabled, orb]);

  return { error: enabled ? error : "", stopRecording: () => stop.current?.() ?? null };
}
