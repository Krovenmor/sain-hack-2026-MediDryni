import "./light-settings.css";
import { useEffect, useRef, useState } from "react";
import sendIcon from "../../../assets/send.svg";
import microphone from "../../../assets/microphone.svg";
import voiceBack from "../../../assets/voice-back.svg";
import { VoiceLight } from "./VoiceLight.jsx";
import voiceOrb from "../../../assets/voice-orb.png";

import { useChatLineFade } from "./useChatLineFade.js";
import { useMicrophoneLevel } from "./useMicrophoneLevel.js";
import { quickPhrases, recordingUpload } from "./chat.js";
import { demoError, demoRequest } from "../../data/demoApi.js";
import { useReplySpeech } from "./useReplySpeech.js";
import { newMessageId } from "./useRoomChat.js";

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

export function LightSettings({ chat, chatReply }) {
  const { turns, send, cursor, loading, error, history } = chat;
  const [showPhrases, setShowPhrases] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [voiceMessageId, setVoiceMessageId] = useState(null);
  const voiceTurn = voiceOpen ? turns.find(turn => turn.id === voiceMessageId) : null;
  const { replyText, busy } = chatReply;
  const voiceReply = voiceTurn?.reply ? voiceTurn : null;
  const speech = useReplySpeech(voiceReply?.replyId);
  const [draft, setDraft] = useState("");
  const [finishingRecording, setFinishingRecording] = useState(false);
  const [recordingStatus, setRecordingStatus] = useState("");
  const pendingRecording = useRef(null);
  useEffect(() => () => pendingRecording.current?.abort(), []);
  const log = useRef(null);
  const orb = useRef(null);
  useChatLineFade(log, turns, showPhrases);
  const { error: microphoneError, stopRecording } = useMicrophoneLevel(voiceOpen && !voiceTurn, orb, finishRecording);
  const inputBusy = busy || finishingRecording;
  const voiceStatus = microphoneError || recordingStatus || (voiceTurn?.failed ? error : "") || speech.status;

  function sendText(text) {
    if (inputBusy) return;
    if (text.trim()) void send(text.trim());
  }

  function closeVoice() {
    pendingRecording.current?.abort();
    pendingRecording.current = null;
    setFinishingRecording(false);
    setRecordingStatus("");
    setVoiceMessageId(null);
    setVoiceOpen(false);
  }

  function toggleVoice() {
    if (inputBusy) return;
    setRecordingStatus("");
    if (!voiceOpen || voiceTurn) {
      setVoiceMessageId(null);
      setVoiceOpen(true);
      return;
    }

    void finishRecording(stopRecording());
  }

  async function finishRecording(recording) {
    if (!recording) {
      setRecordingStatus("Запись ещё недоступна");
      return;
    }

    const controller = new AbortController();
    pendingRecording.current = controller;
    setFinishingRecording(true);
    try {
      const audio = await recording;
      if (controller.signal.aborted) return;
      if (!audio) {
        setRecordingStatus("Речь не распознана. Попробуйте записать ещё раз.");
        setVoiceOpen(false);
        return;
      }
      const body = recordingUpload(audio);
      const result = await demoRequest("/voice/recordings", "POST", body, { signal: controller.signal });
      if (controller.signal.aborted) return;
      const id = newMessageId();
      setVoiceMessageId(id);
      void send(result.text, id, result.recording_id);
    } catch (error) {
      if (!controller.signal.aborted) {
        setRecordingStatus(demoError(error));
        setVoiceOpen(false);
      }
    } finally {
      if (pendingRecording.current === controller) {
        pendingRecording.current = null;
        setFinishingRecording(false);
      }
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
                <p className="voice-question">{finishingRecording ? "Распознаём речь…" : "Чем вам помочь?"}</p>
              )}
              {(voiceTurn && !voiceTurn.failed && !voiceReply) && <Thinking className="voice-thinking" />}
            </div>
            {voiceReply && (
              <p className="voice-reply">
                <span className="visually-hidden">Номер: {voiceReply.reply}</span>
                <span aria-hidden="true">{replyText ?? ""}</span>
              </p>
            )}
            {voiceTurn?.failed && <button className="pill voice-action" type="button"
              onClick={() => void send(voiceTurn.text, voiceTurn.id, voiceTurn.recordingId)}>Повторить отправку</button>}
            {speech.ready && <button className="pill voice-action" type="button" onClick={() => void speech.play()}>Прослушать ответ</button>}
          </div>
        </>
      )}
      {turns.length ? (
        <div className="chat-log" role="log" aria-label="Переписка" ref={log}>
          {cursor && <button type="button" className="pill chat-history" disabled={loading}
            onClick={() => void history(cursor)}>Ранние сообщения</button>}
          {turns.map((turn, index) => (
            <div className="chat-turn" key={turn.id ?? index}>
              <p className="guest-message"><span className="visually-hidden">Вы: </span><span className="guest-text">{turn.text}</span></p>
              {turn.failed ? (
                <button className="pill" type="button" disabled={busy} onClick={() => void send(turn.text, turn.id, turn.recordingId)}>
                  Повторить отправку
                </button>
              ) : turn.reply && index < turns.length - 1 ? (
                <RoomReply turn={turn} />
              ) : !turn.reply || replyText === null ? (
                <Thinking className="chat-thinking" />
              ) : (
                <RoomReply turn={turn} text={replyText} />
              )}
            </div>
          ))}
        </div>
      ) : <h1 id="light-title" className="light-title">Ваши<br />пожелания?</h1>}
      <div className="light-controls">
        {!voiceOpen && error && <p className="microphone-status" role="status">{error}</p>}
        {!voiceOpen && loading && !turns.length && <p className="microphone-status" role="status">Загружаем переписку…</p>}
        {(voiceOpen ? voiceStatus : recordingStatus) && (
          <p className="microphone-status" role="status">{voiceOpen ? voiceStatus : recordingStatus}</p>
        )}
        <div className="quick-phrases-row" hidden={voiceOpen}>
          {showPhrases && <div className="quick-phrases" id="quick-phrases">
            {quickPhrases.map(phrase => <button key={phrase} type="button" disabled={busy} onClick={() => sendText(phrase)}>{phrase}</button>)}
          </div>}
          <button className="quick-phrases-toggle" type="button" aria-label="Быстрые фразы"
            aria-expanded={showPhrases} aria-controls={showPhrases ? "quick-phrases" : undefined} onClick={() => setShowPhrases(open => !open)}>
            <span /><span />
          </button>
        </div>
        <form className="light-composer" onSubmit={event => {
          event.preventDefault();
          if (voiceOpen || inputBusy) return;
          sendText(draft);
          setDraft("");
        }}>
          {!voiceOpen && <>
            <input aria-label="Сообщение" placeholder="Сообщение" value={draft} maxLength={2000} disabled={inputBusy}
              onChange={event => setDraft(event.target.value)} />
            <button type="submit" aria-label="Отправить сообщение" disabled={inputBusy || !draft.trim()}><img src={sendIcon} alt="" /></button>
          </>}
          <button className="voice-toggle" type="button" aria-label={voiceOpen && !voiceTurn ? "Отправить голосовое сообщение" : "Начать запись"}
            aria-pressed={voiceOpen} disabled={inputBusy} onClick={toggleVoice}>
            <img ref={orb} className="voice-orb" src={voiceOrb} alt="" width="169" height="163" />
            <img className="voice-icon" src={microphone} alt="" />
          </button>
        </form>
      </div>
    </section>
  );
}
