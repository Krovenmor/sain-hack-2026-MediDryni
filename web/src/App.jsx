import { useRef, useState } from "react";
import orb from "../assets/orb.png";
import menuIcon from "../assets/menu.svg";
import manualMenuIcon from "../assets/manual-menu.svg";
import { LightBeam } from "./components/LightBeam.jsx";
import { useRoomChat } from "./screens/smart/useRoomChat.js";
import { useChatReply } from "./screens/smart/useChatReply.js";
import { LightSettings } from "./screens/smart/LightSettings.jsx";
import { ManualSettings } from "./screens/manual/ManualSettings.jsx";
import { applySettingsChanges, periodIds, settingsPatch } from "./data/staySettings.js";
import { useDemoClock } from "./components/DemoClock.jsx";
import { Scenario } from "./screens/scenario/Scenario.jsx";
import { Welcome } from "./screens/home/Welcome.jsx";
import { useStaySettings } from "./data/useStaySettings.js";
import { Navigation } from "./components/Navigation.jsx";

export function App({ stay: initialStay }) {
  const { stay, acceptStay, queueChanges, notice, retry } = useStaySettings(initialStay);
  const [periodChanges, setPeriodChanges] = useState({});
  const [alarmChanges, setAlarmChanges] = useState({});
  const [selected, setSelected] = useState(0);
  const [screen, setScreen] = useState("home");
  const roomClock = useDemoClock(initialStay.clock, screen === "home");
  const currentPeriod = periodIds.indexOf(roomClock.clock.period);
  const firstHomePeriod = currentPeriod > 0 ? 1 : 0;
  const homeSelected = selected >= firstHomePeriod && selected < firstHomePeriod + 3
    ? selected
    : Math.max(firstHomePeriod, currentPeriod);
  const periodId = periodIds[selected];
  const manual = applySettingsChanges(stay, { periods: periodChanges, alarm: alarmChanges });
  const { kelvin } = (screen === "manual" ? manual : stay).periods[selected].light;
  const lightMode = kelvin < 3100 ? "warm" : kelvin < 5250 ? "neutral" : "cold";
  const chat = useRoomChat(screen === "light", acceptStay, retry);
  const chatReply = useChatReply(chat.turns.at(-1));
  const menu = useRef(null);
  function navigate(nextScreen) {
    if (menu.current.open) menu.current.close();
    if (nextScreen === screen) return;
    if (screen === "manual") queueManualChanges();
    if (screen === "home") setSelected(homeSelected);
    setScreen(nextScreen);
    window.scrollTo(0, 0);
  }
  function selectManualPeriod(index) {
    if (index === selected) return;
    queueManualChanges();
    setSelected(index);
  }
  function changePeriod(changes) {
    setPeriodChanges(previous => ({ ...previous, [periodId]: { ...previous[periodId], ...changes } }));
  }

  function queueManualChanges() {
    queueChanges(settingsPatch(stay, periodChanges, alarmChanges));
    setPeriodChanges({});
    setAlarmChanges({});
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
            <img src={screen === "manual" ? manualMenuIcon : menuIcon} alt="" />
          </button>
          <span className="hotel">Партнёрский отель<br />сна</span>
        </header>
        {notice && (
          <div className="demo-save-status">
            <p role="status">{notice}</p>
            <button type="button" className="pill" onClick={retry}>Повторить</button>
          </div>
        )}
        {screen === "light" && (
          <LightSettings chat={chat} chatReply={chatReply} />
        )}
        {screen === "scenario" && (
          <Scenario periods={stay.periods} selected={selected} onSelect={setSelected} wakeUp={stay.wakeUp} />
        )}
        {screen === "manual" && (
          <ManualSettings periods={manual.periods} selected={selected} onSelect={selectManualPeriod}
            wakeUp={manual.wakeUp} tracks={stay.tracks} onPeriodChange={changePeriod} onLightChange={changePeriod}
            onAlarmChange={changes => setAlarmChanges(previous => ({ ...previous, ...changes }))}
            onShowSmart={() => navigate("light")} />
        )}
        {screen === "home" && (
          <Welcome stay={stay} roomClock={roomClock} currentPeriod={currentPeriod} firstPeriod={firstHomePeriod}
            selected={homeSelected} setSelected={setSelected} showPlan={() => navigate("scenario")}
            showManual={() => navigate("manual")} showSmart={() => navigate("light")} />
        )}
      </main>
      <Navigation dialogRef={menu} screen={screen} onNavigate={navigate} />
    </>
  );
}
