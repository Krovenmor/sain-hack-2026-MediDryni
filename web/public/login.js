const form = document.querySelector("#entry");
const access = document.querySelector("#access");
const dataset = document.querySelector("#dataset");
const choose = document.querySelector("#choose");
const stayOptions = document.querySelector("#stay-options");
const status = document.querySelector("#status");
const loading = document.querySelector("#loading");
const submit = form.querySelector("button");

const loadingPhrases = [
  "ЛЛМ думает…",
  "ЛЛМ очень старается…",
  "Готовим персональный сценарий…",
  "Уже скоро…",
  "Подготовка ещё идёт…",
];

function browserKey() {
  let key = localStorage.getItem("demo.client");
  if (!/^[a-f0-9]{64}$/.test(key ?? "")) {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    key = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
    localStorage.setItem("demo.client", key);
  }
  return key;
}

function askCode(needed) {
  document.querySelector("#access-label").hidden = !needed;
  access.required = needed;
}

window.addEventListener("storage", event => {
  if (event.key === "demo.client" && event.oldValue && event.oldValue !== event.newValue) location.reload();
});

const errors = {
  access_denied: "Проверьте код показа.",
  too_many_attempts: "Слишком много попыток. Попробуйте через минуту.",
  dataset_not_found: "Набор не найден. Проверьте код набора.",
  forbidden: "Этот набор недоступен. Проверьте код набора.",
  stay_conflict: "За этим браузером уже закреплён другой заезд. Для нового заезда откройте демо в приватном окне.",
  demo_full: "Свободных комнат для этого заезда больше нет. Выберите другой заезд.",
  final_run_unavailable: "Стенд не открыл финальный прогон. Повторите вход чуть позже.",
  invalid_room_or_plan: "Не удалось подготовить комнату. Повторите вход.",
  show_ended: "Показ завершён.",
};

async function start(stayId) {
  submit.disabled = true;
  access.readOnly = true;
  dataset.readOnly = true;
  choose.disabled = true;
  loading.hidden = false;
  status.textContent = "Открываем заезд…";
  let phrase = 0;
  const loadingTimer = window.setInterval(() => {
    status.textContent = loadingPhrases[phrase++];
    if (phrase === loadingPhrases.length) window.clearInterval(loadingTimer);
  }, 7000);
  try {
    const code = dataset.value.trim();
    let key = navigator.locks
      ? await navigator.locks.request("demo.client", browserKey)
      : browserKey();
    const enter = () => fetch("/api/v1/run_client", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
      body: JSON.stringify({ dataset_code: code, access_code: access.value || undefined,
                             stay_record_id: stayId || undefined }),
    });
    let response = await enter();
    let result = await response.json();
    if (response.status === 410 && result.detail?.code === "dev_session_expired") {
      status.textContent = "Время демо истекло. Открываем новый заезд…";
      response = await fetch("/api/v1/demo/reset", { method: "POST", headers: { Authorization: `Bearer ${key}` } });
      result = await response.json();
      if (response.ok) {
        if (localStorage.getItem("demo.client") === key) localStorage.setItem("demo.client", result.client_key);
        key = localStorage.getItem("demo.client");
        response = await enter();
        result = await response.json();
      }
    }
    if (!response.ok) {
      const reason = result.detail?.code;
      if (reason === "access_denied") {
        askCode(true);
        localStorage.removeItem("demo.selection");
      }
      status.textContent = errors[reason] ?? "Не удалось открыть заезд. Проверьте данные и повторите.";
      return;
    }
    access.value = "";
    askCode(false);
    if (result.status === "choose_stay") {
      stayOptions.replaceChildren(...result.stays.map((stay, index) => {
        const label = document.createElement("label");
        const option = document.createElement("input");
        option.type = "radio";
        option.name = "stay";
        option.value = stay.stay_record_id;
        option.checked = index === 0;
        label.append(option, `${stay.user_id} · ${stay.check_in_at.replace("T", " ")}`);
        return label;
      }));
      choose.hidden = false;
      status.textContent = "";
      return;
    }
    localStorage.setItem("demo.selection", JSON.stringify({ code, id: result.stay.stay_record_id }));
    const { openRoom } = await import("/demo/room.js");
    await openRoom(result.stay);
  } catch {
    status.textContent = "Не удалось загрузить приложение. Попробуйте ещё раз.";
  } finally {
    window.clearInterval(loadingTimer);
    loading.hidden = true;
    submit.disabled = false;
    access.readOnly = false;
    dataset.readOnly = false;
    choose.disabled = false;
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  if (!submit.disabled) void start(choose.hidden ? undefined : stayOptions.querySelector(":checked")?.value);
});
let datasetEdited = false;
dataset.addEventListener("input", () => {
  datasetEdited = true;
  choose.hidden = true;
});

async function prefillDataset() {
  try {
    const response = await fetch("/api/v1/demo/default-dataset", { cache: "no-store" });
    if (!response.ok) return;
    const { dataset_code } = await response.json();
    if (dataset_code && !datasetEdited && !dataset.value && !submit.disabled) dataset.value = dataset_code;
  } catch {
    // Manual entry is available if the default cannot be fetched.
  }
}

try {
  const saved = JSON.parse(localStorage.getItem("demo.selection"));
  if (saved?.code && saved?.id) {
    dataset.value = saved.code;
    askCode(false);
    void start(saved.id);
  } else {
    void prefillDataset();
  }
} catch {
  status.textContent = "Для входа нужен доступ к хранилищу браузера.";
}
