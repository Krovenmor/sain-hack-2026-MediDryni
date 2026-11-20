import { Fragment, useId, useRef, useState } from "react";
import orb from "../assets/orb.png";
import menuIcon from "../assets/menu.svg";
import cardRule from "../assets/card-rule.svg";
import timeRule from "../assets/time-rule.svg";

const settingsHint = "Будет доступно после подключения управления номером";

function SettingsList({ settings }) {
  return (
    <ul className="settings">
      {settings.map(setting => <li key={setting}>{setting}</li>)}
    </ul>
  );
}

function PeriodCard({ period, expanded, onSelect, onShowPlan }) {
  const contentId = useId();
  return (
    <article className={expanded ? "period expanded" : "period"}>
      <button className="period-toggle" aria-label={period.name}
        aria-controls={contentId} aria-expanded={expanded} onClick={onSelect}>
        {period.name.toLocaleLowerCase("ru")}
      </button>
      <div className="period-content" id={contentId} inert={!expanded}>
        <h3>{period.name}</h3>
        <img className="card-rule" src={cardRule} alt="" />
        <SettingsList settings={period.settings} />
        <div className="period-actions">
          <button className="pill" disabled title={settingsHint}>Ручная настройка</button>
          <button className="pill dark show-plan" aria-haspopup="dialog" onClick={onShowPlan}>
            Полный сценарий
          </button>
        </div>
      </div>
    </article>
  );
}

function closeOnBackdrop(event) {
  const dialog = event.currentTarget;
  if (event.target !== dialog) return;
  const { left, right, top, bottom } = dialog.getBoundingClientRect();
  if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) {
    dialog.close();
  }
}

function Navigation({ dialogRef, onShowPlan }) {
  const close = () => dialogRef.current.close();
  return (
    <dialog ref={dialogRef} className="drawer" id="menu" aria-labelledby="menu-title" onClick={closeOnBackdrop}>
      <button className="menu-button close-menu" aria-label="Закрыть навигацию" onClick={close}>
        <img src={menuIcon} alt="" />
      </button>
      <h2 id="menu-title">Навигация</h2>
      <nav aria-label="Разделы">
        <button className="current" aria-current="page" onClick={close}>Главная</button>
        <button className="show-plan" aria-haspopup="dialog" onClick={onShowPlan}>Сценарий</button>
        <button disabled>Умная настройка</button>
        <button disabled>Ручная настройка</button>
      </nav>
    </dialog>
  );
}

function FullPlan({ dialogRef, periods }) {
  return (
    <dialog ref={dialogRef} className="plan" id="plan" aria-labelledby="plan-title" onClick={closeOnBackdrop}>
      <div className="plan-heading">
        <h2 id="plan-title">Полный сценарий</h2>
        <form method="dialog"><button className="close-plan" aria-label="Закрыть сценарий">×</button></form>
      </div>
      <div id="plan-periods">
        {periods.map(period => (
          <Fragment key={period.name}>
            <h3>{period.name}</h3>
            <SettingsList settings={period.settings} />
          </Fragment>
        ))}
      </div>
    </dialog>
  );
}

export function Welcome({ stay }) {
  const [selected, setSelected] = useState(0);
  const menu = useRef(null);
  const plan = useRef(null);
  function showPlan() {
    if (menu.current.open) menu.current.close();
    plan.current.showModal();
  }

  return (
    <>
      <main className="welcome">
        <div className="sky" aria-hidden="true">
          <img className="orb orb-four" src={orb} alt="" />
          <img className="orb orb-five" src={orb} alt="" />
        </div>
        <header className="topbar">
          <button className="menu-button" id="open-menu" aria-label="Открыть навигацию"
            aria-haspopup="dialog" onClick={() => menu.current.showModal()}>
            <img src={menuIcon} alt="" />
          </button>
          <span className="hotel">Партнёрский отель<br />сна</span>
        </header>
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
            <button className="pill" disabled title={settingsHint}>Умная настройка</button>
          </div>
          <div className="periods" id="periods">
            {stay.periods.map((period, index) => (
              <PeriodCard key={period.name} period={period} expanded={selected === index}
                onSelect={() => setSelected(index)} onShowPlan={showPlan} />
            ))}
          </div>
        </section>
      </main>
      <Navigation dialogRef={menu} onShowPlan={showPlan} />
      <FullPlan dialogRef={plan} periods={stay.periods} />
    </>
  );
}
