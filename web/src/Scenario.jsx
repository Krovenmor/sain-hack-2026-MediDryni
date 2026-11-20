import climateRays from "../assets/scenario-climate-rays.png";
import climateRule from "../assets/scenario-climate-rule.svg";
import tabsRule from "../assets/scenario-tabs-rule.svg";
import "./scenario.css";

export function Scenario({ periods, selected, onSelect, wakeUp }) {
  const { name, light, curtains, temperature, doNotDisturb } = periods[selected];
  const lit = light.brightness > 0;

  return (
    <section className="scenario" aria-labelledby="scenario-title">
      <h1 id="scenario-title">Сценарий</h1>
      <img className="scenario-tabs-rule" src={tabsRule} alt="" />
      <fieldset className="scenario-periods">
        <legend className="visually-hidden">Период сценария</legend>
        {periods.map((period, index) => (
          <label key={period.name}>
            <input className="visually-hidden" type="radio" name="scenario-period"
              checked={selected === index} onChange={() => onSelect(index)} />
            <span>{period.name}</span>
          </label>
        ))}
      </fieldset>
      <div className="scenario-details" aria-label={name}>
        <section className="scenario-light" aria-labelledby="scenario-light-title">
          <h2 id="scenario-light-title">Свет</h2>
          <dl className="scenario-metrics">
            <div className="scenario-metric kelvin">
              <dt>Температура</dt>
              <dd>{lit ? `${light.kelvin}К` : "—"}</dd>
            </div>
            <div className="scenario-metric brightness">
              <dt>яркость</dt>
              <dd>{light.brightness}%</dd>
            </div>
            <div className="scenario-metric curtains">
              <dt>степень открытости штор</dt>
              <dd>{curtains}%</dd>
            </div>
          </dl>
        </section>
        <section className="scenario-climate" aria-labelledby="scenario-climate-title">
          <img className="scenario-climate-rays" src={climateRays} alt="" width="116" height="252" />
          <h2 id="scenario-climate-title">Климат</h2>
          <p className="scenario-temperature">{temperature}°С</p>
          <img className="scenario-climate-rule" src={climateRule} alt="" />
          <p className="scenario-dnd">Не<br />беспокоить{!doNotDisturb && <small>выкл.</small>}</p>
        </section>
        <section className="scenario-wakeup" aria-labelledby="scenario-wakeup-title">
          <h2 id="scenario-wakeup-title">РАЗБУДИМ в</h2>
          <time dateTime={wakeUp.time}>{wakeUp.time}</time>
          <p>{wakeUp.description}</p>
        </section>
      </div>
    </section>
  );
}
