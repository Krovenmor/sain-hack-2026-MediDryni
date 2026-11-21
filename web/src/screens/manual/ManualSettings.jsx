import { useRef, useState } from "react";
import arrow from "../../../assets/manual-track-arrow.svg";
import dndHand from "../../../assets/manual-dnd-hand.svg";
import { ClimateDial, ManualSlider, ManualSwitch, TimeWheel } from "./ManualControls.jsx";
import { PeriodTabs } from "../../components/PeriodTabs.jsx";
import { formatTime, toggleLight } from "../../data/staySettings.js";
import "./manual-settings.css";

function TrackPicker({ tracks, selected, onSelect, disabled }) {
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

export function ManualSettings({ periods, selected, onSelect, wakeUp, onPeriodChange, onLightChange, onAlarmChange, onShowSmart, tracks }) {
  const period = periods[selected];
  const { light } = period;
  const lit = light.brightness > 0;
  const soundAvailable = wakeUp.enabled && wakeUp.soundOn;
  const [hour, minute] = wakeUp.time.split(":").map(Number);

  return (
    <section className="manual-settings" aria-labelledby="manual-title">
      <header className="manual-period-header">
        <h1 id="manual-title">Ручная<br />настройка</h1>
        <PeriodTabs periods={periods} selected={selected} onSelect={onSelect} name="manual-period" />
      </header>
      <div className="manual-card-grid">
        <div className="manual-row manual-row-top">
          <section className="manual-card manual-light" aria-labelledby="manual-light-title">
            <div className="manual-card-heading">
              <h2 id="manual-light-title">свет</h2>
              <ManualSwitch label="Свет" checked={lit} onChange={enabled => onLightChange(toggleLight(light, enabled))} />
            </div>
            <ManualSlider label="Температура" min={2200} max={6500} step={50} unit="К"
              value={light.kelvin} onChange={kelvin => onLightChange({ kelvin })} disabled={!lit} />
            <ManualSlider label="Яркость" value={light.brightness}
              onChange={brightness => onLightChange(brightness ? { brightness } : toggleLight(light, false))} disabled={!lit} />
          </section>
          <section className="manual-card manual-climate" aria-labelledby="manual-climate-title">
            <h2 id="manual-climate-title">климат</h2>
            <ClimateDial value={period.temperature} onChange={temperature => onPeriodChange({ temperature })} />
          </section>
        </div>
        <div className="manual-row manual-row-middle">
          <section className="manual-card manual-dnd" aria-labelledby="manual-dnd-title" data-active={period.doNotDisturb}>
            <img className="manual-dnd-hand" src={dndHand} width="106" height="128" alt="" />
            <h2 id="manual-dnd-title">не<br />беспокоить</h2>
            <ManualSwitch label="Не беспокоить" checked={period.doNotDisturb} onChange={doNotDisturb => onPeriodChange({ doNotDisturb })} />
          </section>
          <section className="manual-card manual-curtains" aria-labelledby="manual-curtains-title">
            <h2 id="manual-curtains-title">шторы</h2>
            <ManualSlider label="Степень открытости" value={period.curtains} onChange={curtains => onPeriodChange({ curtains })} />
          </section>
        </div>
        <section className="manual-card manual-alarm" aria-labelledby="manual-alarm-title">
          <div className="manual-card-heading">
            <h2 id="manual-alarm-title">будильник</h2>
            <ManualSwitch label="Будильник" checked={wakeUp.enabled} onChange={enabled => onAlarmChange({ enabled })} />
          </div>
          <fieldset className="manual-alarm-options" disabled={!wakeUp.enabled}>
            <legend className="visually-hidden">Настройки будильника</legend>
            <div className="alarm-time">
              <TimeWheel label="Часы" max={23} value={hour}
                onChange={value => onAlarmChange({ time: formatTime(value, minute) })} disabled={!wakeUp.enabled} />
              <span aria-hidden="true">:</span>
              <TimeWheel label="Минуты" max={59} value={minute}
                onChange={value => onAlarmChange({ time: formatTime(hour, value) })} disabled={!wakeUp.enabled} />
            </div>
            <div className="manual-sound-heading">
              <h3>звук</h3>
              <ManualSwitch label="Звук" checked={wakeUp.soundOn} onChange={soundOn => onAlarmChange({ soundOn })} />
            </div>
            <div className="manual-sound">
              <div className="track-cover" aria-hidden="true" />
              <ManualSlider label="Громкость" value={wakeUp.volume}
                onChange={volume => onAlarmChange({ volume })} disabled={!wakeUp.soundOn} />
            </div>
            <TrackPicker tracks={tracks} key={`${selected}:${soundAvailable}`} selected={wakeUp.track}
              onSelect={track => onAlarmChange({ track })} disabled={!soundAvailable} />
            <div className="manual-card-heading manual-alarm-light">
              <h3>сопровождение светом</h3>
              <ManualSwitch label="Сопровождение светом" checked={wakeUp.alarmLight} onChange={alarmLight => onAlarmChange({ alarmLight })} />
            </div>
          </fieldset>
        </section>
      </div>
      <button className="pill manual-to-smart" type="button" onClick={onShowSmart}>Перейти к умной настройке</button>
    </section>
  );
}
