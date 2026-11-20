import { useId, useRef, useState } from "react";
import orb from "../assets/orb.png";
import menuIcon from "../assets/menu.svg";
import cardRule from "../assets/card-rule.svg";
import timeRule from "../assets/time-rule.svg";
import { LightBeam } from "./LightBeam.jsx";
import { LightSettings } from "./LightSettings.jsx";
import { Scenario } from "./Scenario.jsx";

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
          <button className="pill dark show-plan" onClick={onShowPlan}>
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

function Navigation({ dialogRef, screen, onNavigate }) {
  return (
    <dialog ref={dialogRef} className="drawer" id="menu" aria-labelledby="menu-title" onClick={closeOnBackdrop}>
      <button className="menu-button close-menu" aria-label="Закрыть навигацию" onClick={() => dialogRef.current.close()}>
        <img src={menuIcon} alt="" />
      </button>
      <h2 id="menu-title">Навигация</h2>
      <nav aria-label="Разделы">
        {[
          ["home", "Главная"],
          ["scenario", "Сценарий"],
          ["light", "Умная настройка"],
        ].map(([id, name]) => (
          <button key={id} aria-current={screen === id ? "page" : undefined} onClick={() => onNavigate(id)}>
            {name}
          </button>
        ))}
        <button disabled>Ручная настройка</button>
      </nav>
    </dialog>
  );
}

export function Welcome({ stay }) {
  const [selected, setSelected] = useState(0);
  const [scenarioPeriod, setScenarioPeriod] = useState(0);
  const [screen, setScreen] = useState("home");
  const [lightMode, setLightMode] = useState("warm");
  const menu = useRef(null);
  function navigate(nextScreen) {
    if (menu.current.open) menu.current.close();
    if (nextScreen === screen) return;
    setScreen(nextScreen);
    window.scrollTo(0, 0);
  }
  function showPlan(index) {
    setScenarioPeriod(index);
    navigate("scenario");
  }

  return (
    <>
      <main className={`welcome ${screen}-screen`}>
        <LightBeam mode={lightMode} />
        {screen === "home" && <div className="sky" aria-hidden="true">
          <img className="orb orb-four" src={orb} alt="" />
          <img className="orb orb-five" src={orb} alt="" />
        </div>}
        <header className="topbar">
          <button className="menu-button" id="open-menu" aria-label="Открыть навигацию"
            aria-haspopup="dialog" onClick={() => menu.current.showModal()}>
            <img src={menuIcon} alt="" />
          </button>
          <span className="hotel">Партнёрский отель<br />сна</span>
        </header>
        {screen === "light" && (
          <LightSettings mode={lightMode} onModeChange={setLightMode} />
        )}
        {screen === "scenario" && (
          <Scenario periods={stay.periods} selected={scenarioPeriod} onSelect={setScenarioPeriod} wakeUp={stay.wakeUp} />
        )}
        {screen === "home" && (
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
                <button className="pill" onClick={() => navigate("light")}>Умная настройка</button>
              </div>
              <div className="periods" id="periods">
                {stay.periods.slice(0, 3).map((period, index) => (
                  <PeriodCard key={period.name} period={period} expanded={selected === index}
                    onSelect={() => setSelected(index)} onShowPlan={() => showPlan(index)} />
                ))}
              </div>
            </section>
          </>
        )}
      </main>
      <Navigation dialogRef={menu} screen={screen} onNavigate={navigate} />
    </>
  );
}
