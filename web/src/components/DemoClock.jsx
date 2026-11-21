import { useEffect, useState } from "react";
import { demoError, demoRequest } from "../data/demoApi.js";

function localTime(value, offset) {
  return new Date(value + offset).toISOString().slice(5, 16).replace("T", " ");
}

export function DemoClock({ initial }) {
  const [clock, setClock] = useState(initial);
  const [seek, setSeek] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const min = Date.parse(clock.min);
  const max = Date.parse(clock.max);
  const now = Date.parse(clock.now);
  const offset = Date.parse(clock.now.slice(0, 19) + "Z") - Math.floor(now / 1000) * 1000;
  const seconds = Math.floor((max - min) / 1000);

  useEffect(() => {
    if (busy) return;
    let active = true;
    async function readClock() {
      try {
        const clock = await demoRequest("/demo/clock");
        if (active) setClock(clock);
      } catch (error) {
        if (active) setMessage(demoError(error));
      }
    }
    void readClock();
    const timer = window.setInterval(readClock, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [busy]);

  async function update(changes) {
    setBusy(true);
    setMessage("");
    try {
      setClock(await demoRequest("/demo/clock", "PATCH", changes));
      setSeek(null);
    } catch (error) {
      setMessage(demoError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="demo-clock" aria-label="Время заезда">
      <p>Время в номере <strong>{localTime(now, offset)}</strong></p>
      <div>
        <button type="button" className="pill" disabled={busy || now >= max}
          onClick={() => void update({ paused: !clock.paused })}>{clock.paused ? "Продолжить" : "Пауза"}</button>
        <label>
          <input type="range" aria-label="Время для перемотки" min="0" max={seconds}
            value={seek ?? Math.floor((now - min) / 1000)} disabled={busy || seconds < 1}
            onChange={event => setSeek(Number(event.target.value))} />
          {seek !== null && <span>{localTime(min + seek * 1000, offset)}</span>}
        </label>
        <button type="button" className="pill" disabled={busy || seek === null}
          onClick={() => void update({ at: new Date(min + seek * 1000).toISOString() })}>Перемотать</button>
      </div>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
