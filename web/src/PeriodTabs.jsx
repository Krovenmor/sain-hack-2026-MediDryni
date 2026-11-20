import tabsRule from "../assets/scenario-tabs-rule.svg";

export function PeriodTabs({ periods, selected, onSelect, name }) {
  return (
    <>
      <img className="scenario-tabs-rule" src={tabsRule} alt="" />
      <fieldset className="scenario-periods">
        <legend className="visually-hidden">Период сценария</legend>
        {periods.map((period, index) => (
          <label key={period.name}>
            <input className="visually-hidden" type="radio" name={name}
              checked={selected === index} onChange={() => onSelect(index)} />
            <span>{period.name}</span>
          </label>
        ))}
      </fieldset>
    </>
  );
}
