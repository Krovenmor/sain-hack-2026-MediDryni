import { useRef, useState } from "react";
import { demoError, demoRequest } from "./demoApi.js";
import { applySettingsChanges, settingsPatch } from "./staySettings.js";

function mergeChanges(batches) {
  const periods = {};
  const alarm = {};
  for (const batch of batches) {
    for (const [period, fields] of Object.entries(batch.periods)) {
      periods[period] = { ...periods[period], ...fields };
    }
    Object.assign(alarm, batch.alarm);
  }
  return { periods, alarm };
}

export function useStaySettings(initialStay) {
  const confirmed = useRef(initialStay);
  const queue = useRef([]);
  const sending = useRef(null);
  const failed = useRef(false);
  const [stay, setStay] = useState(initialStay);
  const [pending, setPending] = useState([]);
  const [notice, setNotice] = useState("");

  function acceptStay(view) {
    if (view.revision < confirmed.current.revision) return;
    confirmed.current = view;
    setStay(view);
  }

  async function send(changes) {
    const patch = settingsPatch(confirmed.current, changes.periods, changes.alarm);
    if (Object.keys(patch.periods).length || Object.keys(patch.alarm).length) {
      acceptStay(await demoRequest("/stay/current", "PATCH", patch));
    }
  }

  async function sendQueuedChanges() {
    try {
      while (queue.current.length) {
        const changes = queue.current[0];
        try {
          await send(changes);
        } catch (error) {
          if (error.status !== 409) throw error;
          acceptStay(await demoRequest("/stay/current"));
          await send(changes);
        }
        queue.current = queue.current.slice(1);
        setPending(queue.current);
      }
      return true;
    } catch (error) {
      failed.current = true;
      setNotice(demoError(error));
      return false;
    }
  }

  function flush() {
    if (sending.current) return sending.current;
    if (!queue.current.length) return true;
    if (failed.current && queue.current.length > 1) {
      // A correction must be able to replace fields from a rejected batch.
      queue.current = [mergeChanges(queue.current)];
      setPending(queue.current);
    }
    failed.current = false;
    setNotice("");
    sending.current = sendQueuedChanges().finally(() => { sending.current = null; });
    return sending.current;
  }

  function queueChanges(changes) {
    if (Object.keys(changes.periods).length || Object.keys(changes.alarm).length) {
      queue.current = [...queue.current, changes];
      setPending(queue.current);
    }
    void flush();
  }

  return { stay: pending.reduce(applySettingsChanges, stay), acceptStay, queueChanges, notice, retry: flush };
}
