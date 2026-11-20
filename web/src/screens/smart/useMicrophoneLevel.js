import { useEffect, useState } from "react";

export function useMicrophoneLevel(enabled, orb) {
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    if (!enabled) return;

    const image = orb.current;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let disposed = false;
    let stream;
    let context;
    let frame;

    function release() {
      cancelAnimationFrame(frame);
      image.style.removeProperty("--voice-scale");
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
        context = new AudioContext();
        const analyser = context.createAnalyser();
        analyser.fftSize = 1024;
        context.createMediaStreamSource(stream).connect(analyser);
        await context.resume();
        if (disposed || !stream) return;

        const samples = new Float32Array(analyser.fftSize);
        let level = 0;
        function update() {
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length);
          const target = Math.min(1, Math.max(0, (rms - 0.012) / 0.14));
          level += (target - level) * (target > level ? 0.3 : 0.12);
          if (!reducedMotion.matches) image.style.setProperty("--voice-scale", (1 + level * 0.19).toFixed(3));
          frame = requestAnimationFrame(update);
        }
        update();
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

  return enabled ? error : "";
}
