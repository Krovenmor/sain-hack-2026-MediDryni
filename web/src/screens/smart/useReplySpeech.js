import { useEffect, useRef, useState } from "react";
import { demoError, demoRequest } from "../../data/demoApi.js";

export function useReplySpeech(replyId) {
  const audio = useRef(null);
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    setReady(false);
    setStatus("");
    if (!replyId) return;
    const controller = new AbortController();
    let url;
    async function prepare() {
      try {
        const speech = await demoRequest(`/messages/${encodeURIComponent(replyId)}/speech`,
          "GET", undefined, { audio: true, signal: controller.signal });
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(speech);
        audio.current = new Audio(url);
        setReady(true);
        try {
          await audio.current.play();
        } catch {
          if (!controller.signal.aborted) setStatus("Нажмите «Прослушать ответ», чтобы включить звук.");
        }
      } catch (error) {
        if (!controller.signal.aborted) setStatus(demoError(error));
      }
    }
    void prepare();
    return () => {
      controller.abort();
      audio.current?.pause();
      audio.current = null;
      if (url) URL.revokeObjectURL(url);
    };
  }, [replyId]);

  async function play() {
    if (!audio.current) return;
    try {
      audio.current.currentTime = 0;
      await audio.current.play();
      setStatus("");
    } catch {
      setStatus("Не удалось воспроизвести ответ.");
    }
  }

  return { ready, status, play };
}
