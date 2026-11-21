export const quickPhrases = ["Не беспокоить", "Я проснулся", "Как вчера", "Я ложусь", "Как дома"];

export function composeDemoVoiceTurn(recording) {
  if (!recording?.size) return null;
  return {
    text: "Голосовое сообщение",
    heading: "Голосовое получено",
    reply: "Распознавание речи пока недоступно. Вернитесь в чат, чтобы написать просьбу.",
  };
}

export function turnsFromMessages(items) {
  const turns = new Map();
  for (const item of items) {
    const id = item.client_message_id;
    const turn = turns.get(id) ?? { id };
    turns.set(id, item.role === "user" ? { ...turn, text: item.text } : { ...turn, reply: item.text });
  }
  return [...turns.values()].filter(turn => turn.text && turn.reply);
}
