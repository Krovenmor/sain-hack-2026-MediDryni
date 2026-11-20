import { useRef, useState } from "react";
import orb from "../assets/orb.png";
import menuIcon from "../assets/menu.svg";
import manualMenuIcon from "../assets/manual-menu.svg";
import { LightBeam } from "./components/LightBeam.jsx";
import { useChatReply } from "./screens/smart/useChatReply.js";
import { LightSettings } from "./screens/smart/LightSettings.jsx";
import { ManualSettings } from "./screens/manual/ManualSettings.jsx";
import { initialManualSettings } from "./screens/manual/manualSettings.js";
import { Scenario } from "./screens/scenario/Scenario.jsx";
import { Welcome } from "./screens/home/Welcome.jsx";
import { Navigation } from "./components/Navigation.jsx";

export function App({ stay }) {
  const [selected, setSelected] = useState(0);
  const [scenarioPeriod, setScenarioPeriod] = useState(0);
  const [manualPeriod, setManualPeriod] = useState(0);
  const [manualSettings, setManualSettings] = useState(() => initialManualSettings(stay));
  const [screen, setScreen] = useState("home");
  const [lightMode, setLightMode] = useState("warm");
  const [chatTurns, setChatTurns] = useState([]);
  const chatReply = useChatReply(chatTurns.at(-1));
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
  function showManual(index) {
    setManualPeriod(index);
    navigate("manual");
  }
  function updateManual(field, value) {
    setManualSettings(previous => {
      if (previous[manualPeriod][field] === value) return previous;
      return previous.map((settings, index) => index === manualPeriod ? { ...settings, [field]: value } : settings);
    });
    if (field === "kelvin") setLightMode(value < 3100 ? "warm" : value < 5250 ? "neutral" : "cold");
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
          <LightSettings mode={lightMode} onModeChange={setLightMode} stay={stay} turns={chatTurns} chatReply={chatReply}
            onSend={turn => setChatTurns(previous => [...previous, turn])} />
        )}
        {screen === "scenario" && (
          <Scenario periods={stay.periods} selected={scenarioPeriod} onSelect={setScenarioPeriod} wakeUp={stay.wakeUp} />
        )}
        {screen === "manual" && (
          <ManualSettings periods={stay.periods} selected={manualPeriod} onSelect={setManualPeriod}
            settings={manualSettings[manualPeriod]} onChange={updateManual} onShowSmart={() => navigate("light")} />
        )}
        {screen === "home" && (
          <Welcome stay={stay} selected={selected} setSelected={setSelected} showPlan={showPlan}
            showManual={showManual} showSmart={() => navigate("light")} />
        )}
      </main>
      <Navigation dialogRef={menu} screen={screen} onNavigate={navigate} />
    </>
  );
}
