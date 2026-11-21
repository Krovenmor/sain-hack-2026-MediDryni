import warm from "../../assets/light-warm.svg";
import neutral from "../../assets/light-neutral.svg";
import cold from "../../assets/light-cold.svg";
import "./light-beam.css";

const lightModes = [
  { id: "warm", image: warm },
  { id: "neutral", image: neutral },
  { id: "cold", image: cold },
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
