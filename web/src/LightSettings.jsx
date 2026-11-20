import { useState } from "react";
import warm from "../assets/light-warm.svg";
import neutral from "../assets/light-neutral.svg";
import cold from "../assets/light-cold.svg";
import send from "../assets/send.svg";
import microphone from "../assets/microphone.svg";
import "./light-settings.css";

const lightModes = [
  { id: "warm", name: "Тёплый", image: warm, swatch: "#f0ad65" },
  { id: "neutral", name: "Нейтральный", image: neutral, swatch: "#fff2da" },
  { id: "cold", name: "Холодный", image: cold, swatch: "#b8e8ff" },
];

export function LightSettings({ mode, onModeChange }) {
  const [showModes, setShowModes] = useState(true);

  return (
    <section className="light-settings" aria-labelledby="light-title">
      <div className="light-beam" aria-hidden="true">
        {lightModes.map(({ id, image }) => (
          <img key={id} className="light-color" src={image} alt="" data-active={mode === id} />
        ))}
        <div className="light-air" />
        <div className="light-grain" />
      </div>
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
