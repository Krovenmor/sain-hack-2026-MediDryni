import { useRef, useState } from "react";
import orb from "../assets/orb.png";
import menuIcon from "../assets/menu.svg";
import manualMenuIcon from "../assets/manual-menu.svg";
import { LightBeam } from "./components/LightBeam.jsx";
import { useChatReply } from "./screens/smart/useChatReply.js";
import { LightSettings } from "./screens/smart/LightSettings.jsx";
import { ManualSettings } from "./screens/manual/ManualSettings.jsx";
import { updatePeriod } from "./data/staySettings.js";
import { Scenario } from "./screens/scenario/Scenario.jsx";
import { Welcome } from "./screens/home/Welcome.jsx";
import { Navigation } from "./components/Navigation.jsx";

export function App({ stay: initialStay }) {
  const [stay, setStay] = useState(initialStay);
  const [selected, setSelected] = useState(0);
  const [screen, setScreen] = useState("home");
  const { kelvin } = stay.periods[selected].light;
  const lightMode = kelvin < 3100 ? "warm" : kelvin < 5250 ? "neutral" : "cold";
  const [chatTurns, setChatTurns] = useState([]);
  const chatReply = useChatReply(chatTurns.at(-1));
  const menu = useRef(null);
  function navigate(nextScreen) {
    if (menu.current.open) menu.current.close();
    if (nextScreen === screen) return;
    if (nextScreen === "home" && selected >= 3) setSelected(0);
    setScreen(nextScreen);
    window.scrollTo(0, 0);
  }
  function changePeriod(changes) {
    setStay(previous => updatePeriod(previous, selected, changes));
  }
  function changeLight(changes) {
    setStay(previous => updatePeriod(previous, selected, {
      light: { ...previous.periods[selected].light, ...changes },
    }));
  }
  function changeAlarm(changes) {
    setStay(previous => ({ ...previous, wakeUp: { ...previous.wakeUp, ...changes } }));
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
        {screen === "light" && (
          <LightSettings mode={lightMode} onModeChange={kelvin => changeLight({ kelvin })} stay={stay} turns={chatTurns} chatReply={chatReply}
            onSend={turn => setChatTurns(previous => [...previous, turn])} />
        )}
        {screen === "scenario" && (
          <Scenario periods={stay.periods} selected={selected} onSelect={setSelected} wakeUp={stay.wakeUp} />
        )}
        {screen === "manual" && (
          <ManualSettings periods={stay.periods} selected={selected} onSelect={setSelected}
            wakeUp={stay.wakeUp} onPeriodChange={changePeriod} onLightChange={changeLight}
            onAlarmChange={changeAlarm} onShowSmart={() => navigate("light")} />
        )}
        {screen === "home" && (
          <Welcome stay={stay} selected={selected} setSelected={setSelected} showPlan={() => navigate("scenario")}
            showManual={() => navigate("manual")} showSmart={() => navigate("light")} />
        )}
      </main>
      <Navigation dialogRef={menu} screen={screen} onNavigate={navigate} />
    </>
  );
}
