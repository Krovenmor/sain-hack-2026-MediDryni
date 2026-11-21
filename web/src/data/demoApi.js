export async function demoRequest(path, method = "GET", body, { audio = false, signal } = {}) {
  const multipart = body instanceof FormData;
  const response = await fetch(`/api/v1${path}`, {
    method,
    signal,
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${localStorage.getItem("demo.client")}`,
      ...(body === undefined || multipart ? {} : { "Content-Type": "application/json" }),
    },
    body: multipart || body === undefined ? body : JSON.stringify(body),
  });
  if (response.ok && audio) return response.blob();
  const result = await response.json();
  if (!response.ok) {
    const error = new Error("Запрос не выполнен");
    error.status = response.status;
    error.code = result.detail?.code;
    throw error;
  }
  return result;
}

const errors = {
  audio_too_large: "Запись больше 5 МБ. Запишите более короткую просьбу.",
  unsupported_audio: "Формат записи не поддерживается.",
  invalid_audio: "Не удалось прочитать запись.",
  no_speech: "Речь не распознана. Попробуйте записать ещё раз.",
  transcript_too_long: "Просьба слишком длинная. Разбейте её на несколько сообщений.",
  voice_budget: "Суточный лимит голосовых запросов закончился.",
  voice_rate_limit: "Слишком много голосовых запросов. Попробуйте через минуту.",
  voice_unavailable: "Голосовой сервис временно недоступен.",
  message_conflict: "Этот запрос уже отправлен с другим текстом.",
  show_ended: "Показ завершён.",
  alarm_outside_stay: "Выберите время будильника в пределах заезда.",
  alarm_mode_required: "Для будильника включите звук или свет.",
  invalid_device_settings: "Некоторые значения недоступны для этой комнаты.",
  empty_period: "Этот период не входит в интервал заезда.",
  time_outside_stay: "Выберите время в пределах заезда.",
};

export function demoError(error) {
  return errors[error.code] || "Не удалось выполнить запрос. Попробуйте ещё раз.";
}
