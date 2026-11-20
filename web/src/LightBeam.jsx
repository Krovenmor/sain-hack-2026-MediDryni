import warm from "../assets/light-warm.svg";
import neutral from "../assets/light-neutral.svg";
import cold from "../assets/light-cold.svg";
import "./light-settings.css";

export const lightModes = [
  { id: "warm", name: "Тёплый", image: warm, swatch: "#f0ad65" },
  { id: "neutral", name: "Нейтральный", image: neutral, swatch: "#fff2da" },
  { id: "cold", name: "Холодный", image: cold, swatch: "#b8e8ff" },
];

export function LightBeam({ mode }) {
  return (
    <div className="light-beam" aria-hidden="true">
      {lightModes.map(({ id, image }) => (
        <img key={id} className="light-color" src={image} alt="" data-active={mode === id} />
      ))}
      <div className="light-air" />
      <div className="light-grain" />
    </div>
  );
}
