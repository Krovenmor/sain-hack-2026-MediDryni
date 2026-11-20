import "./light-settings.css";
import { useRef, useState } from "react";
import send from "../../../assets/send.svg";
import microphone from "../../../assets/microphone.svg";
import voiceBack from "../../../assets/voice-back.svg";
import { VoiceLight } from "./VoiceLight.jsx";
import voiceOrb from "../../../assets/voice-orb.png";
import { lightModes } from "../../components/LightBeam.jsx";

import { useChatLineFade } from "./useChatLineFade.js";
import { useMicrophoneLevel } from "./useMicrophoneLevel.js";
import { composeDemoTurn, composeDemoVoiceTurn, quickPhrases } from "./chat.js";

function Thinking({ className = "" }) {
  return <p className={className}>Думает<span className="thinking-dots" aria-hidden="true">...</span></p>;
}

function RoomReply({ turn, text = turn.reply }) {
  const { heading, reply, emphasis } = turn;
  const start = emphasis ? reply.indexOf(emphasis) : -1;
  return (
    <div className="room-message">
      <span className="visually-hidden">Номер: </span>
      {heading && <p className="reply-heading">{heading}</p>}
      <p><span className="visually-hidden">{reply}</span><span aria-hidden="true">{start < 0 ? text : <>
        {text.slice(0, start)}<strong>{text.slice(start, start + emphasis.length)}</strong>{text.slice(start + emphasis.length)}
      </>}</span></p>
    </div>
  );
}

export function LightSettings({ mode, onModeChange, stay, turns, chatReply, onSend }) {
  const [showPhrases, setShowPhrases] = useState(false);
  const [voiceStart, setVoiceStart] = useState(null);
  const voiceOpen = voiceStart !== null;
  const voiceTurn = voiceOpen && turns.length > voiceStart ? turns.at(-1) : null;
  const { replyText, busy } = chatReply;
  const voiceReply = voiceTurn && replyText !== null ? voiceTurn : null;
  const [draft, setDraft] = useState("");
  const [finishingRecording, setFinishingRecording] = useState(false);
  const [recordingStatus, setRecordingStatus] = useState("");
  const pendingRecording = useRef(null);
  const log = useRef(null);
  const orb = useRef(null);
  useChatLineFade(log, turns, showPhrases);
  const { error: microphoneError, stopRecording } = useMicrophoneLevel(voiceOpen && !voiceTurn, orb);
  const inputBusy = busy || finishingRecording;

  function sendText(text) {
    if (inputBusy) return;
    const turn = composeDemoTurn(text, stay);
    if (turn) onSend(turn);
  }

  function closeVoice() {
    pendingRecording.current = null;
    setFinishingRecording(false);
    setRecordingStatus("");
    setVoiceStart(null);
  }

  async function toggleVoice() {
    if (inputBusy) return;
    setRecordingStatus("");
    if (!voiceOpen || (voiceTurn && !draft.trim())) {
      setVoiceStart(turns.length);
    } else if (draft.trim()) {
      sendText(draft);
      setDraft("");
    } else {
      const recording = stopRecording();
      if (!recording) {
        setRecordingStatus("Запись ещё недоступна");
        return;
      }
      pendingRecording.current = recording;
      setFinishingRecording(true);
      const audio = await recording;
      if (pendingRecording.current !== recording) return;
      pendingRecording.current = null;
      setFinishingRecording(false);
      const turn = composeDemoVoiceTurn(audio);
      if (turn) onSend(turn);
      else setRecordingStatus("Не удалось записать голосовое. Введите просьбу текстом.");
    }
  }

  return (
    <section className="light-settings" aria-label="Чат с номером" data-voice-open={voiceOpen}>
      <VoiceLight active={voiceOpen} reply={voiceReply} />
      {voiceOpen && (
        <>
          <button className="voice-back" type="button" aria-label="Вернуться в чат" onClick={closeVoice}>
            <img src={voiceBack} alt="" />
          </button>
          <div className="voice-dialogue" role="status" data-answered={Boolean(voiceReply)}>
            <div className="voice-utterance">
              {voiceTurn ? (
                <p className="voice-question"><span className="visually-hidden">Вы: </span>{voiceTurn.text}</p>
              ) : (
                <p className="voice-question">{finishingRecording ? "Голосовое сообщение" : "Чем вам помочь?"}</p>
              )}
              {(finishingRecording || (voiceTurn && !voiceReply)) && <Thinking className="voice-thinking" />}
            </div>
            {voiceReply && (
              <p className="voice-reply">
                <span className="visually-hidden">Номер: {voiceReply.reply}</span>
                <span aria-hidden="true">{replyText}</span>
              </p>
            )}
          </div>
        </>
      )}
      {turns.length ? (
        <div className="chat-log" role="log" aria-label="Переписка" ref={log}>
          {turns.map((turn, index) => (
            <div className="chat-turn" key={index}>
              <p className="guest-message"><span className="visually-hidden">Вы: </span><span className="guest-text">{turn.text}</span></p>
              {index < turns.length - 1 ? (
                <RoomReply turn={turn} />
              ) : replyText === null ? (
                <Thinking className="chat-thinking" />
              ) : (
                <RoomReply turn={turn} text={replyText} />
              )}
            </div>
          ))}
        </div>
      ) : <h1 id="light-title" className="light-title">Ваши<br />пожелания?</h1>}
      <div className="light-controls">
        {voiceOpen && (microphoneError || recordingStatus) && (
          <p className="microphone-status" role="status">{microphoneError || recordingStatus}</p>
        )}
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
            {quickPhrases.map(phrase => <button key={phrase} type="button" disabled={busy} onClick={() => sendText(phrase)}>{phrase}</button>)}
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
          if (voiceOpen) event.currentTarget.querySelector("input").blur();
        }}>
          <input aria-label="Сообщение" placeholder="Сообщение" value={draft} disabled={inputBusy}
            onChange={event => setDraft(event.target.value)} />
          <button type="submit" aria-label="Отправить сообщение" disabled={inputBusy || !draft.trim()}><img src={send} alt="" /></button>
          <button className="voice-toggle" type="button" aria-label="Голосовой режим"
            aria-pressed={voiceOpen} disabled={inputBusy} onClick={toggleVoice}>
            <img ref={orb} className="voice-orb" src={voiceOrb} alt="" width="169" height="163" />
            <img className="voice-icon" src={microphone} alt="" />
          </button>
        </form>
      </div>
    </section>
  );
}
