import { useCallback, useEffect, useRef, useState } from "react";
import { demoError, demoRequest } from "../data/demoApi.js";

export function useDemoClock(initial, visible) {
  const [clock, setClock] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!visible || busy) return;
    const controller = new AbortController();
    let timer;
    async function readClock() {
      try {
        const clock = await demoRequest("/demo/clock", "GET", undefined, { signal: controller.signal });
        if (!controller.signal.aborted) setClock(clock);
      } catch (error) {
        if (!controller.signal.aborted) setMessage(demoError(error));
      }
      if (!controller.signal.aborted) timer = window.setTimeout(readClock, 1000);
    }
    void readClock();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [visible, busy]);

  const update = useCallback(async function setDemoTime(at) {
    setBusy(true);
    setMessage("");
    try {
      setClock(await demoRequest("/demo/clock", "PATCH", { at, paused: false }));
    } catch (error) {
      setMessage(demoError(error));
    } finally {
      setBusy(false);
    }
  }, []);
  return { clock, busy, message, update };
}

export function DemoTimeSlider({ clock, busy, message, update }) {
  const input = useRef(null);
  const [seek, setSeek] = useState(null);
  const min = Date.parse(clock.min);
  const duration = Date.parse(clock.max) - min;
  const elapsed = Date.parse(clock.now) - min;

  useEffect(() => {
    const slider = input.current;
    // React's range onChange fires during drag. The native change fires on commit.
    async function commit(event) {
      const { valueAsNumber } = event.currentTarget;
      await update(new Date(min + valueAsNumber).toISOString());
      setSeek(null);
    }
    slider.addEventListener("change", commit);
    return () => slider.removeEventListener("change", commit);
  }, [min, update]);

  return (
    <div className="demo-time-slider">
      <input ref={input} type="range" aria-label="Перемотка демо времени"
        min={0} max={duration} step="any" value={seek ?? elapsed}
        disabled={busy || duration <= 0} onChange={event => setSeek(event.currentTarget.valueAsNumber)}
        onPointerCancel={() => setSeek(null)} />
      {message && <p role="status">{message}</p>}
    </div>
  );
}
