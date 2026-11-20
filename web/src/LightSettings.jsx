import { useState } from "react";
import send from "../assets/send.svg";
import microphone from "../assets/microphone.svg";
import { lightModes } from "./LightBeam.jsx";

export function LightSettings({ mode, onModeChange }) {
  const [showModes, setShowModes] = useState(true);

  return (
    <section className="light-settings" aria-labelledby="light-title">
      <h1 id="light-title" className="light-title">Ваши<br />пожелания?</h1>
      <div className="light-controls">
        <div className="light-mode-row">
          <fieldset className="light-modes" id="light-modes" hidden={!showModes}>
            <legend className="visually-hidden">Цвет света</legend>
            {lightModes.map(({ id, name, swatch }) => (
              <label key={id} style={{ "--swatch": swatch }}>
                <input type="radio" name="light-color" value={id} checked={mode === id}
                  onChange={() => onModeChange(id)} />
                <span>{name}</span>
              </label>
            ))}
          </fieldset>
          <button className="light-mode-toggle" type="button" aria-label="Цвет света"
            aria-expanded={showModes} aria-controls="light-modes" onClick={() => setShowModes(open => !open)}>
            <span /><span />
          </button>
        </div>
        <div className="light-composer">
          <input aria-label="Сообщение" placeholder="Сообщение" disabled />
          <button type="button" aria-label="Отправить сообщение" disabled><img src={send} alt="" /></button>
          <button type="button" aria-label="Голосовой ввод" disabled><img src={microphone} alt="" /></button>
        </div>
      </div>
    </section>
  );
}
