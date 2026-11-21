import { useEffect, useRef, useState } from "react";
import { demoError, demoRequest } from "../../data/demoApi.js";
import { turnsFromMessages } from "./chat.js";

export function useRoomChat(open, onStay) {
  const [turns, setTurns] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  const loaded = useRef(false);
  const loadingPage = useRef(false);

  async function history(before = null) {
    if (loadingPage.current) return;
    loadingPage.current = true;
    setLoading(true);
    try {
      const suffix = before ? `?before=${encodeURIComponent(before)}` : "";
      const page = await demoRequest(`/messages${suffix}`);
      const older = turnsFromMessages(page.items);
      const initial = !loaded.current;
      setTurns(previous => {
        const fromPage = new Set(older.map(turn => turn.id));
        if (before || initial) return [...older, ...previous.filter(turn => !fromPage.has(turn.id))];
        const existing = new Set(previous.map(turn => turn.id));
        return [...previous.map(turn => fromPage.has(turn.id) && !turn.reply
          ? older.find(saved => saved.id === turn.id) : turn),
        ...older.filter(turn => !existing.has(turn.id))];
      });
      if (before || initial) setCursor(page.next_before);
      loaded.current = true;
      setError("");
    } catch (err) {
      setError(demoError(err));
    } finally {
      loadingPage.current = false;
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open) void history();
  }, [open]);

  async function send(text, id = crypto.randomUUID?.() ?? [...crypto.getRandomValues(new Uint8Array(16))]
    .map(byte => byte.toString(16).padStart(2, "0")).join("")) {
    if (pending.current) return;
    pending.current = true;
    setError("");
    setTurns(previous => [...previous.filter(turn => turn.id !== id), { id, text, reply: null }]);
    try {
      const result = await demoRequest("/messages", "POST", { client_message_id: id, text });
      setTurns(previous => previous.map(turn => turn.id === id
        ? { id, text: result.guest.text, reply: result.reply.text, animate: true } : turn));
      if (Object.keys(result.changes).length) {
        onStay(await demoRequest("/stay/current"));
      }
    } catch (err) {
      setTurns(previous => previous.map(turn => turn.id === id && turn.reply === null
        ? { ...turn, failed: true } : turn));
      setError(demoError(err));
    } finally {
      pending.current = false;
    }
  }

  function addVoice(turn) {
    setTurns(previous => [...previous, { ...turn, id: `voice-${Date.now()}`, voice: true, animate: true }]);
  }

  return { turns, send, addVoice, cursor, loading, error, history };
}
