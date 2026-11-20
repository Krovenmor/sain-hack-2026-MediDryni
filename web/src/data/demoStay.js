// Данные одного демонстрационного заезда до подключения GET /api/stay/current.
export const demoStay = {
  guestName: "Анастасия",
  checkInTime: "20:00",
  wakeUp: { time: "05:30", description: "со светомузыкой" },
  periods: [
    {
      name: "Вечер",
      light: { kelvin: 2200, brightness: 30 },
      curtains: 30,
      temperature: 26,
      doNotDisturb: true,
      settings: ["Свет: 2200 К", "Яркость: 30 %", "Температура: 26 °C"],
    },
    {
      name: "Ночь",
      light: { kelvin: 2200, brightness: 0 },
      curtains: 0,
      temperature: 19,
      doNotDisturb: true,
      settings: ["Свет: выключен", "Температура: 19 °C", "Шторы: закрыты", "Не беспокоить: вкл."],
    },
    {
      name: "Утро",
      light: { kelvin: 3500, brightness: 55 },
      curtains: 65,
      temperature: 21,
      doNotDisturb: false,
      settings: ["Рассвет: с 05:00", "Будильник: 05:30", "Режим: свет"],
    },
    {
      name: "День",
      light: { kelvin: 5000, brightness: 85 },
      curtains: 100,
      temperature: 22,
      doNotDisturb: false,
    },
  ],
};
