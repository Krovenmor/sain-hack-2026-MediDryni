export async function demoRequest(path, method = "GET", body) {
  const response = await fetch(`/api/v1${path}`, {
    method,
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${localStorage.getItem("demo.client")}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
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
