import { useEffect, useId, useRef, useState } from "react";
import climateColor from "../assets/manual-climate-color.png";
import climateMask from "../assets/manual-climate-mask.svg";
import switchArt from "../assets/manual-switch.svg";

export function ManualSwitch({ label, checked, onChange, disabled }) {
  return (
    <button className="manual-switch" type="button" role="switch" aria-label={label}
      aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}>
      <img className="switch-on" src={switchArt} alt="" />
      <img className="switch-off" src={switchArt} alt="" />
    </button>
  );
}

export function ManualSlider({ label, value, onChange, min = 0, max = 100, step = 1, unit = "%", disabled }) {
  const id = useId();
  return (
    <div className={`manual-slider ${unit === "К" ? "color-slider" : ""}`}>
      <output htmlFor={id}>{value}{unit}</output>
      <label htmlFor={id}>{label}</label>
      <input id={id} type="range" min={min} max={max} step={step} value={value} disabled={disabled}
        aria-valuetext={`${value} ${unit}`} onChange={event => onChange(event.target.valueAsNumber)} />
    </div>
  );
}

function useSpinControl({ value, onChange, min, max, axis, pixelsPerStep, wrap = false, disabled = false }) {
  const target = useRef(null);
  const gesture = useRef(null);
  const [offset, setOffset] = useState(0);
  const [dragging, setDragging] = useState(false);
  const normalize = next => {
    if (!wrap) return Math.min(max, Math.max(min, next));
    const count = max + 1;
    return ((next % count) + count) % count;
  };
  const step = direction => onChange(normalize(value + direction));
  const wheel = useRef(null);
  useEffect(() => {
    const control = target.current;
    const handleWheel = event => wheel.current(event);
    control.addEventListener("wheel", handleWheel, { passive: false });
    return () => control.removeEventListener("wheel", handleWheel);
  }, []);
  wheel.current = event => {
    if (disabled || document.activeElement !== target.current || !event.deltaY) return;
    event.preventDefault();
    step(Math.sign(event.deltaY));
  };

  function endGesture(event, tap = false) {
    const start = gesture.current;
    if (start?.pointer !== event.pointerId) return;
    gesture.current = null;
    setDragging(false);
    setOffset(0);
    if (tap && !start.moved && axis === "y") {
      const rect = event.currentTarget.getBoundingClientRect();
      const distance = event.clientY - rect.top - rect.height / 2;
      if (Math.abs(distance) > 22) step(Math.sign(distance));
    }
  }

  return {
    offset,
    dragging,
    handlers: {
      ref: target,
      onKeyDown(event) {
        if (disabled) return;
        const keys = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1 };
        if (event.key in keys) step(keys[event.key]);
        else if (event.key === "Home") onChange(min);
        else if (event.key === "End") onChange(max);
        else return;
        event.preventDefault();
      },
      onPointerDown(event) {
        if (disabled || !event.isPrimary || event.button !== 0) return;
        const coordinate = axis === "x" ? event.clientX : event.clientY;
        gesture.current = { pointer: event.pointerId, coordinate, value, moved: false };
        event.currentTarget.focus({ preventScroll: true });
        event.currentTarget.setPointerCapture(event.pointerId);
        setDragging(true);
      },
      onPointerMove(event) {
        const start = gesture.current;
        if (start?.pointer !== event.pointerId) return;
        const distance = axis === "x" ? event.clientX - start.coordinate : start.coordinate - event.clientY;
        if (Math.abs(distance) < 4 && !start.moved) return;
        start.moved = true;
        const steps = distance / pixelsPerStep;
        const whole = Math.round(steps);
        const next = normalize(start.value + whole);
        onChange(next);
        setOffset(!wrap && next !== start.value + whole ? 0 : (steps - whole) * pixelsPerStep);
      },
      onPointerUp: event => endGesture(event, true),
      onPointerCancel: endGesture,
      onLostPointerCapture: endGesture,
    },
  };
}

export function ClimateDial({ value, onChange }) {
  const { dragging, offset, handlers } = useSpinControl({
    value, onChange, min: 16, max: 26, axis: "x", pixelsPerStep: 12,
  });
  const angle = -3.5 * (value - 16 + offset / 12);
  return (
    <div className="climate-dial" role="spinbutton" aria-label="Температура воздуха" tabIndex={0}
      aria-valuemin={16} aria-valuemax={26} aria-valuenow={value} aria-valuetext={`${value} °C`}
      data-dragging={dragging} {...handlers}>
      <span>{value}°С</span>
      <div className="climate-dial-rotation" style={{ "--dial-angle": `${angle}deg` }} aria-hidden="true">
        <div className="climate-dial-mask" style={{ "--climate-mask": `url("${climateMask}")` }}>
          <img src={climateColor} width="250" height="250" alt="" />
        </div>
      </div>
    </div>
  );
}

export function TimeWheel({ label, value, max, onChange, disabled }) {
  const { dragging, offset, handlers } = useSpinControl({
    value, onChange, min: 0, max, axis: "y", pixelsPerStep: 48, wrap: true, disabled,
  });
  const format = number => String((number + max + 1) % (max + 1)).padStart(2, "0");
  return (
    <div className="time-wheel" role="spinbutton" aria-label={label} aria-valuemin={0} aria-valuemax={max}
      aria-valuenow={value} aria-valuetext={format(value)} aria-disabled={disabled} tabIndex={disabled ? -1 : 0}
      data-dragging={dragging} {...handlers}>
      <div className="time-wheel-strip" style={{ "--wheel-offset": `${-offset}px` }} aria-hidden="true">
        {[-2, -1, 0, 1, 2].map(delta => <span key={delta} data-current={delta === 0}>{format(value + delta)}</span>)}
      </div>
    </div>
  );
}
