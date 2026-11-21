import { useEffect, useState } from "react";

export function useChatReply(turn) {
  const [preview, setPreview] = useState(null);
  const replyText = preview && preview.turn === turn ? preview.text : null;
  const busy = Boolean(turn && replyText !== turn.reply);

  useEffect(() => {
    if (!turn) return;
    const characters = Array.from(turn.reply);
    let typingTimer;
    // The local demo returns immediately. Simulate the wait for a service reply.
    const thinkingTimer = window.setTimeout(() => {
      if (characters.length <= 1 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        setPreview({ turn, text: turn.reply });
        return;
      }

      let count = 1;
      let text = characters[0];
      setPreview({ turn, text });
      typingTimer = window.setInterval(() => {
        text += characters[count++];
        setPreview({ turn, text });
        if (count === characters.length) window.clearInterval(typingTimer);
      }, 18);
    }, 960);

    return () => {
      window.clearTimeout(thinkingTimer);
      window.clearInterval(typingTimer);
    };
  }, [turn]);

  return { replyText, busy };
}
