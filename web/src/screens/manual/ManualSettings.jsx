import { useRef, useState } from "react";
import arrow from "../../../assets/manual-track-arrow.svg";
import { ClimateDial, ManualSlider, ManualSwitch, TimeWheel } from "./ManualControls.jsx";
import { PeriodTabs } from "../../components/PeriodTabs.jsx";
import { tracks } from "./manualSettings.js";
import "./manual-settings.css";

function TrackPicker({ selected, onSelect, disabled }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef(null);
  const expanded = open && !disabled;

  function close() {
    setOpen(false);
    trigger.current.focus();
  }

  return (
    <div className="track-picker" onKeyDown={event => {
      if (expanded && event.key === "Escape") {
        event.stopPropagation();
        close();
      }
    }}>
      <button className="track-trigger" type="button" ref={trigger} disabled={disabled}
        aria-label={`Трек: ${selected}`} aria-expanded={expanded} aria-controls="manual-track-list"
        onClick={() => setOpen(!expanded)}>
        <span>трек</span>
        <span className="track-name">{selected}</span>
        <img src={arrow} alt="" />
      </button>
      <div className="track-panel" id="manual-track-list" data-open={expanded} inert={!expanded}>
        <div className="track-panel-clip">
          <ul className="track-list">
            {tracks.map(track => (
              <li key={track}>
                <button type="button" aria-pressed={track === selected} onClick={() => {
                  onSelect(track);
                  close();
                }}>{track}</button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

export function ManualSettings({ periods, selected, onSelect, settings, onChange, onShowSmart }) {
  const update = field => value => onChange(field, value);

  return (
    <section className="manual-settings" aria-labelledby="manual-title">
      <h1 id="manual-title">Ручная<br />настройка</h1>
      <PeriodTabs periods={periods} selected={selected} onSelect={onSelect} name="manual-period" />
      <div className="manual-card-grid">
        <div className="manual-row manual-row-top">
          <section className="manual-card manual-light" aria-labelledby="manual-light-title">
            <div className="manual-card-heading">
              <h2 id="manual-light-title">свет</h2>
              <ManualSwitch label="Свет" checked={settings.lightOn} onChange={update("lightOn")} />
            </div>
            <ManualSlider label="Температура" min={2200} max={6500} step={50} unit="К"
              value={settings.kelvin} onChange={update("kelvin")} disabled={!settings.lightOn} />
            <ManualSlider label="Яркость" value={settings.brightness}
              onChange={update("brightness")} disabled={!settings.lightOn} />
          </section>
          <section className="manual-card manual-climate" aria-labelledby="manual-climate-title">
            <h2 id="manual-climate-title">климат</h2>
            <ClimateDial value={settings.climate} onChange={update("climate")} />
          </section>
        </div>
        <div className="manual-row manual-row-middle">
          <section className="manual-card manual-dnd" aria-labelledby="manual-dnd-title">
            <h2 id="manual-dnd-title">не<br />беспокоить</h2>
            <ManualSwitch label="Не беспокоить" checked={settings.doNotDisturb} onChange={update("doNotDisturb")} />
          </section>
          <section className="manual-card manual-curtains" aria-labelledby="manual-curtains-title">
            <h2 id="manual-curtains-title">шторы</h2>
            <ManualSlider label="Степень открытости" value={settings.curtains} onChange={update("curtains")} />
          </section>
        </div>
        <section className="manual-card manual-alarm" aria-labelledby="manual-alarm-title">
          <div className="manual-card-heading">
            <h2 id="manual-alarm-title">будильник</h2>
            <ManualSwitch label="Будильник" checked={settings.alarmOn} onChange={update("alarmOn")} />
          </div>
          <div className="alarm-time" data-disabled={!settings.alarmOn}>
            <TimeWheel label="Часы" max={23} value={settings.hour}
              onChange={update("hour")} disabled={!settings.alarmOn} />
            <span aria-hidden="true">:</span>
            <TimeWheel label="Минуты" max={59} value={settings.minute}
              onChange={update("minute")} disabled={!settings.alarmOn} />
          </div>
          <div className="manual-sound-heading">
            <h3>звук</h3>
            <ManualSwitch label="Звук" checked={settings.soundOn} onChange={update("soundOn")} />
          </div>
          <div className="manual-sound">
            <div className="track-cover" aria-hidden="true" />
            <ManualSlider label="Громкость" value={settings.volume}
              onChange={update("volume")} disabled={!settings.soundOn} />
          </div>
          <TrackPicker key={selected} selected={settings.track} onSelect={update("track")} disabled={!settings.soundOn} />
          <div className="manual-card-heading manual-alarm-light">
            <h3>сопровождение светом</h3>
            <ManualSwitch label="Сопровождение светом" checked={settings.alarmLight} onChange={update("alarmLight")} />
          </div>
        </section>
      </div>
      <button className="pill manual-to-smart" type="button" onClick={onShowSmart}>Перейти к умной настройке</button>
    </section>
  );
}
