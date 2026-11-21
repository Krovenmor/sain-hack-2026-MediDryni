// Данные одного демонстрационного заезда до подключения GET /api/stay/current.
export const demoStay = {
  guestName: "Анастасия",
  checkInTime: "20:00",
  wakeUp: {
    time: "05:30", enabled: true, soundOn: true, alarmLight: true,
    volume: 30, track: "Приветствие отеля", dawnMinutes: 30,
  },
  periods: [
    {
      name: "Вечер",
      light: { kelvin: 2200, brightness: 30 },
      curtains: 30,
      temperature: 26,
      doNotDisturb: true,
    },
    {
      name: "Ночь",
      light: { kelvin: 2200, brightness: 0 },
      curtains: 0,
      temperature: 19,
      doNotDisturb: true,
    },
    {
      name: "Утро",
      light: { kelvin: 3500, brightness: 55 },
      curtains: 65,
      temperature: 21,
      doNotDisturb: false,
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
