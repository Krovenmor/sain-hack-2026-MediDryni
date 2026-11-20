export const quickPhrases = ["Не беспокоить", "Я проснулся", "Как вчера", "Я ложусь", "Как дома"];

export function composeDemoTurn(message, stay) {
  const text = message.trim();
  if (!text) return null;
  const phrase = text.toLocaleLowerCase("ru").replace(/[.!?]+$/, "");
  switch (phrase) {
    case "я ложусь":
    case "я проснулся": {
      const name = phrase === "я ложусь" ? "Ночь" : "Утро";
      const period = stay.periods.find(item => item.name === name);
      if (period) {
        const emphasis = `температура ${period.temperature} °C и яркость света ${period.light.brightness} %`;
        return { text, heading: "Хорошо!", reply: `В сценарии «${name}» запланированы ${emphasis}.`, emphasis };
      }
      break;
    }
    case "не беспокоить": {
      const periods = stay.periods.filter(item => item.doNotDisturb).map(item => item.name);
      const reply = periods.length ? `«Не беспокоить» включён в плане на периоды: ${periods.join(", ")}.` : "В плане заезда режим «Не беспокоить» выключен.";
      return { text, heading: "По сценарию", reply, emphasis: "«Не беспокоить»" };
    }
    case "как вчера":
    case "как дома":
      return { text, heading: "Уточните пожелания", emphasis: "текущего сценария", reply: "В демозаезде доступны только настройки текущего сценария. Какие параметры хотите повторить?" };
  }
  return { text, heading: "О настройке номера", emphasis: "настройки из плана заезда", reply: "Для выполнения этой просьбы понадобится подключение к номеру. Пока я могу показать настройки из плана заезда." };
}
