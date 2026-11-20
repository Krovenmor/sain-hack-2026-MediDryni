import "./light-settings.css";
import { useRef, useState } from "react";
import send from "../../../assets/send.svg";
import microphone from "../../../assets/microphone.svg";
import voiceOrb from "../../../assets/voice-orb.png";
import { lightModes } from "../../components/LightBeam.jsx";

import { useChatLineFade } from "./useChatLineFade.js";
import { useMicrophoneLevel } from "./useMicrophoneLevel.js";
import { composeDemoTurn, quickPhrases } from "./chat.js";

function RoomReply({ turn }) {
  const { heading, reply, emphasis } = turn;
  const start = emphasis ? reply.indexOf(emphasis) : -1;
  return (
    <div className="room-message">
      <span className="visually-hidden">Номер: </span>
      {heading && <p className="reply-heading">{heading}</p>}
      <p>{start < 0 ? reply : <>
        {reply.slice(0, start)}<strong>{emphasis}</strong>{reply.slice(start + emphasis.length)}
      </>}</p>
    </div>
  );
}

export function LightSettings({ mode, onModeChange, stay, turns, onSend }) {
  const [showPhrases, setShowPhrases] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const log = useRef(null);
  const orb = useRef(null);
  useChatLineFade(log, turns, showPhrases);
  const microphoneError = useMicrophoneLevel(voiceOpen, orb);

  function sendText(text) {
    const turn = composeDemoTurn(text, stay);
    if (turn) onSend(turn);
  }

  return (
    <section className="light-settings" aria-label="Чат с номером">
      {turns.length ? (
        <div className="chat-log" role="log" aria-label="Переписка" ref={log}>
          {turns.map((turn, index) => (
            <div className="chat-turn" key={index}>
              <p className="guest-message"><span className="visually-hidden">Вы: </span><span className="guest-text">{turn.text}</span></p>
              <RoomReply turn={turn} />
            </div>
          ))}
        </div>
      ) : <h1 id="light-title" className="light-title">Ваши<br />пожелания?</h1>}
      <div className="light-controls">
        {microphoneError && <p className="microphone-status" role="status">{microphoneError}</p>}
        <div className="light-mode-row" hidden={voiceOpen}>
          <fieldset className="light-modes" id="light-modes" hidden={showPhrases}>
            <legend className="visually-hidden">Цвет света</legend>
            {lightModes.map(({ id, name, swatch }) => (
              <label key={id} style={{ "--swatch": swatch }}>
                <input className="visually-hidden" type="radio" name="light-color" value={id} checked={mode === id}
                  onChange={() => onModeChange(id)} />
                <span>{name}</span>
              </label>
            ))}
          </fieldset>
          {showPhrases && <div className="quick-phrases" id="quick-phrases">
            {quickPhrases.map(phrase => <button key={phrase} type="button" onClick={() => sendText(phrase)}>{phrase}</button>)}
          </div>}
          <button className="light-mode-toggle" type="button" aria-label="Быстрые фразы"
            aria-expanded={showPhrases} aria-controls={showPhrases ? "quick-phrases" : undefined} onClick={() => setShowPhrases(open => !open)}>
            <span /><span />
          </button>
        </div>
        <form className="light-composer" onSubmit={event => {
          event.preventDefault();
          sendText(draft);
          setDraft("");
        }}>
          <input aria-label="Сообщение" placeholder="Сообщение" value={draft} onChange={event => setDraft(event.target.value)} />
          <button type="submit" aria-label="Отправить сообщение" disabled={!draft.trim()}><img src={send} alt="" /></button>
          <button className="voice-toggle" type="button" aria-label="Голосовой режим"
            aria-pressed={voiceOpen} onClick={() => setVoiceOpen(open => !open)}>
            {voiceOpen && <img ref={orb} className="voice-orb" src={voiceOrb} alt="" width="169" height="163" />}
            <img className="voice-icon" src={microphone} alt="" />
          </button>
        </form>
      </div>
    </section>
  );
}
