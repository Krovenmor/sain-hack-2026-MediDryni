import { useId } from "react";
import cardRule from "../../../assets/card-rule.svg";
import timeRule from "../../../assets/time-rule.svg";
import { DemoTimeSlider } from "../../components/DemoClock.jsx";
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

export function Welcome({ stay, roomClock, currentPeriod, firstPeriod, selected, setSelected, showPlan, showManual, showSmart }) {
  const time = roomClock.clock.now.slice(11, 16);
  return (
    <>
      <section className="arrival" aria-labelledby="greeting">
        <h1 id="greeting">Добро пожаловать,<span id="guest" className="guest">{stay.guestName}</span></h1>
        <div className="demo-time">
          <time dateTime={roomClock.clock.now}>{time}</time>
          <img className="time-rule" src={timeRule} alt="" />
          <span>Демо<br />время</span>
        </div>
        <DemoTimeSlider {...roomClock} />
      </section>
      <section className="room" aria-labelledby="room-title">
        <div className="room-heading">
          <h2 id="room-title">Ваш номер</h2>
          <button className="pill" onClick={showSmart}>Умная настройка</button>
        </div>
        <div className="periods" id="periods">
          {stay.periods.slice(firstPeriod, firstPeriod + 3).map((period, offset) => {
            const index = firstPeriod + offset;
            return (
              <PeriodCard key={period.name} period={period} wakeUp={stay.wakeUp} expanded={selected === index}
                current={currentPeriod === index} onSelect={() => setSelected(index)} onShowPlan={showPlan}
                onShowManual={showManual} />
            );
          })}
        </div>
      </section>
    </>
  );
}
