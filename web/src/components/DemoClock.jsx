import { useEffect, useState } from "react";
import { demoError, demoRequest } from "../data/demoApi.js";

function localTime(value, offset) {
  return new Date(value + offset).toISOString().slice(5, 16).replace("T", " ");
}

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

  async function update(changes) {
    setBusy(true);
    setMessage("");
    try {
      setClock(await demoRequest("/demo/clock", "PATCH", changes));
      return true;
    } catch (error) {
      setMessage(demoError(error));
    } finally {
      setBusy(false);
    }
  }
  return { clock, busy, message, update };
}

export function DemoClock({ clock, busy, message, update }) {
  const [seek, setSeek] = useState(null);
  const min = Date.parse(clock.min);
  const max = Date.parse(clock.max);
  const now = Date.parse(clock.now);
  const offset = Date.parse(clock.now.slice(0, 19) + "Z") - Math.floor(now / 1000) * 1000;
  const seconds = Math.floor((max - min) / 1000);
  async function apply(changes) {
    if (await update(changes)) setSeek(null);
  }
  return (
    <section className="demo-clock" aria-label="Время заезда">
      <p>Время в номере <strong>{localTime(now, offset)}</strong></p>
      <div>
        <button type="button" className="pill" disabled={busy || now >= max}
          onClick={() => void apply({ paused: !clock.paused })}>{clock.paused ? "Продолжить" : "Пауза"}</button>
        <label>
          <input type="range" aria-label="Время для перемотки" min="0" max={seconds}
            value={seek ?? Math.floor((now - min) / 1000)} disabled={busy || seconds < 1}
            onChange={event => setSeek(Number(event.target.value))} />
          {seek !== null && <span>{localTime(min + seek * 1000, offset)}</span>}
        </label>
        <button type="button" className="pill" disabled={busy || seek === null}
          onClick={() => void apply({ at: new Date(min + seek * 1000).toISOString() })}>Перемотать</button>
      </div>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
