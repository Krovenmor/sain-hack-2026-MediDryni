export const tracks = [
  "Приветствие отеля", "Шум дождя", "Морской прибой", "Белый шум",
  "Камин", "Лесные птицы", "Фортепиано для сна", "Ветер в соснах",
  "Розовый шум", "Ручей", "Поющие чаши", "Ночной город",
];

export function initialManualSettings(stay) {
  const [hour, minute] = stay.wakeUp.time.split(":").map(Number);
  return stay.periods.map(period => ({
    kelvin: period.light.kelvin,
    brightness: period.light.brightness,
    lightOn: period.light.brightness > 0,
    climate: period.temperature,
    curtains: period.curtains,
    doNotDisturb: period.doNotDisturb,
    alarmOn: true,
    hour,
    minute,
    soundOn: true,
    volume: 30,
    track: tracks[0],
    alarmLight: true,
  }));
}
