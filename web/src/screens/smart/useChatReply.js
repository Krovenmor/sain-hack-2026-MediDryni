import { useEffect, useState } from "react";

export function useChatReply(turn) {
  const [preview, setPreview] = useState(null);
  const replyText = turn?.animate
    ? (preview?.turn === turn ? preview.text : null)
    : (turn?.reply ?? null);
  const busy = Boolean(turn && !turn.failed && (turn.reply === null || replyText !== turn.reply));

  useEffect(() => {
    if (!turn?.animate || !turn.reply) return;
    const characters = Array.from(turn.reply);
    if (characters.length <= 1 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setPreview({ turn, text: turn.reply });
      return;
    }

    let count = 0;
    let text = "";
    const timer = window.setInterval(() => {
      text += characters[count++];
      setPreview({ turn, text });
      if (count === characters.length) window.clearInterval(timer);
    }, 18);
    return () => window.clearInterval(timer);
  }, [turn]);

  return { replyText, busy };
}
