# Автозапуск бота на Mac (без открытого Terminal)

Это временное решение — работает, пока Mac включён и вы вошли в свою
учётную запись. Если Mac выключен, спит или перезагружается — бот
остановится (в отличие от VPS с systemd, который живёт 24/7 даже когда
ваш компьютер выключен, см. `deploy/DEPLOY.md`).

Механизм называется `launchd` — это встроенный в macOS аналог systemd.

## Установка

```bash
cd /Users/user/Desktop/robo_companion_v2

# 1. Останавливаем бота, если он сейчас запущен вручную в Terminal (Ctrl+C)

# 2. Копируем конфигурацию автозапуска в системную папку
cp deploy/mac/com.robocompanion.bot.plist ~/Library/LaunchAgents/

# 3. Регистрируем и сразу запускаем
launchctl load ~/Library/LaunchAgents/com.robocompanion.bot.plist
```

Всё — бот запущен в фоне. Проверьте в Telegram командой `/today`.

С этого момента бот будет:
- автоматически запускаться при каждом входе в систему (перезагрузка Mac,
  выход из сна);
- автоматически перезапускаться, если процесс упадёт с ошибкой;
- работать без единого открытого окна Terminal.

## Проверка состояния

```bash
launchctl list | grep robocompanion
```

Если видите строку с `com.robocompanion.bot` — работает. Число перед
именем — код последнего завершения (0 — всё хорошо; если бот сейчас
работает, там будет `-`).

Логи:
```bash
tail -f /Users/user/Desktop/robo_companion_v2/bot.log        # логи самого бота
tail -f /Users/user/Desktop/robo_companion_v2/launchd.log    # сырой вывод процесса
```

## Остановка / отключение автозапуска

```bash
launchctl unload ~/Library/LaunchAgents/com.robocompanion.bot.plist
```

Это остановит бота и уберёт его из автозапуска. Чтобы включить автозапуск
снова — повторите команду `launchctl load` из раздела «Установка».

Если решите полностью удалить автозапуск:
```bash
launchctl unload ~/Library/LaunchAgents/com.robocompanion.bot.plist
rm ~/Library/LaunchAgents/com.robocompanion.bot.plist
```

## Важно: не запускайте бота двумя способами одновременно

Если бот уже работает через `launchd`, не запускайте его вручную ещё раз
командой `python bot.py` в Terminal — Telegram не разрешает двум
процессам одновременно использовать один токен, оба перестанут отвечать
корректно. Если нужно временно вручную — сначала `launchctl unload`.

## На будущее

Когда перенесёте бота на VPS (Zomro, инструкция в `deploy/DEPLOY.md`) —
этот автозапуск на Mac можно будет отключить (`launchctl unload`) и
выключать Mac спокойно: бот продолжит работать на сервере.
