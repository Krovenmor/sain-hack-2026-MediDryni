import { useId } from "react";
import cardRule from "../../../assets/card-rule.svg";
import timeRule from "../../../assets/time-rule.svg";
import { periodSummary } from "../../data/staySettings.js";
import "./welcome.css";

function SettingsList({ settings }) {
  return (
    <ul className="settings">
      {settings.map(setting => <li key={setting}>{setting}</li>)}
    </ul>
  );
}

function PeriodCard({ period, wakeUp, expanded, current, onSelect, onShowPlan, onShowManual }) {
  const contentId = useId();
  return (
    <article className={expanded ? "period expanded" : "period"} data-current={current}>
      <div className="period-inner">
        <button className="period-toggle" aria-label={period.name}
          aria-controls={contentId} aria-expanded={expanded} aria-current={current ? "time" : undefined} onClick={onSelect}>
          {period.name.toLocaleLowerCase("ru")}
        </button>
        <div className="period-content" id={contentId} inert={!expanded}>
          <h3>{period.name}</h3>
          <img className="card-rule" src={cardRule} alt="" />
          <SettingsList settings={periodSummary(period, wakeUp)} />
          <div className="period-actions">
            <button className="pill" onClick={onShowManual}>Ручная настройка</button>
            <button className="pill dark show-plan" onClick={onShowPlan}>
              Полный сценарий
            </button>
          </div>
        </div>
      </div>
      <svg className="period-outline" aria-hidden="true">
        <rect x="1" y="1" rx="19" />
      </svg>
    </article>
  );
}

export function Welcome({ stay, currentPeriod, selected, setSelected, showPlan, showManual, showSmart }) {
  return (
    <>
      <section className="arrival" aria-labelledby="greeting">
        <h1 id="greeting">Добро пожаловать,<span id="guest" className="guest">{stay.guestName}</span></h1>
        <div className="check-in">
          <time id="check-in-time" dateTime={stay.checkInTime}>{stay.checkInTime}</time>
          <img className="time-rule" src={timeRule} alt="" />
          <span>время<br />заезда</span>
        </div>
      </section>
      <section className="room" aria-labelledby="room-title">
        <div className="room-heading">
          <h2 id="room-title">Ваш номер</h2>
          <button className="pill" onClick={showSmart}>Умная настройка</button>
        </div>
        <div className="periods" id="periods">
          {stay.periods.slice(0, 3).map((period, index) => (
            <PeriodCard key={period.name} period={period} wakeUp={stay.wakeUp} expanded={selected === index}
              current={currentPeriod === index} onSelect={() => setSelected(index)} onShowPlan={showPlan}
              onShowManual={showManual} />
          ))}
        </div>
        {currentPeriod === 3 && <p className="home-day">Сейчас день</p>}
      </section>
    </>
  );
}
