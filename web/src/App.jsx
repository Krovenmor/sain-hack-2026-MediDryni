import { useRef, useState } from "react";
import orb from "../assets/orb.png";
import menuIcon from "../assets/menu.svg";
import manualMenuIcon from "../assets/manual-menu.svg";
import { LightBeam } from "./components/LightBeam.jsx";
import { useRoomChat } from "./screens/smart/useRoomChat.js";
import { useChatReply } from "./screens/smart/useChatReply.js";
import { LightSettings } from "./screens/smart/LightSettings.jsx";
import { ManualSettings } from "./screens/manual/ManualSettings.jsx";
import { demoError, demoRequest } from "./data/demoApi.js";
import { DemoClock } from "./components/DemoClock.jsx";
import { Scenario } from "./screens/scenario/Scenario.jsx";
import { Welcome } from "./screens/home/Welcome.jsx";
import { Navigation } from "./components/Navigation.jsx";

const periodIds = ["evening", "night", "morning", "day"];

export function App({ stay: initialStay }) {
  const [stay, setStay] = useState(initialStay);
  const [periodChanges, setPeriodChanges] = useState({});
  const [alarmChanges, setAlarmChanges] = useState({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState(0);
  const [screen, setScreen] = useState("home");
  const periodId = periodIds[selected];
  const dirty = Object.keys(periodChanges).length > 0 || Object.keys(alarmChanges).length > 0;
  const draft = screen === "manual" || screen === "light";
  const periods = stay.periods.map((period, index) => {
    const { kelvin, brightness, previousBrightness, ...changes } = periodChanges[periodIds[index]] ?? {};
    return {
      ...period,
      ...changes,
      light: { kelvin: kelvin ?? period.light.kelvin, brightness: brightness ?? period.light.brightness,
               previousBrightness: previousBrightness ?? period.light.previousBrightness },
    };
  });
  const wakeUp = { ...stay.wakeUp, ...alarmChanges };
  const { kelvin } = (draft ? periods : stay.periods)[selected].light;
  const lightMode = kelvin < 3100 ? "warm" : kelvin < 5250 ? "neutral" : "cold";
  const chat = useRoomChat(screen === "light", view => {
    setStay(previous => view.revision >= previous.revision ? view : previous);
  });
  const chatReply = useChatReply(chat.turns.at(-1));
  const menu = useRef(null);
  function navigate(nextScreen) {
    if (menu.current.open) menu.current.close();
    if (nextScreen === screen) return;
    if (nextScreen === "home" && selected >= 3) setSelected(0);
    setScreen(nextScreen);
    window.scrollTo(0, 0);
  }
  function changePeriod(changes) {
    setPeriodChanges(previous => ({ ...previous, [periodId]: { ...previous[periodId], ...changes } }));
  }

  function discard() {
    setPeriodChanges({});
    setAlarmChanges({});
  }

  async function save() {
    setSaving(true);
    setNotice("");
    try {
      const periods = Object.fromEntries(Object.entries(periodChanges).map(([period, fields]) => {
        const { previousBrightness, ...values } = fields;
        return [period, values];
      }));
      setStay(await demoRequest("/stay/current", "PATCH", { revision: stay.revision, periods, alarm: alarmChanges }));
      discard();
      setNotice("Настройки сохранены.");
    } catch (error) {
      if (error.status === 409) {
        try {
          setStay(await demoRequest("/stay/current"));
          setNotice("Настройки изменились в другой вкладке. Проверьте свои правки перед повторным сохранением.");
        } catch (refreshError) {
          setNotice(demoError(refreshError));
        }
      } else {
        setNotice(demoError(error));
      }
    } finally {
      setSaving(false);
    }
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
        {draft && (dirty || notice) && (
          <div className="demo-save">
            {dirty && <>
              <button type="button" className="pill" disabled={saving} onClick={save}>
                {saving ? "Сохраняем…" : "Сохранить настройки"}
              </button>
              <button type="button" className="pill" disabled={saving} onClick={() => { discard(); setNotice(""); }}>Отменить</button>
            </>}
            {notice && <p role="status">{notice}</p>}
          </div>
        )}
        {screen === "light" && (
          <LightSettings mode={lightMode} onModeChange={kelvin => { if (!saving) changePeriod({ kelvin }); }} chat={chat} chatReply={chatReply} />
        )}
        {screen === "scenario" && (
          <>
            <DemoClock initial={stay.clock} />
            <Scenario periods={stay.periods} selected={selected} onSelect={setSelected} wakeUp={stay.wakeUp} />
          </>
        )}
        {screen === "manual" && (
          <ManualSettings periods={periods} selected={selected} onSelect={setSelected}
            wakeUp={wakeUp} tracks={stay.tracks} onPeriodChange={changePeriod} onLightChange={changePeriod}
            onAlarmChange={changes => setAlarmChanges(previous => ({ ...previous, ...changes }))}
            saving={saving} onShowSmart={() => navigate("light")} />
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
